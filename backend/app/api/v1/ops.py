from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.redis import ping_redis
from backend.app.models.generation_job import GenerationJob
from backend.app.models.runpod_pod import RunpodPod
from backend.app.schemas.ops import OpsDependencyStatus, OpsStatusResponse
from shared.app.config import get_settings
from shared.app.database import get_session
from shared.app.enums import JobStatus

router = APIRouter(prefix="/ops", tags=["ops"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/status", response_model=OpsStatusResponse)
async def get_ops_status(session: SessionDep, response: Response) -> OpsStatusResponse:
    settings = get_settings()
    database = OpsDependencyStatus(status="ok")
    redis = OpsDependencyStatus(status="ok")
    jobs: dict[str, int] = {}
    runpod_pods: dict[str, int] = {}

    try:
        jobs = await _count_jobs(session)
        runpod_pods = await _count_runpod_pods(session)
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
