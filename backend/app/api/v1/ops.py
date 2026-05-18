from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.redis import ping_redis
from backend.app.models.business_account import BusinessAccount
from backend.app.models.business_account_member import BusinessAccountMember
from backend.app.models.generation_job import GenerationJob
from backend.app.models.runpod_pod import RunpodPod
from backend.app.schemas.ops import OpsDependencyStatus, OpsStatusResponse
from backend.app.services.payment_packages import PaymentPackageService
from shared.app.config import get_settings
from shared.app.database import get_session
from shared.app.enums import BusinessAccountStatus, JobStatus
from worker.app.services.runpod_costs import RunPodCostService

router = APIRouter(prefix="/ops", tags=["ops"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/status", response_model=OpsStatusResponse)
async def get_ops_status(session: SessionDep, response: Response) -> OpsStatusResponse:
    settings = get_settings()
    database = OpsDependencyStatus(status="ok")
    redis = OpsDependencyStatus(status="ok")
    jobs: dict[str, int] = {}
    runpod_pods: dict[str, int] = {}
    business: dict[str, int] = {}

    try:
        jobs = await _count_jobs(session)
        runpod_pods = await _count_runpod_pods(session)
        business = await _count_business(session)
    except Exception as exc:
        database = OpsDependencyStatus(status="error", error=exc.__class__.__name__)

    try:
        await ping_redis()
    except Exception as exc:
        redis = OpsDependencyStatus(status="error", error=exc.__class__.__name__)

    status = "ok" if database.status == "ok" and redis.status == "ok" else "degraded"
    if status == "degraded":
        response.status_code = 503

    return OpsStatusResponse(
        status=status,
        service="backend",
        app_env=settings.app_env,
        version=os.getenv("APP_VERSION", "0.1.0"),
        commit=os.getenv("GIT_COMMIT", "unknown"),
        database=database,
        redis=redis,
        worker_queue="not_checked",
        jobs=jobs,
        runpod_pods=runpod_pods,
        runpod_costs=_runpod_cost_ops_status(settings),
        payments=_payment_ops_status(settings),
        business=business,
    )


async def _count_jobs(session: AsyncSession) -> dict[str, int]:
    statuses = [
        JobStatus.QUEUED.value,
        JobStatus.WAITING_FOR_GPU.value,
        JobStatus.WAITING_FOR_POD.value,
        JobStatus.GENERATING.value,
        JobStatus.STITCHING.value,
        JobStatus.UPLOADING_RESULT.value,
    ]
    result = await session.execute(
        select(GenerationJob.status, func.count())
        .where(GenerationJob.status.in_(statuses))
        .group_by(GenerationJob.status)
    )
    counts = {status: 0 for status in statuses}
    counts.update({str(status): int(count) for status, count in result.all()})
    counts["active"] = sum(counts.values())
    return counts


async def _count_runpod_pods(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(
        select(RunpodPod.status, func.count()).group_by(RunpodPod.status)
    )
    counts = {str(status): int(count) for status, count in result.all()}
    counts["active"] = sum(
        count
        for status, count in counts.items()
        if status in {"creating", "starting", "ready", "idle", "busy"}
    )
    return counts


async def _count_business(session: AsyncSession) -> dict[str, int]:
    active_accounts = await session.scalar(
        select(func.count(BusinessAccount.id)).where(
            BusinessAccount.status == BusinessAccountStatus.ACTIVE.value
        )
    )
    active_members = await session.scalar(
        select(func.count(BusinessAccountMember.id)).where(
            BusinessAccountMember.is_active.is_(True)
        )
    )
    return {
        "business_accounts_active": int(active_accounts or 0),
        "business_members_active": int(active_members or 0),
    }


def _payment_ops_status(settings) -> dict[str, object]:
    try:
        packages = PaymentPackageService(settings).get_payment_packages()
        package_amounts = [str(package.amount_usd) for package in packages]
    except Exception:
        package_amounts = []
    return {
        "packages_enabled": settings.payment_packages_enabled,
        "packages_usd": package_amounts,
        "custom_amount_enabled": settings.payment_custom_amount_enabled,
        "display_currency": settings.payment_display_currency,
        "provider_currency": settings.payment_provider_currency,
    }


def _runpod_cost_ops_status(settings) -> dict[str, object]:
    try:
        known_gpu_cost_count = len(RunPodCostService(settings).parse_gpu_hourly_costs())
    except Exception:
        known_gpu_cost_count = 0
    return {
        "tracking_enabled": settings.runpod_cost_tracking_enabled,
        "default_hourly_cost_usd": str(settings.runpod_default_hourly_cost_usd),
        "known_gpu_cost_count": known_gpu_cost_count,
        "include_cold_start": settings.runpod_cost_include_cold_start,
        "include_idle_time": settings.runpod_cost_include_idle_time,
    }
