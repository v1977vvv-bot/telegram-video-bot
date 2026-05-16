from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select

from backend.app.models.generation_job import GenerationJob
from backend.app.models.runpod_pod import RunpodPod
from shared.app.config import Settings, get_settings
from shared.app.enums import JobStatus, PodStatus
from worker.app.database import get_worker_session
from worker.app.services.runpod import NoGpuAvailableError, RunPodError
from worker.app.services.runpod_manager import RunPodManager

logger = logging.getLogger(__name__)

ACTIVE_POD_STATUSES = {
    PodStatus.CREATING.value,
    PodStatus.STARTING.value,
    PodStatus.READY.value,
    PodStatus.IDLE.value,
    PodStatus.BUSY.value,
}
HEALTHCHECK_POD_STATUSES = {
    PodStatus.CREATING.value,
    PodStatus.STARTING.value,
    PodStatus.READY.value,
    PodStatus.IDLE.value,
}


@dataclass(frozen=True, slots=True)
class RunPodKeeperResult:
    enabled: bool
    active_pods: int
    busy_pods: int = 0
    idle_pods: int = 0
    pending_jobs: int = 0
    desired_active_pods: int = 0
    terminated_idle_pods: list[str] = field(default_factory=list)
    created_warm_pod: str | None = None
    created_warm_pods: list[str] = field(default_factory=list)
    requeued_waiting_jobs: int | None = None
    should_enqueue_waiting_retry: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "active_pods": self.active_pods,
            "busy_pods": self.busy_pods,
            "idle_pods": self.idle_pods,
            "pending_jobs": self.pending_jobs,
            "desired_active_pods": self.desired_active_pods,
            "terminated_idle_pods": self.terminated_idle_pods,
            "created_warm_pod": self.created_warm_pod,
            "created_warm_pods": self.created_warm_pods,
            "requeued_waiting_jobs": self.requeued_waiting_jobs,
        }


@dataclass(frozen=True, slots=True)
class _PodSnapshot:
    id: UUID
    runpod_pod_id: str
    status: str
    base_url: str | None
    last_used_at: datetime | None
    last_busy_at: datetime | None
    updated_at: datetime
    created_at: datetime


class RunPodKeeper:
    """Maintain warm RunPod capacity and shut down idle managed pods."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        runpod_manager: RunPodManager | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._manager = runpod_manager or RunPodManager(self._settings)

    def tick(self) -> RunPodKeeperResult:
        active_count = self._count_active_pods()
        busy_count = self._count_busy_pods()
        idle_count = self._count_idle_pods()
        pending_jobs_count = self._count_pending_jobs()
        desired_active_pods = self._desired_active_pods(pending_jobs_count)
        if not self._settings.runpod_keeper_enabled:
            logger.info("RunPod keeper disabled")
            return RunPodKeeperResult(
                enabled=False,
                active_pods=active_count,
                busy_pods=busy_count,
                idle_pods=idle_count,
                pending_jobs=pending_jobs_count,
                desired_active_pods=desired_active_pods,
                terminated_idle_pods=[],
                created_warm_pod=None,
                created_warm_pods=[],
            )

        if not self._settings.runpod_auto_manager_enabled:
            logger.info("RunPod keeper skipped because RunPod auto-manager is not configured")
            return RunPodKeeperResult(
                enabled=True,
                active_pods=active_count,
                busy_pods=busy_count,
                idle_pods=idle_count,
                pending_jobs=pending_jobs_count,
                desired_active_pods=desired_active_pods,
                terminated_idle_pods=[],
                created_warm_pod=None,
                created_warm_pods=[],
            )

        self._refresh_healthy_idle_pods()
        terminated = self._terminate_expired_idle_pods()
        active_count = self._count_active_pods()
        pending_jobs_count = self._count_pending_jobs()
        desired_active_pods = self._desired_active_pods(pending_jobs_count)

        created_warm_pods: list[str] = []
        while self._should_create_warm_pod(active_count, desired_active_pods):
            try:
                created_warm_pods.append(self._create_warm_pod())
            except NoGpuAvailableError as exc:
                logger.warning("RunPod keeper warm pod capacity unavailable error=%s", exc)
                break
            except RunPodError as exc:
                logger.warning("RunPod keeper warm pod creation failed error=%s", exc)
                break
            active_count = self._count_active_pods()

        busy_count = self._count_busy_pods()
        idle_count = self._count_idle_pods()
        should_enqueue_waiting_retry = self._has_waiting_jobs() and idle_count > 0
        return RunPodKeeperResult(
            enabled=True,
            active_pods=active_count,
            busy_pods=busy_count,
            idle_pods=idle_count,
            pending_jobs=pending_jobs_count,
            desired_active_pods=desired_active_pods,
            terminated_idle_pods=terminated,
            created_warm_pod=created_warm_pods[0] if created_warm_pods else None,
            created_warm_pods=created_warm_pods,
            should_enqueue_waiting_retry=should_enqueue_waiting_retry,
        )

    def _refresh_healthy_idle_pods(self) -> None:
        for pod in self._healthcheck_candidates():
            logger.info(
                "RunPod keeper healthcheck pod_id=%s status=%s",
                pod.runpod_pod_id,
                pod.status,
            )
            if pod.base_url is None:
                continue
            if self._manager._healthcheck(pod.base_url):  # noqa: SLF001
                self._mark_healthcheck_ok(pod)

    def _mark_healthcheck_ok(self, pod: _PodSnapshot) -> None:
        with get_worker_session() as session:
            with session.begin():
                refreshed = session.get(RunpodPod, pod.id, with_for_update=True)
                if refreshed is None:
                    return
                if refreshed.active_job_id is not None or refreshed.current_job_id is not None:
                    return

                previous_status = refreshed.status
                now = datetime.now(UTC)
                refreshed.last_healthcheck_at = now
                refreshed.error_message = None
                if previous_status in {
                    PodStatus.CREATING.value,
                    PodStatus.STARTING.value,
                    PodStatus.READY.value,
                }:
                    refreshed.status = PodStatus.IDLE.value
                    refreshed.last_used_at = now
                    logger.info(
                        "RunPod keeper marked healthy pod idle pod_id=%s previous_status=%s",
                        refreshed.runpod_pod_id,
                        previous_status,
                    )

    def _terminate_expired_idle_pods(self) -> list[str]:
        cutoff = datetime.now(UTC) - timedelta(
            minutes=self._settings.runpod_pod_idle_shutdown_minutes
        )
        terminated: list[str] = []
        for pod in self._idle_shutdown_candidates(cutoff):
            pod_id = self._mark_pod_stopping_if_idle(pod.id)
            if pod_id is None:
                continue

            try:
                self._manager._client.terminate_pod(pod_id)  # noqa: SLF001
            except RunPodError as exc:
                logger.warning(
                    "RunPod keeper idle termination failed pod_id=%s error=%s",
                    pod_id,
                    exc,
                )
                self._restore_idle_after_termination_failure(pod.id, str(exc))
                continue

            self._mark_pod_terminated(pod.id)
            terminated.append(pod_id)
            logger.info("RunPod keeper terminated idle pod pod_id=%s", pod_id)
        return terminated

    def _should_create_warm_pod(self, active_count: int, desired_active_pods: int) -> bool:
        if not self._settings.runpod_warm_pod_enabled:
            return False
        if desired_active_pods <= 0:
            return False
        return active_count < desired_active_pods

    def _create_warm_pod(self) -> str:
        logger.info("RunPod keeper creating warm pod")
        with get_worker_session() as session:
            pod = self._manager._create_and_wait_for_pod(session, job_id=None)  # noqa: SLF001
            with session.begin():
                refreshed = session.get(RunpodPod, pod.id, with_for_update=True)
                if refreshed is None:
                    raise RunPodError(
                        f"RunPod warm pod record disappeared pod_id={pod.runpod_pod_id}"
                    )
                if refreshed.active_job_id is None and refreshed.current_job_id is None:
                    now = datetime.now(UTC)
                    refreshed.status = PodStatus.IDLE.value
                    refreshed.last_healthcheck_at = now
                    refreshed.last_used_at = now
                    refreshed.last_busy_at = now
                    refreshed.error_message = None
                logger.info("RunPod keeper warm pod ready pod_id=%s", refreshed.runpod_pod_id)
                return refreshed.runpod_pod_id

    def _healthcheck_candidates(self) -> list[_PodSnapshot]:
        with get_worker_session() as session:
            result = session.execute(
                select(RunpodPod).where(
                    RunpodPod.status.in_(HEALTHCHECK_POD_STATUSES),
                    RunpodPod.base_url.is_not(None),
                    RunpodPod.runpod_pod_id.is_not(None),
                    RunpodPod.terminated_at.is_(None),
                )
            )
            pods = [
                _PodSnapshot(
                    id=pod.id,
                    runpod_pod_id=pod.runpod_pod_id,
                    status=pod.status,
                    base_url=pod.base_url,
                    last_used_at=pod.last_used_at,
                    last_busy_at=pod.last_busy_at,
                    updated_at=pod.updated_at,
                    created_at=pod.created_at,
                )
                for pod in result.scalars()
                if pod.active_job_id is None and pod.current_job_id is None
            ]
            session.commit()
            return pods

    def _idle_shutdown_candidates(self, cutoff: datetime) -> list[_PodSnapshot]:
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
            pods: list[_PodSnapshot] = []
            for pod in result.scalars():
                idle_since = (
                    pod.last_used_at or pod.last_busy_at or pod.updated_at or pod.created_at
                )
                if idle_since < cutoff:
                    pods.append(
                        _PodSnapshot(
                            id=pod.id,
                            runpod_pod_id=pod.runpod_pod_id,
                            status=pod.status,
                            base_url=pod.base_url,
                            last_used_at=pod.last_used_at,
                            last_busy_at=pod.last_busy_at,
                            updated_at=pod.updated_at,
                            created_at=pod.created_at,
                        )
                    )
            session.commit()
            return pods

    def _mark_pod_stopping_if_idle(self, pod_id: UUID) -> str | None:
        with get_worker_session() as session:
            with session.begin():
                pod = session.get(RunpodPod, pod_id, with_for_update=True)
                if pod is None:
                    return None
                if (
                    pod.status != PodStatus.IDLE.value
                    or pod.active_job_id is not None
                    or pod.current_job_id is not None
                ):
                    return None
                pod.status = PodStatus.STOPPING.value
                return pod.runpod_pod_id

    def _restore_idle_after_termination_failure(self, pod_id: UUID, error_message: str) -> None:
        with get_worker_session() as session:
            with session.begin():
                pod = session.get(RunpodPod, pod_id, with_for_update=True)
                if pod is None:
                    return
                if pod.status == PodStatus.STOPPING.value:
                    pod.status = PodStatus.IDLE.value
                    pod.error_message = error_message[:1000]

    def _mark_pod_terminated(self, pod_id: UUID) -> None:
        with get_worker_session() as session:
            with session.begin():
                pod = session.get(RunpodPod, pod_id, with_for_update=True)
                if pod is None:
                    return
                now = datetime.now(UTC)
                pod.status = PodStatus.TERMINATED.value
                pod.active_job_id = None
                pod.current_job_id = None
                pod.terminated_at = now
                pod.updated_at = now

    def _count_active_pods(self) -> int:
        with get_worker_session() as session:
            count = session.scalar(
                select(func.count())
                .select_from(RunpodPod)
                .where(
                    RunpodPod.status.in_(ACTIVE_POD_STATUSES),
                    RunpodPod.runpod_pod_id.is_not(None),
                    RunpodPod.terminated_at.is_(None),
                )
            )
            session.commit()
            return int(count or 0)

    def _count_busy_pods(self) -> int:
        with get_worker_session() as session:
            count = session.scalar(
                select(func.count())
                .select_from(RunpodPod)
                .where(
                    RunpodPod.status == PodStatus.BUSY.value,
                    RunpodPod.runpod_pod_id.is_not(None),
                    RunpodPod.terminated_at.is_(None),
                )
            )
            session.commit()
            return int(count or 0)

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

    def _count_pending_jobs(self) -> int:
        with get_worker_session() as session:
            count = session.scalar(
                select(func.count())
                .select_from(GenerationJob)
                .where(
                    GenerationJob.status.in_(
                        [
                            JobStatus.QUEUED.value,
                            JobStatus.WAITING_FOR_GPU.value,
                            JobStatus.WAITING_FOR_POD.value,
                        ]
                    )
                )
            )
            session.commit()
            return int(count or 0)

    def _desired_active_pods(self, pending_jobs_count: int) -> int:
        if pending_jobs_count <= 0:
            return 0
        return min(max(self._settings.runpod_max_active_pods, 1), pending_jobs_count)

    def _has_waiting_jobs(self) -> bool:
        with get_worker_session() as session:
            count = session.scalar(
                select(func.count())
                .select_from(GenerationJob)
                .where(
                    GenerationJob.status.in_(
                        [JobStatus.WAITING_FOR_GPU.value, JobStatus.WAITING_FOR_POD.value]
                    )
                )
            )
            session.commit()
            return bool(count)
