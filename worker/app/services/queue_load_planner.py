from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from typing import Any

UTC = timezone.utc  # noqa: UP017


@dataclass(frozen=True)
class QueueLoadPlan:
    waiting_for_pod_jobs_count: int
    queued_jobs_count: int
    generating_jobs_count: int
    total_waiting_audio_seconds: Decimal
    total_waiting_audio_minutes: Decimal
    healthy_pods_count: int
    idle_healthy_pods_count: int
    busy_pods_count: int
    active_pods_count: int
    oldest_wait_minutes: int
    target_minutes_per_pod_min: Decimal
    target_minutes_per_pod_max: Decimal
    current_capacity_minutes_min: Decimal
    current_capacity_minutes_max: Decimal
    recommended_total_pods: int
    recommended_additional_pods: int
    max_active_pods: int
    alert_min_total_minutes: Decimal
    max_recommended_pods: int
    include_generating: bool
    planning_enabled: bool
    should_alert: bool
    alert_reason: str | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def calculate_queue_load_plan(
    *,
    waiting_for_pod_jobs_count: int,
    queued_jobs_count: int,
    generating_jobs_count: int,
    total_waiting_audio_seconds: Decimal | int | str,
    healthy_pods_count: int,
    idle_healthy_pods_count: int,
    busy_pods_count: int,
    active_pods_count: int,
    oldest_wait_minutes: int,
    target_minutes_per_pod_min: Decimal | int | str = Decimal("5"),
    target_minutes_per_pod_max: Decimal | int | str = Decimal("6"),
    alert_min_total_minutes: Decimal | int | str = Decimal("5"),
    max_recommended_pods: int = 5,
    max_active_pods: int = 1,
    min_waiting_jobs_for_count_alert: int = 2,
    target_wait_minutes_for_oldest_alert: int = 10,
    include_generating: bool = True,
    planning_enabled: bool = True,
) -> QueueLoadPlan:
    target_min = _positive_decimal(target_minutes_per_pod_min, Decimal("5"))
    target_max = _positive_decimal(target_minutes_per_pod_max, Decimal("6"))
    if target_min > target_max:
        target_min, target_max = target_max, target_min

    total_seconds = max(_decimal(total_waiting_audio_seconds), Decimal("0"))
    total_minutes = total_seconds / Decimal("60")
    healthy_pods = max(healthy_pods_count, 0)
    busy_pods = max(busy_pods_count, 0) if include_generating else 0
    active_pods = max(active_pods_count, 0)
    max_active = max(max_active_pods, 0)
    max_recommended = max(max_recommended_pods, 0)
    waiting_count = max(waiting_for_pod_jobs_count, 0)

    if planning_enabled and waiting_count > 0 and total_minutes > Decimal("0"):
        queue_pods_needed = int((total_minutes / target_max).to_integral_value(ROUND_CEILING))
    else:
        queue_pods_needed = 0

    recommended_total_pods = busy_pods + queue_pods_needed
    additional_uncapped = max(0, recommended_total_pods - healthy_pods)
    active_capacity_left = max(0, max_active - active_pods)
    recommended_additional_pods = min(
        additional_uncapped,
        active_capacity_left,
        max_recommended,
    )

    alert_min = _decimal(alert_min_total_minutes)
    oldest_wait = max(oldest_wait_minutes, 0)
    oldest_wait_exceeded = oldest_wait >= max(target_wait_minutes_for_oldest_alert, 1)
    count_pressure = waiting_count >= max(min_waiting_jobs_for_count_alert, 1)
    load_pressure = total_minutes > alert_min
    capacity_pressure = recommended_additional_pods > 0 and (load_pressure or count_pressure)

    alert_reason: str | None = None
    if planning_enabled and oldest_wait_exceeded and waiting_count > 0:
        alert_reason = "oldest_wait_exceeded"
    elif planning_enabled and capacity_pressure:
        alert_reason = "queue_load_exceeded"

    return QueueLoadPlan(
        waiting_for_pod_jobs_count=waiting_count,
        queued_jobs_count=max(queued_jobs_count, 0),
        generating_jobs_count=max(generating_jobs_count, 0),
        total_waiting_audio_seconds=total_seconds,
        total_waiting_audio_minutes=total_minutes,
        healthy_pods_count=healthy_pods,
        idle_healthy_pods_count=max(idle_healthy_pods_count, 0),
        busy_pods_count=max(busy_pods_count, 0),
        active_pods_count=active_pods,
        oldest_wait_minutes=oldest_wait,
        target_minutes_per_pod_min=target_min,
        target_minutes_per_pod_max=target_max,
        current_capacity_minutes_min=healthy_pods * target_min,
        current_capacity_minutes_max=healthy_pods * target_max,
        recommended_total_pods=recommended_total_pods,
        recommended_additional_pods=recommended_additional_pods,
        max_active_pods=max_active,
        alert_min_total_minutes=alert_min,
        max_recommended_pods=max_recommended,
        include_generating=include_generating,
        planning_enabled=planning_enabled,
        should_alert=bool(alert_reason),
        alert_reason=alert_reason,
    )


def calculate_queue_load_plan_for_session(
    session: Any,
    settings: Any | None = None,
) -> QueueLoadPlan:
    from shared.app.config import get_settings

    resolved_settings = settings or get_settings()
    snapshot = _queue_load_snapshot(session, resolved_settings)
    return calculate_queue_load_plan(**snapshot)


async def calculate_queue_load_plan_for_async_session(
    session: Any,
    settings: Any | None = None,
) -> QueueLoadPlan:
    from shared.app.config import get_settings

    resolved_settings = settings or get_settings()
    snapshot = await _async_queue_load_snapshot(session, resolved_settings)
    return calculate_queue_load_plan(**snapshot)


def _queue_load_snapshot(session: Any, settings: Any) -> dict[str, object]:
    from sqlalchemy import func, select

    from backend.app.models.generation_job import GenerationJob
    from backend.app.models.runpod_pod import RunpodPod
    from shared.app.enums import JobStatus

    waiting_rows = list(
        session.execute(
            select(
                GenerationJob.audio_duration_seconds,
                GenerationJob.waiting_for_pod_since,
                GenerationJob.updated_at,
                GenerationJob.created_at,
            ).where(GenerationJob.status == JobStatus.WAITING_FOR_POD.value)
        ).all()
    )
    queued_jobs_count = int(
        session.scalar(
            select(func.count(GenerationJob.id)).where(
                GenerationJob.status == JobStatus.QUEUED.value
            )
        )
        or 0
    )
    generating_jobs_count = int(
        session.scalar(
            select(func.count(GenerationJob.id)).where(
                GenerationJob.status == JobStatus.GENERATING.value
            )
        )
        or 0
    )
    pods = list(session.execute(select(RunpodPod)).scalars())
    return _build_snapshot_from_rows(
        settings=settings,
        waiting_rows=waiting_rows,
        queued_jobs_count=queued_jobs_count,
        generating_jobs_count=generating_jobs_count,
        pods=pods,
    )


async def _async_queue_load_snapshot(session: Any, settings: Any) -> dict[str, object]:
    from sqlalchemy import func, select

    from backend.app.models.generation_job import GenerationJob
    from backend.app.models.runpod_pod import RunpodPod
    from shared.app.enums import JobStatus

    waiting_result = await session.execute(
        select(
            GenerationJob.audio_duration_seconds,
            GenerationJob.waiting_for_pod_since,
            GenerationJob.updated_at,
            GenerationJob.created_at,
        ).where(GenerationJob.status == JobStatus.WAITING_FOR_POD.value)
    )
    queued_jobs_count = int(
        await session.scalar(
            select(func.count(GenerationJob.id)).where(
                GenerationJob.status == JobStatus.QUEUED.value
            )
        )
        or 0
    )
    generating_jobs_count = int(
        await session.scalar(
            select(func.count(GenerationJob.id)).where(
                GenerationJob.status == JobStatus.GENERATING.value
            )
        )
        or 0
    )
    pods_result = await session.execute(select(RunpodPod))
    return _build_snapshot_from_rows(
        settings=settings,
        waiting_rows=list(waiting_result.all()),
        queued_jobs_count=queued_jobs_count,
        generating_jobs_count=generating_jobs_count,
        pods=list(pods_result.scalars()),
    )


def _build_snapshot_from_rows(
    *,
    settings: Any,
    waiting_rows: list[Any],
    queued_jobs_count: int,
    generating_jobs_count: int,
    pods: list[Any],
) -> dict[str, object]:
    from shared.app.enums import PodStatus

    default_duration = max(_decimal(settings.runpod_default_job_duration_seconds), Decimal("0"))
    total_waiting_audio_seconds = Decimal("0")
    oldest_wait_minutes = 0
    now = datetime.now(UTC)
    for row in waiting_rows:
        duration, waiting_since, updated_at, created_at = row
        total_waiting_audio_seconds += (
            _decimal(duration) if duration is not None else default_duration
        )
        since = waiting_since or updated_at or created_at
        oldest_wait_minutes = max(oldest_wait_minutes, _elapsed_minutes(now, since))

    active_statuses = {
        PodStatus.CREATING.value,
        PodStatus.STARTING.value,
        PodStatus.READY.value,
        PodStatus.IDLE.value,
        PodStatus.BUSY.value,
    }
    healthy_statuses = {
        PodStatus.READY.value,
        PodStatus.IDLE.value,
        PodStatus.BUSY.value,
    }
    active_pods = [
        pod
        for pod in pods
        if pod.status in active_statuses and getattr(pod, "terminated_at", None) is None
    ]
    healthy_pods = [
        pod
        for pod in active_pods
        if pod.status in healthy_statuses
        and getattr(pod, "last_healthcheck_at", None) is not None
        and getattr(pod, "error_message", None) is None
    ]
    idle_healthy_pods = [
        pod
        for pod in healthy_pods
        if pod.status in {PodStatus.READY.value, PodStatus.IDLE.value}
        and getattr(pod, "active_job_id", None) is None
        and getattr(pod, "current_job_id", None) is None
    ]
    busy_pods = [
        pod
        for pod in active_pods
        if pod.status == PodStatus.BUSY.value
        or getattr(pod, "active_job_id", None) is not None
        or getattr(pod, "current_job_id", None) is not None
    ]
    return {
        "waiting_for_pod_jobs_count": len(waiting_rows),
        "queued_jobs_count": queued_jobs_count,
        "generating_jobs_count": generating_jobs_count,
        "total_waiting_audio_seconds": total_waiting_audio_seconds,
        "healthy_pods_count": len(healthy_pods),
        "idle_healthy_pods_count": len(idle_healthy_pods),
        "busy_pods_count": len(busy_pods),
        "active_pods_count": len(active_pods),
        "oldest_wait_minutes": oldest_wait_minutes,
        "target_minutes_per_pod_min": settings.runpod_target_queue_minutes_per_pod_min,
        "target_minutes_per_pod_max": settings.runpod_target_queue_minutes_per_pod_max,
        "alert_min_total_minutes": settings.runpod_queue_load_alert_min_total_minutes,
        "max_recommended_pods": settings.runpod_queue_load_max_recommended_pods,
        "max_active_pods": settings.runpod_max_active_pods,
        "min_waiting_jobs_for_count_alert": settings.admin_queue_alert_min_waiting_jobs,
        "target_wait_minutes_for_oldest_alert": settings.admin_queue_alert_target_wait_minutes,
        "include_generating": settings.runpod_queue_load_include_generating,
        "planning_enabled": settings.runpod_queue_load_planning_enabled,
    }


def _decimal(value: Decimal | int | str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _positive_decimal(value: Decimal | int | str, fallback: Decimal) -> Decimal:
    decimal_value = _decimal(value)
    if decimal_value <= Decimal("0"):
        return fallback
    return decimal_value


def _elapsed_minutes(now: datetime, since: datetime | None) -> int:
    if since is None:
        return 0
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    return max(0, int((now - since).total_seconds() // 60))
