from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.runpod_pod import RunpodPod
from shared.app.config import Settings, get_settings
from shared.app.enums import PodStatus
from worker.app.services.runpod import (
    ComfyUINotReadyError,
    NoGpuAvailableError,
    RunPodCapacityError,
    RunPodClient,
    RunPodError,
    RunPodPodInfo,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ManagedComfyUIEndpoint:
    base_url: str
    managed: bool
    runpod_pod_id: str | None = None
    db_pod_id: UUID | None = None


class RunPodManager:
    """Provision and reuse one RunPod-hosted ComfyUI endpoint for Celery workers."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        runpod_client: RunPodClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = runpod_client or RunPodClient(self._settings)

    def ensure_comfyui_endpoint(
        self,
        session: Session,
        *,
        job_id: UUID | None = None,
    ) -> ManagedComfyUIEndpoint:
        if not self._settings.runpod_auto_manager_enabled:
            return ManagedComfyUIEndpoint(
                base_url=self._settings.comfyui_base_url.rstrip("/"),
                managed=False,
            )

        existing_pods = self._get_reusable_existing_pods(session)
        session.commit()
        for existing in existing_pods:
            if existing.base_url is None:
                continue
            logger.info(
                "RunPod checking existing pod pod_id=%s status=%s",
                existing.runpod_pod_id,
                existing.status,
            )
            if self._healthcheck(existing.base_url):
                logger.info(
                    "RunPod existing pod healthcheck ok pod_id=%s previous_status=%s",
                    existing.runpod_pod_id,
                    existing.status,
                )
                self._mark_pod_busy(session, existing, job_id)
                logger.info("RunPod reusing existing pod pod_id=%s", existing.runpod_pod_id)
                return ManagedComfyUIEndpoint(
                    base_url=existing.base_url,
                    managed=True,
                    runpod_pod_id=existing.runpod_pod_id,
                    db_pod_id=existing.id,
                )

        pod = self._create_and_wait_for_pod(session, job_id=job_id)
        return ManagedComfyUIEndpoint(
            base_url=pod.base_url or self._client.build_comfyui_base_url(pod.runpod_pod_id),
            managed=True,
            runpod_pod_id=pod.runpod_pod_id,
            db_pod_id=pod.id,
        )

    def mark_pod_idle(self, session: Session, endpoint: ManagedComfyUIEndpoint) -> None:
        if not endpoint.managed or endpoint.db_pod_id is None:
            return
        with session.begin():
            pod = session.get(RunpodPod, endpoint.db_pod_id, with_for_update=True)
            if pod is None:
                return
            pod.status = PodStatus.IDLE.value
            pod.active_job_id = None
            pod.current_job_id = None
            pod.last_used_at = datetime.now(UTC)
            pod.last_busy_at = pod.last_used_at
            pod.error_message = None
            logger.info("RunPod pod marked idle pod_id=%s", pod.runpod_pod_id)

    def release_after_failure(self, session: Session, endpoint: ManagedComfyUIEndpoint) -> None:
        if not endpoint.managed or endpoint.db_pod_id is None:
            return
        with session.begin():
            pod = session.get(RunpodPod, endpoint.db_pod_id, with_for_update=True)
            if pod is None:
                return
            base_url = pod.base_url

        if base_url and self._healthcheck(base_url):
            with session.begin():
                pod = session.get(RunpodPod, endpoint.db_pod_id, with_for_update=True)
                if pod is None:
                    return
                pod.status = PodStatus.IDLE.value
                pod.active_job_id = None
                pod.current_job_id = None
                pod.last_healthcheck_at = datetime.now(UTC)
                pod.last_used_at = pod.last_healthcheck_at
                pod.last_busy_at = pod.last_healthcheck_at
                logger.info("RunPod pod kept idle after job failure pod_id=%s", pod.runpod_pod_id)
                return

        with session.begin():
            pod = session.get(RunpodPod, endpoint.db_pod_id, with_for_update=True)
            if pod is None:
                return
            pod.status = PodStatus.FAILED.value
            pod.active_job_id = None
            pod.current_job_id = None
            pod.error_message = "ComfyUI healthcheck failed after job failure"
            logger.warning("RunPod pod marked failed pod_id=%s", pod.runpod_pod_id)

    def terminate_idle_pods(self, session: Session, *, force: bool = False) -> list[str]:
        cutoff = datetime.now(UTC) - timedelta(
            minutes=self._settings.runpod_pod_idle_shutdown_minutes
        )
        statement = select(RunpodPod).where(
            RunpodPod.status.in_([PodStatus.IDLE.value, PodStatus.READY.value]),
            RunpodPod.active_job_id.is_(None),
            RunpodPod.runpod_pod_id.is_not(None),
        )
        if not force:
            statement = statement.where(
                RunpodPod.last_used_at.is_not(None),
                RunpodPod.last_used_at < cutoff,
            )

        pods = list(session.execute(statement).scalars())
        session.commit()
        terminated: list[str] = []
        for pod in pods:
            try:
                self._client.terminate_pod(pod.runpod_pod_id)
            except RunPodError:
                logger.exception("RunPod idle pod termination failed pod_id=%s", pod.runpod_pod_id)
                continue

            with session.begin():
                refreshed = session.get(RunpodPod, pod.id, with_for_update=True)
                if refreshed is None:
                    continue
                now = datetime.now(UTC)
                refreshed.status = PodStatus.TERMINATED.value
                refreshed.active_job_id = None
                refreshed.current_job_id = None
                refreshed.terminated_at = now
                refreshed.updated_at = now
                terminated.append(refreshed.runpod_pod_id)
                logger.info("RunPod pod terminated pod_id=%s", refreshed.runpod_pod_id)
        return terminated

    def _get_reusable_existing_pods(self, session: Session) -> list[RunpodPod]:
        statement = (
            select(RunpodPod)
            .where(
                RunpodPod.status.in_(
                    [
                        PodStatus.STARTING.value,
                        PodStatus.CREATING.value,
                        PodStatus.READY.value,
                        PodStatus.IDLE.value,
                    ]
                ),
                RunpodPod.base_url.is_not(None),
                RunpodPod.runpod_pod_id.is_not(None),
                RunpodPod.active_job_id.is_(None),
                RunpodPod.current_job_id.is_(None),
            )
            .order_by(RunpodPod.updated_at.desc())
        )
        return list(session.execute(statement).scalars())

    def _create_and_wait_for_pod(self, session: Session, *, job_id: UUID | None) -> RunpodPod:
        last_error: Exception | None = None
        max_attempts = max(self._settings.runpod_create_max_attempts, 1)
        sleep_seconds = max(self._settings.runpod_create_retry_sleep_seconds, 0)

        for phase, min_ram_gb in self._create_resource_phases():
            logger.info(
                "RunPod create phase started phase=%s min_ram_gb=%s",
                phase,
                min_ram_gb,
            )
            for attempt in range(1, max_attempts + 1):
                logger.info(
                    "RunPod create attempt started phase=%s attempt=%s "
                    "max_attempts=%s min_ram_gb=%s",
                    phase,
                    attempt,
                    max_attempts,
                    min_ram_gb,
                )
                for gpu_type in self._settings.runpod_allowed_gpu_type_list:
                    try:
                        info = self._client.create_pod(gpu_type, min_ram_gb=min_ram_gb)
                    except RunPodCapacityError as exc:
                        last_error = exc
                        logger.warning(
                            "RunPod capacity unavailable phase=%s gpu_type=%s "
                            "min_ram_gb=%s attempt=%s",
                            phase,
                            gpu_type,
                            min_ram_gb,
                            attempt,
                        )
                        continue

                    logger.info(
                        "RunPod pod created gpu_type=%s min_ram_gb=%s phase=%s",
                        info.gpu_type or gpu_type,
                        min_ram_gb,
                        phase,
                    )
                    pod = self._create_pod_record(session, info, gpu_type=gpu_type, job_id=job_id)
                    try:
                        self._wait_for_comfyui_ready(info.base_url, info.pod_id)
                    except ComfyUINotReadyError as exc:
                        self._mark_pod_failed(session, pod, str(exc))
                        if self._settings.runpod_auto_terminate:
                            self._terminate_failed_pod(session, pod)
                        raise

                    with session.begin():
                        refreshed = session.get(RunpodPod, pod.id, with_for_update=True)
                        if refreshed is None:
                            raise RunPodError(
                                f"RunPod pod record disappeared pod_id={pod.runpod_pod_id}"
                            )
                        now = datetime.now(UTC)
                        refreshed.status = PodStatus.BUSY.value
                        refreshed.active_job_id = job_id
                        refreshed.current_job_id = job_id
                        refreshed.last_healthcheck_at = now
                        refreshed.last_busy_at = now
                        refreshed.last_used_at = now
                        refreshed.error_message = None
                        logger.info("ComfyUI ready pod_id=%s", refreshed.runpod_pod_id)
                        return refreshed

                if attempt < max_attempts:
                    logger.warning(
                        "RunPod retrying create after capacity errors phase=%s " "sleep_seconds=%s",
                        phase,
                        sleep_seconds,
                    )
                    time.sleep(sleep_seconds)

            logger.warning(
                "RunPod create phase exhausted phase=%s min_ram_gb=%s",
                phase,
                min_ram_gb,
            )

        if last_error is not None:
            logger.warning("RunPod create attempts exhausted")
            raise NoGpuAvailableError(
                "GPU temporarily unavailable. Please try again later."
            ) from last_error
        raise NoGpuAvailableError("GPU temporarily unavailable. Please try again later.")

    def _create_resource_phases(self) -> list[tuple[str, int]]:
        phases = [("primary", self._settings.runpod_min_ram_gb)]
        if (
            self._settings.runpod_ram_fallback_enabled
            and self._settings.runpod_fallback_min_ram_gb is not None
        ):
            phases.append(("fallback", self._settings.runpod_fallback_min_ram_gb))
        return phases

    def _create_pod_record(
        self,
        session: Session,
        info: RunPodPodInfo,
        *,
        gpu_type: str,
        job_id: UUID | None,
    ) -> RunpodPod:
        with session.begin():
            pod = RunpodPod(
                provider_pod_id=info.pod_id,
                runpod_pod_id=info.pod_id,
                name=info.name,
                status=PodStatus.STARTING.value,
                cloud_type=self._settings.runpod_cloud_type,
                gpu_type=info.gpu_type or gpu_type,
                template_id=self._settings.runpod_template_id,
                base_url=info.base_url,
                comfyui_url=info.base_url,
                comfyui_port=self._settings.runpod_comfyui_port,
                active_job_id=job_id,
                current_job_id=job_id,
            )
            session.add(pod)
            session.flush()
            logger.info("RunPod pod record created pod_id=%s", pod.runpod_pod_id)
            return pod

    def _mark_pod_busy(self, session: Session, pod: RunpodPod, job_id: UUID | None) -> None:
        with session.begin():
            refreshed = session.get(RunpodPod, pod.id, with_for_update=True)
            if refreshed is None:
                raise RunPodError(f"RunPod pod record disappeared pod_id={pod.runpod_pod_id}")
            now = datetime.now(UTC)
            refreshed.status = PodStatus.BUSY.value
            refreshed.active_job_id = job_id
            refreshed.current_job_id = job_id
            refreshed.last_healthcheck_at = now
            refreshed.last_busy_at = now
            refreshed.last_used_at = now
            refreshed.error_message = None
            logger.info(
                "RunPod pod marked busy pod_id=%s job_id=%s", refreshed.runpod_pod_id, job_id
            )

    def _mark_pod_failed(self, session: Session, pod: RunpodPod, error_message: str) -> None:
        with session.begin():
            refreshed = session.get(RunpodPod, pod.id, with_for_update=True)
            if refreshed is None:
                return
            refreshed.status = PodStatus.FAILED.value
            refreshed.active_job_id = None
            refreshed.current_job_id = None
            refreshed.error_message = error_message[:1000]

    def _terminate_failed_pod(self, session: Session, pod: RunpodPod) -> None:
        try:
            self._client.terminate_pod(pod.runpod_pod_id)
        except RunPodError:
            logger.exception("RunPod failed pod termination failed pod_id=%s", pod.runpod_pod_id)
            return

        with session.begin():
            refreshed = session.get(RunpodPod, pod.id, with_for_update=True)
            if refreshed is None:
                return
            now = datetime.now(UTC)
            refreshed.status = PodStatus.TERMINATED.value
            refreshed.terminated_at = now
            refreshed.updated_at = now
            logger.info("RunPod failed pod terminated pod_id=%s", refreshed.runpod_pod_id)

    def _wait_for_comfyui_ready(self, base_url: str, pod_id: str) -> None:
        deadline = time.monotonic() + self._settings.runpod_pod_ready_timeout_seconds
        interval = max(self._settings.runpod_healthcheck_interval_seconds, 1)
        logger.info("Waiting for ComfyUI readiness base_url=%s", base_url)
        while time.monotonic() < deadline:
            if self._healthcheck(base_url):
                return
            if self._pod_disappeared_or_terminated(pod_id):
                raise ComfyUINotReadyError(
                    "RunPod pod disappeared or was terminated while waiting for ComfyUI"
                )
            time.sleep(min(interval, max(deadline - time.monotonic(), 0)))
        raise ComfyUINotReadyError("ComfyUI did not become ready before timeout")

    def _pod_disappeared_or_terminated(self, pod_id: str) -> bool:
        try:
            info = self._client.get_pod(pod_id)
        except RunPodError as exc:
            if "HTTP 404" in str(exc):
                logger.warning("RunPod pod disappeared while waiting pod_id=%s", pod_id)
                return True
            logger.warning("RunPod pod lookup failed while waiting pod_id=%s error=%s", pod_id, exc)
            return False

        status = (info.status or "").strip().lower()
        if status in {"terminated", "deleted", "stopped", "exited"}:
            logger.warning(
                "RunPod pod terminated while waiting pod_id=%s status=%s",
                pod_id,
                status,
            )
            return True
        return False

    def _healthcheck(self, base_url: str) -> bool:
        try:
            with httpx.Client(
                base_url=base_url,
                timeout=httpx.Timeout(15.0, connect=5.0),
                follow_redirects=True,
            ) as client:
                response = client.get("/system_stats")
                response.raise_for_status()
            return True
        except Exception:
            logger.warning("ComfyUI healthcheck failed base_url=%s", base_url)
            return False
