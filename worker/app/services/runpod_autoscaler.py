from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import httpx
from sqlalchemy import func, select

from backend.app.models.generation_job import GenerationJob
from backend.app.models.runpod_pod import RunpodPod
from shared.app.config import Settings, get_settings
from shared.app.enums import JobStatus, PodStatus
from worker.app.database import get_worker_session

logger = logging.getLogger(__name__)

ACTIVE_POD_STATUSES = {
    PodStatus.CREATING.value,
    PodStatus.STARTING.value,
    PodStatus.READY.value,
    PodStatus.IDLE.value,
    PodStatus.BUSY.value,
}
PENDING_JOB_STATUSES = {
    JobStatus.QUEUED.value,
    JobStatus.WAITING_FOR_GPU.value,
    JobStatus.WAITING_FOR_POD.value,
}


@dataclass(frozen=True, slots=True)
class RunPodAutoscalingDecision:
    enabled: bool
    strategy: str
    pending_jobs: int
    pending_gpu_minutes: Decimal
    target_queue_wait_minutes: int
    active_pods: int
    active_capacity_pods: int
    assignable_pods: int
    starting_pods: int
    creating_pods: int
    busy_pods: int
    idle_pods: int
    estimated_cold_start_seconds: int
    max_active_pods: int
    min_warm_pods: int
    estimated_pod_hourly_cost_usd: Decimal
    max_estimated_hourly_cost_usd: Decimal
    desired_active_pods: int
    pods_to_create: int
    pods_to_terminate: int
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "strategy": self.strategy,
            "pending_jobs": self.pending_jobs,
            "pending_gpu_minutes": str(self.pending_gpu_minutes),
            "target_queue_wait_minutes": self.target_queue_wait_minutes,
            "active_pods": self.active_pods,
            "active_capacity_pods": self.active_capacity_pods,
            "assignable_pods": self.assignable_pods,
            "starting_pods": self.starting_pods,
            "creating_pods": self.creating_pods,
            "busy_pods": self.busy_pods,
            "idle_pods": self.idle_pods,
            "estimated_cold_start_seconds": self.estimated_cold_start_seconds,
            "max_active_pods": self.max_active_pods,
            "min_warm_pods": self.min_warm_pods,
            "estimated_pod_hourly_cost_usd": str(self.estimated_pod_hourly_cost_usd),
            "max_estimated_hourly_cost_usd": str(self.max_estimated_hourly_cost_usd),
            "desired_active_pods": self.desired_active_pods,
            "pods_to_create": self.pods_to_create,
            "pods_to_terminate": self.pods_to_terminate,
            "reason": self.reason,
        }


class RunPodAutoscaler:
    """Read-only autoscaling policy for managed RunPod pods."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def calculate_decision(self) -> RunPodAutoscalingDecision:
        pending_jobs = self._pending_jobs()
        pending_jobs_count = len(pending_jobs)
        pending_gpu_minutes = self._estimate_pending_gpu_minutes(pending_jobs)
        active_pods = self._count_active_pods()
        assignable_pods = self._count_assignable_pods()
        starting_pods = self._count_pods({PodStatus.STARTING.value})
        creating_pods = self._count_pods({PodStatus.CREATING.value})
        busy_pods = self._count_busy_pods()
        idle_pods = self._count_idle_pods()
        max_active_pods = max(self._settings.runpod_max_active_pods, 1)
        min_warm_pods = max(self._settings.runpod_min_warm_pods, 0)
        strategy = self._settings.runpod_autoscaling_strategy.strip().lower()

        if not self._settings.runpod_autoscaling_enabled or strategy != "queue_time":
            return self._stage83_decision(
                strategy=strategy or "queue_time",
                pending_jobs_count=pending_jobs_count,
                pending_gpu_minutes=pending_gpu_minutes,
                active_pods=active_pods,
                assignable_pods=assignable_pods,
                starting_pods=starting_pods,
                creating_pods=creating_pods,
                busy_pods=busy_pods,
                idle_pods=idle_pods,
                max_active_pods=max_active_pods,
                min_warm_pods=min_warm_pods,
            )

        target_wait = max(self._settings.runpod_target_queue_wait_minutes, 1)
        desired_by_queue_time = _ceil_div_decimal(
            pending_gpu_minutes,
            Decimal(target_wait),
        )
        desired = max(min_warm_pods, desired_by_queue_time)
        reason_parts = [f"queue_time desired={desired_by_queue_time}"]

        if desired > max_active_pods:
            reason_parts.append(f"capped_by_max_active={max_active_pods}")
        desired = min(desired, max_active_pods)

        max_cost_pods = self._max_cost_pods(max_active_pods)
        if desired > max_cost_pods:
            logger.info("RunPod autoscaler cost cap applied max_cost_pods=%s", max_cost_pods)
            reason_parts.append(f"capped_by_cost={max_cost_pods}")
        desired = min(desired, max_cost_pods)

        if desired < busy_pods:
            reason_parts.append(f"raised_to_busy_pods={busy_pods}")
        desired = max(desired, busy_pods)

        raw_pods_to_create = min(
            max(desired - active_pods, 0),
            max(max_active_pods - active_pods, 0),
            max(self._settings.runpod_max_warm_pods_to_create_per_tick, 0),
        )
        pods_to_create = raw_pods_to_create
        if pods_to_create > 0 and self._scale_up_cooldown_active():
            logger.info("RunPod autoscaler cooldown active, skip scale up")
            reason_parts.append("scale_up_cooldown_active")
            pods_to_create = 0
        if pods_to_create > 0 and not self._settings.runpod_auto_create_enabled:
            logger.info("RunPod auto-create disabled; waiting for manual/discovered pod")
            reason_parts.append("auto_create_disabled")
            pods_to_create = 0

        pods_to_terminate = min(
            max(active_pods - desired, 0),
            self._eligible_idle_termination_count(),
        )
        if pods_to_terminate > 0:
            logger.info("RunPod autoscaler scale down pods_to_terminate=%s", pods_to_terminate)

        logger.info(
            "RunPod autoscaler decision pending_jobs=%s pending_gpu_minutes=%s "
            "desired_active_pods=%s active_pods=%s",
            pending_jobs_count,
            pending_gpu_minutes,
            desired,
            active_pods,
        )
        if pods_to_create > 0:
            logger.info("RunPod autoscaler scale up pods_to_create=%s", pods_to_create)

        return RunPodAutoscalingDecision(
            enabled=True,
            strategy=strategy,
            pending_jobs=pending_jobs_count,
            pending_gpu_minutes=pending_gpu_minutes,
            target_queue_wait_minutes=target_wait,
            active_pods=active_pods,
            active_capacity_pods=active_pods,
            assignable_pods=assignable_pods,
            starting_pods=starting_pods,
            creating_pods=creating_pods,
            busy_pods=busy_pods,
            idle_pods=idle_pods,
            estimated_cold_start_seconds=max(self._settings.runpod_estimated_cold_start_seconds, 0),
            max_active_pods=max_active_pods,
            min_warm_pods=min_warm_pods,
            estimated_pod_hourly_cost_usd=self._estimated_pod_hourly_cost(),
            max_estimated_hourly_cost_usd=self._max_hourly_cost(),
            desired_active_pods=desired,
            pods_to_create=pods_to_create,
            pods_to_terminate=pods_to_terminate,
            reason=" ".join(reason_parts),
        )

    def _stage83_decision(
        self,
        *,
        strategy: str,
        pending_jobs_count: int,
        pending_gpu_minutes: Decimal,
        active_pods: int,
        assignable_pods: int,
        starting_pods: int,
        creating_pods: int,
        busy_pods: int,
        idle_pods: int,
        max_active_pods: int,
        min_warm_pods: int,
    ) -> RunPodAutoscalingDecision:
        desired = max(min_warm_pods, min(max_active_pods, pending_jobs_count))
        desired = max(desired, busy_pods)
        pods_to_create = min(max(desired - active_pods, 0), max(max_active_pods - active_pods, 0))
        if pods_to_create > 0 and not self._settings.runpod_auto_create_enabled:
            logger.info("RunPod auto-create disabled; waiting for manual/discovered pod")
            pods_to_create = 0
        pods_to_terminate = min(
            max(active_pods - desired, 0),
            self._eligible_idle_termination_count(),
        )
        enabled = self._settings.runpod_autoscaling_enabled and strategy == "queue_time"
        reason = (
            "autoscaling_disabled_stage83"
            if not self._settings.runpod_autoscaling_enabled
            else f"unknown_strategy_{strategy}_stage83"
        )
        if not self._settings.runpod_auto_create_enabled:
            reason = f"{reason} auto_create_disabled"
        return RunPodAutoscalingDecision(
            enabled=enabled,
            strategy=strategy,
            pending_jobs=pending_jobs_count,
            pending_gpu_minutes=pending_gpu_minutes,
            target_queue_wait_minutes=max(self._settings.runpod_target_queue_wait_minutes, 1),
            active_pods=active_pods,
            active_capacity_pods=active_pods,
            assignable_pods=assignable_pods,
            starting_pods=starting_pods,
            creating_pods=creating_pods,
            busy_pods=busy_pods,
            idle_pods=idle_pods,
            estimated_cold_start_seconds=max(self._settings.runpod_estimated_cold_start_seconds, 0),
            max_active_pods=max_active_pods,
            min_warm_pods=min_warm_pods,
            estimated_pod_hourly_cost_usd=self._estimated_pod_hourly_cost(),
            max_estimated_hourly_cost_usd=self._max_hourly_cost(),
            desired_active_pods=desired,
            pods_to_create=pods_to_create,
            pods_to_terminate=pods_to_terminate,
            reason=reason,
        )

    def _estimate_pending_gpu_minutes(self, jobs: list[GenerationJob]) -> Decimal:
        total = Decimal("0")
        default_seconds = Decimal(max(self._settings.runpod_default_job_duration_seconds, 1))
        speed_factor = self._settings.runpod_estimated_generation_speed_factor
        for job in jobs:
            duration_seconds = Decimal(job.audio_duration_seconds or default_seconds)
            duration_minutes = duration_seconds / Decimal("60")
            total += duration_minutes * speed_factor

        max_gpu_minutes = self._settings.runpod_max_estimated_gpu_minutes_per_tick
        if total > max_gpu_minutes:
            return max_gpu_minutes
        return total.quantize(Decimal("0.001"))

    def _pending_jobs(self) -> list[GenerationJob]:
        with get_worker_session() as session:
            jobs = list(
                session.execute(
                    select(GenerationJob)
                    .where(GenerationJob.status.in_(PENDING_JOB_STATUSES))
                    .order_by(GenerationJob.created_at.asc())
                ).scalars()
            )
            session.commit()
            return jobs

    def _count_active_pods(self) -> int:
        return self._count_pods(ACTIVE_POD_STATUSES)

    def _count_busy_pods(self) -> int:
        return self._count_pods({PodStatus.BUSY.value})

    def _count_idle_pods(self) -> int:
        with get_worker_session() as session:
            count = session.scalar(
                select(func.count())
                .select_from(RunpodPod)
                .where(
                    RunpodPod.status.in_([PodStatus.IDLE.value, PodStatus.READY.value]),
                    RunpodPod.active_job_id.is_(None),
                    RunpodPod.current_job_id.is_(None),
                    RunpodPod.runpod_pod_id.is_not(None),
                    RunpodPod.terminated_at.is_(None),
                )
            )
            session.commit()
            return int(count or 0)

    def _count_assignable_pods(self) -> int:
        with get_worker_session() as session:
            result = session.execute(
                select(RunpodPod).where(
                    RunpodPod.status.in_([PodStatus.IDLE.value, PodStatus.READY.value]),
                    RunpodPod.active_job_id.is_(None),
                    RunpodPod.current_job_id.is_(None),
                    RunpodPod.base_url.is_not(None),
                    RunpodPod.runpod_pod_id.is_not(None),
                    RunpodPod.terminated_at.is_(None),
                )
            )
            pods = list(result.scalars())
            session.commit()

        count = 0
        for pod in pods:
            if pod.base_url and _healthcheck(pod.base_url):
                count += 1
        return count

    def _count_pods(self, statuses: set[str]) -> int:
        with get_worker_session() as session:
            count = session.scalar(
                select(func.count())
                .select_from(RunpodPod)
                .where(
                    RunpodPod.status.in_(statuses),
                    RunpodPod.runpod_pod_id.is_not(None),
                    RunpodPod.terminated_at.is_(None),
                )
            )
            session.commit()
            return int(count or 0)

    def _eligible_idle_termination_count(self) -> int:
        cutoff = self._idle_termination_cutoff()
        with get_worker_session() as session:
            result = session.execute(
                select(RunpodPod).where(
                    RunpodPod.status == PodStatus.IDLE.value,
                    RunpodPod.active_job_id.is_(None),
                    RunpodPod.current_job_id.is_(None),
                    RunpodPod.runpod_pod_id.is_not(None),
                    RunpodPod.terminated_at.is_(None),
                )
            )
            count = 0
            for pod in result.scalars():
                idle_since = (
                    pod.last_used_at or pod.last_busy_at or pod.updated_at or pod.created_at
                )
                if idle_since < cutoff:
                    count += 1
            session.commit()
            return count

    def _scale_up_cooldown_active(self) -> bool:
        cooldown = max(self._settings.runpod_scale_up_cooldown_seconds, 0)
        if cooldown <= 0:
            return False
        cutoff = datetime.now(UTC) - timedelta(seconds=cooldown)
        with get_worker_session() as session:
            latest_created_at = session.scalar(
                select(func.max(RunpodPod.created_at)).where(
                    RunpodPod.status.in_(ACTIVE_POD_STATUSES),
                    RunpodPod.runpod_pod_id.is_not(None),
                    RunpodPod.terminated_at.is_(None),
                )
            )
            session.commit()
            return latest_created_at is not None and latest_created_at >= cutoff

    def _idle_termination_cutoff(self) -> datetime:
        idle_seconds = max(self._settings.runpod_pod_idle_shutdown_minutes, 0) * 60
        cooldown_seconds = (
            max(self._settings.runpod_scale_down_cooldown_seconds, 0)
            if self._settings.runpod_autoscaling_enabled
            else 0
        )
        return datetime.now(UTC) - timedelta(seconds=max(idle_seconds, cooldown_seconds))

    def _max_cost_pods(self, max_active_pods: int) -> int:
        estimated_cost = self._estimated_pod_hourly_cost()
        max_cost = self._max_hourly_cost()
        if estimated_cost <= Decimal("0") or max_cost < Decimal("0"):
            return max_active_pods
        return max(0, int((max_cost / estimated_cost).to_integral_value(rounding=ROUND_FLOOR)))

    def _estimated_pod_hourly_cost(self) -> Decimal:
        return self._settings.runpod_estimated_pod_hourly_cost_usd.quantize(Decimal("0.01"))

    def _max_hourly_cost(self) -> Decimal:
        return self._settings.runpod_max_estimated_hourly_gpu_cost_usd.quantize(Decimal("0.01"))


def _ceil_div_decimal(numerator: Decimal, denominator: Decimal) -> int:
    if numerator <= Decimal("0"):
        return 0
    return int((numerator / denominator).to_integral_value(rounding=ROUND_CEILING))


def _healthcheck(base_url: str) -> bool:
    try:
        with httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(5.0, connect=2.0),
            follow_redirects=True,
        ) as client:
            response = client.get("/system_stats")
            response.raise_for_status()
        return True
    except Exception:
        return False
