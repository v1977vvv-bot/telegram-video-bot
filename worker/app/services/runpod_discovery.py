from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.runpod_pod import RunpodPod
from shared.app.config import Settings, get_settings
from shared.app.enums import PodStatus
from worker.app.services.runpod import RunPodClient, RunPodPodInfo
from worker.app.services.runpod_costs import RunPodCostService

logger = logging.getLogger(__name__)

ACTIVE_DISCOVERY_STATUSES = {"running", "started", "ready", "active", "idle"}
ACTIVE_DB_POD_STATUSES = {
    PodStatus.CREATING.value,
    PodStatus.STARTING.value,
    PodStatus.READY.value,
    PodStatus.IDLE.value,
    PodStatus.BUSY.value,
}


@dataclass(frozen=True, slots=True)
class RunPodDiscoverySkippedPod:
    pod_id: str
    reason: str
    status: str | None = None
    gpu_type: str | None = None
    template_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "pod_id": self.pod_id,
            "reason": self.reason,
            "status": self.status,
            "gpu_type": self.gpu_type,
            "template_id": self.template_id,
        }


@dataclass(slots=True)
class RunPodDiscoveryResult:
    found: int = 0
    registered: int = 0
    updated: int = 0
    healthy: int = 0
    skipped: list[RunPodDiscoverySkippedPod] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "found": self.found,
            "registered": self.registered,
            "updated": self.updated,
            "healthy": self.healthy,
            "skipped": [item.as_dict() for item in self.skipped],
        }


class RunPodDiscoveryService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        runpod_client: RunPodClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = runpod_client or RunPodClient(self._settings)

    def sync_active_pods(self, session: Session) -> RunPodDiscoveryResult:
        if not self._settings.runpod_discovery_enabled:
            logger.info("RunPod discovery disabled")
            return RunPodDiscoveryResult()
        if not self._settings.runpod_auto_manager_enabled:
            logger.info("RunPod discovery skipped because RunPod API is not configured")
            return RunPodDiscoveryResult()

        pods = self._client.list_pods()
        result = RunPodDiscoveryResult(found=len(pods))
        seen_pod_ids: set[str] = set()

        for pod_info in pods:
            seen_pod_ids.add(pod_info.pod_id)
            skip_reason = self._skip_reason(pod_info)
            healthy = False
            if skip_reason is None:
                healthy = self._healthcheck(pod_info.base_url)
                if healthy:
                    result.healthy += 1
                elif self._settings.runpod_discovery_require_healthy:
                    skip_reason = "comfyui_healthcheck_failed"

            if skip_reason is not None:
                result.skipped.append(
                    RunPodDiscoverySkippedPod(
                        pod_id=pod_info.pod_id,
                        reason=skip_reason,
                        status=pod_info.status,
                        gpu_type=pod_info.gpu_type,
                        template_id=pod_info.template_id,
                    )
                )
                continue

            if not self._settings.runpod_discovery_auto_register:
                continue

            created = self._register_or_update_pod(session, pod_info, healthy=healthy)
            if created:
                result.registered += 1
            else:
                result.updated += 1

        self._mark_missing_pods(session, seen_pod_ids)
        session.commit()
        logger.info(
            "RunPod discovery sync completed found=%s registered=%s updated=%s "
            "healthy=%s skipped=%s",
            result.found,
            result.registered,
            result.updated,
            result.healthy,
            len(result.skipped),
        )
        return result

    def check_registered_pods_health(self, session: Session) -> RunPodDiscoveryResult:
        result = RunPodDiscoveryResult()
        pods = list(
            session.execute(
                select(RunpodPod).where(
                    RunpodPod.status.in_(ACTIVE_DB_POD_STATUSES),
                    RunpodPod.base_url.is_not(None),
                    RunpodPod.terminated_at.is_(None),
                )
            ).scalars()
        )
        result.found = len(pods)
        now = datetime.now(UTC)
        for pod in pods:
            healthy = self._healthcheck(pod.base_url or "")
            if healthy:
                result.healthy += 1
                pod.last_healthcheck_at = now
                pod.last_heartbeat_at = now
                pod.error_message = None
                if pod.active_job_id is None and pod.current_job_id is None:
                    pod.status = PodStatus.IDLE.value
            else:
                pod.error_message = "ComfyUI healthcheck failed during admin check"
                result.skipped.append(
                    RunPodDiscoverySkippedPod(
                        pod_id=pod.runpod_pod_id,
                        reason="comfyui_healthcheck_failed",
                        status=pod.status,
                        gpu_type=pod.gpu_type,
                        template_id=pod.template_id,
                    )
                )
        session.commit()
        return result

    def _skip_reason(self, pod_info: RunPodPodInfo) -> str | None:
        status = (pod_info.status or "").strip().lower()
        if status and status not in ACTIVE_DISCOVERY_STATUSES:
            return f"status_not_active:{status}"

        allowed_gpus = {
            gpu_type.lower()
            for gpu_type in (
                self._settings.runpod_allowed_gpu_type_list
                + self._settings.runpod_fallback_allowed_gpu_type_list
            )
        }
        if allowed_gpus and (pod_info.gpu_type or "").lower() not in allowed_gpus:
            return "gpu_not_allowed"

        configured_template_id = self._settings.runpod_template_id.strip()
        if (
            pod_info.template_id
            and configured_template_id
            and configured_template_id != "change_me"
            and pod_info.template_id != configured_template_id
        ):
            return "template_mismatch"

        expected_port = f"{self._settings.runpod_comfyui_port}/http"
        discovered_ports = {port.lower() for port in pod_info.ports}
        if pod_info.ports and expected_port.lower() not in discovered_ports:
            return "comfyui_port_missing"
        return None

    def _register_or_update_pod(
        self,
        session: Session,
        pod_info: RunPodPodInfo,
        *,
        healthy: bool,
    ) -> bool:
        now = datetime.now(UTC)
        pod = session.scalar(select(RunpodPod).where(RunpodPod.runpod_pod_id == pod_info.pod_id))
        created = pod is None
        if pod is None:
            pod = RunpodPod(
                provider_pod_id=pod_info.pod_id,
                runpod_pod_id=pod_info.pod_id,
                name=pod_info.name,
                status=PodStatus.IDLE.value if healthy else PodStatus.STARTING.value,
                cloud_type=pod_info.cloud_type,
                gpu_type=pod_info.gpu_type,
                template_id=pod_info.template_id or self._settings.runpod_template_id,
                hourly_price_usd=self._hourly_price(pod_info),
                base_url=pod_info.base_url,
                comfyui_url=pod_info.base_url,
                comfyui_port=self._settings.runpod_comfyui_port,
                last_healthcheck_at=now if healthy else None,
                last_heartbeat_at=now,
                last_used_at=now if healthy else None,
                last_busy_at=None,
            )
            session.add(pod)
            logger.info(
                "RunPod discovery registered pod pod_id=%s gpu_type=%s cloud_type=%s healthy=%s",
                pod_info.pod_id,
                pod_info.gpu_type,
                pod_info.cloud_type,
                healthy,
            )
            return created

        pod.name = pod_info.name or pod.name
        pod.provider_pod_id = pod_info.pod_id
        pod.runpod_pod_id = pod_info.pod_id
        pod.cloud_type = pod_info.cloud_type or pod.cloud_type
        pod.gpu_type = pod_info.gpu_type or pod.gpu_type
        pod.template_id = (
            pod_info.template_id or pod.template_id or self._settings.runpod_template_id
        )
        pod.hourly_price_usd = self._hourly_price(pod_info) or pod.hourly_price_usd
        pod.base_url = pod_info.base_url
        pod.comfyui_url = pod_info.base_url
        pod.comfyui_port = self._settings.runpod_comfyui_port
        pod.last_heartbeat_at = now
        if healthy:
            pod.last_healthcheck_at = now
            pod.error_message = None
            if pod.active_job_id is None and pod.current_job_id is None:
                pod.status = PodStatus.IDLE.value
                pod.last_used_at = pod.last_used_at or now
        elif pod.active_job_id is None and pod.current_job_id is None:
            pod.status = PodStatus.STARTING.value
            pod.error_message = "Discovered pod is not healthy yet"
        logger.info(
            "RunPod discovery updated pod pod_id=%s status=%s healthy=%s",
            pod_info.pod_id,
            pod.status,
            healthy,
        )
        return created

    def _mark_missing_pods(self, session: Session, seen_pod_ids: set[str]) -> None:
        pods = list(
            session.execute(
                select(RunpodPod).where(
                    RunpodPod.status.in_(ACTIVE_DB_POD_STATUSES),
                    RunpodPod.runpod_pod_id.is_not(None),
                    RunpodPod.terminated_at.is_(None),
                )
            ).scalars()
        )
        now = datetime.now(UTC)
        for pod in pods:
            if pod.runpod_pod_id in seen_pod_ids:
                continue
            if pod.active_job_id is not None or pod.current_job_id is not None:
                pod.error_message = "RunPod pod missing from discovery while job is active"
                continue
            pod.status = PodStatus.DELETED.value
            pod.terminated_at = now
            pod.error_message = "RunPod pod missing from discovery"
            logger.info("RunPod discovery marked missing pod deleted pod_id=%s", pod.runpod_pod_id)

    def _hourly_price(self, pod_info: RunPodPodInfo) -> Decimal | None:
        if pod_info.hourly_price_usd:
            try:
                return Decimal(pod_info.hourly_price_usd)
            except InvalidOperation:
                logger.warning(
                    "RunPod discovery ignored invalid hourly price pod_id=%s",
                    pod_info.pod_id,
                )
        return RunPodCostService(self._settings).get_cloud_gpu_hourly_cost(
            cloud_type=pod_info.cloud_type,
            gpu_type=pod_info.gpu_type,
        )

    def _healthcheck(self, base_url: str) -> bool:
        if not base_url:
            return False
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
            logger.info("RunPod discovery ComfyUI healthcheck failed base_url=%s", base_url)
            return False
