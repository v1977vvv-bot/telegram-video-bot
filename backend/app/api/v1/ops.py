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
        runpod_config=_runpod_config_ops_status(settings),
        runpod_costs=_runpod_cost_ops_status(settings),
        comfyui=_comfyui_ops_status(settings),
        payments=_payment_ops_status(settings),
        business=business,
        admin={
            "admin_panel_enabled": settings.admin_panel_enabled,
            "admin_actions_enabled": settings.admin_actions_enabled,
            "admin_internal_api_token_configured": settings.admin_internal_api_token_configured,
            "admin_bot_token_configured": settings.admin_bot_token_is_configured,
            "admin_alerts_enabled": settings.admin_alerts_enabled,
        },
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
        "payment_provider": settings.payment_provider_normalized,
        "packages_enabled": settings.payment_packages_enabled,
        "packages_usd": package_amounts,
        "custom_amount_enabled": settings.payment_custom_amount_enabled,
        "display_currency": settings.payment_display_currency,
        "provider_currency": settings.payment_provider_currency,
        "cryptomus_enabled": settings.cryptomus_enabled,
        "cryptobot_enabled": settings.cryptobot_pay_enabled,
    }


def _comfyui_ops_status(settings) -> dict[str, object]:
    try:
        model_profile = settings.comfyui_model_profile_normalized
    except ValueError:
        model_profile = settings.comfyui_model_profile
    return {
        "model_profile": model_profile,
        "allowed_model_profiles": list(settings.comfyui_allowed_model_profiles),
        "workflow_path": settings.comfyui_workflow_path,
        "timeout_seconds": settings.comfyui_timeout_seconds,
    }


def _runpod_cost_ops_status(settings) -> dict[str, object]:
    try:
        known_gpu_cost_count = len(RunPodCostService(settings).parse_gpu_hourly_costs())
    except Exception:
        known_gpu_cost_count = 0
    secure_gpu_price = settings.runpod_secure_gpu_price_per_hour
    community_gpu_price = settings.runpod_community_gpu_price_per_hour
    secure_storage_price = settings.runpod_secure_storage_price_per_gb_month
    community_storage_price = settings.runpod_community_storage_price_per_gb_month
    billing_margin = settings.runpod_billing_margin_percent_value
    return {
        "tracking_enabled": settings.runpod_cost_tracking_enabled,
        "default_hourly_cost_usd": str(settings.runpod_default_hourly_cost_usd),
        "known_gpu_cost_count": known_gpu_cost_count,
        "include_cold_start": settings.runpod_cost_include_cold_start,
        "include_idle_time": settings.runpod_cost_include_idle_time,
        "cloud_specific_pricing_configured": settings.runpod_cloud_specific_pricing_configured,
        "secure_gpu_price_per_hour_usd": (
            str(secure_gpu_price) if secure_gpu_price is not None else None
        ),
        "community_gpu_price_per_hour_usd": (
            str(community_gpu_price) if community_gpu_price is not None else None
        ),
        "secure_startup_surcharge_usd": str(settings.runpod_secure_startup_surcharge),
        "community_cold_start_surcharge_usd": str(settings.runpod_community_cold_start_surcharge),
        "secure_storage_price_per_gb_month_usd": (
            str(secure_storage_price) if secure_storage_price is not None else None
        ),
        "community_storage_price_per_gb_month_usd": (
            str(community_storage_price) if community_storage_price is not None else None
        ),
        "billing_margin_percent": str(billing_margin) if billing_margin is not None else None,
    }


def _runpod_config_ops_status(settings) -> dict[str, object]:
    return {
        "primary_cloud_type": settings.runpod_primary_cloud_type,
        "fallback_cloud_type": settings.runpod_fallback_cloud_type,
        "allowed_gpu_types": settings.runpod_allowed_gpu_type_list,
        "fallback_allowed_gpu_types": settings.runpod_fallback_allowed_gpu_type_list,
        "primary_create_max_attempts": settings.runpod_primary_create_max_attempts,
        "fallback_create_max_attempts": settings.runpod_fallback_create_max_attempts,
        "pod_ready_timeout_seconds": settings.runpod_pod_ready_timeout_seconds,
        "healthcheck_interval_seconds": settings.runpod_healthcheck_interval_seconds,
        "container_disk_gb": settings.runpod_container_disk_gb,
        "volume_disk_gb": settings.runpod_volume_disk_gb,
        "min_vcpu_count": settings.runpod_min_vcpu,
        "min_memory_gb": settings.runpod_min_ram_gb,
        "fallback_min_memory_gb": settings.runpod_fallback_min_ram_gb,
        "ports": settings.runpod_ports,
        "fallback_ports": settings.runpod_fallback_ports or None,
        "allowed_cuda_versions": [
            item.strip()
            for item in settings.runpod_allowed_cuda_versions.split(",")
            if item.strip()
        ],
        "fallback_allowed_cuda_versions": [
            item.strip()
            for item in settings.runpod_fallback_allowed_cuda_versions.split(",")
            if item.strip()
        ],
        "min_download": settings.runpod_min_download,
        "min_upload": settings.runpod_min_upload,
        "fallback_min_download": settings.runpod_fallback_min_download or None,
        "fallback_min_upload": settings.runpod_fallback_min_upload or None,
        "support_public_ip": settings.runpod_support_public_ip,
        "fallback_support_public_ip": settings.runpod_fallback_support_public_ip or None,
        "start_jupyter": settings.runpod_start_jupyter,
        "fallback_start_jupyter": settings.runpod_fallback_start_jupyter or None,
        "start_ssh": settings.runpod_start_ssh,
        "fallback_start_ssh": settings.runpod_fallback_start_ssh or None,
        "global_network": settings.runpod_global_network,
        "fallback_global_network": settings.runpod_fallback_global_network or None,
        "experimental_low_vram_startup": settings.runpod_experimental_low_vram_startup,
        "discovery_enabled": settings.runpod_discovery_enabled,
        "discovery_interval_seconds": settings.runpod_discovery_interval_seconds,
        "discovery_auto_register": settings.runpod_discovery_auto_register,
        "discovery_require_healthy": settings.runpod_discovery_require_healthy,
        "queue_load_planning_enabled": settings.runpod_queue_load_planning_enabled,
        "target_queue_minutes_per_pod_min": settings.runpod_target_queue_minutes_per_pod_min,
        "target_queue_minutes_per_pod_max": settings.runpod_target_queue_minutes_per_pod_max,
        "queue_load_alert_min_total_minutes": (settings.runpod_queue_load_alert_min_total_minutes),
        "queue_load_max_recommended_pods": settings.runpod_queue_load_max_recommended_pods,
        "queue_load_include_generating": settings.runpod_queue_load_include_generating,
    }
