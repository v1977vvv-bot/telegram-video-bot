from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.generation_job import GenerationJob
from backend.app.models.runpod_pod import RunpodPod
from shared.app.config import Settings, get_settings
from shared.app.enums import PodStatus, VideoQuality
from worker.app.services.admin_alerts import TelegramAdminAlertService
from worker.app.services.runpod import (
    ComfyUINotReadyError,
    NoGpuAvailableError,
    RunPodCapacityError,
    RunPodClient,
    RunPodError,
    RunPodPodInfo,
    RunPodPoolFullError,
)
from worker.app.services.runpod_costs import RunPodCostService
from worker.app.services.runpod_discovery import RunPodDiscoveryService

logger = logging.getLogger(__name__)

ASSIGNABLE_POD_STATUSES = {
    PodStatus.READY.value,
    PodStatus.IDLE.value,
}


@dataclass(frozen=True, slots=True)
class ManagedComfyUIEndpoint:
    base_url: str
    managed: bool
    runpod_pod_id: str | None = None
    db_pod_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RunPodCreateStrategy:
    tier: str
    template_id: str
    gpu_types: list[str]
    gpu_count: int
    max_attempts: int
    default_hourly_cost_usd: Decimal | None


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

        quality_profile = self._get_job_quality_profile(session, job_id)
        existing_endpoint = self._try_assign_existing_endpoint(
            session,
            job_id=job_id,
            quality_profile=quality_profile,
        )
        if existing_endpoint is not None:
            return existing_endpoint

        if self._settings.runpod_discovery_enabled:
            try:
                discovery_result = RunPodDiscoveryService(
                    self._settings,
                    runpod_client=self._client,
                ).sync_active_pods(session)
                logger.info("RunPod discovery before create result=%s", discovery_result.as_dict())
            except Exception as exc:
                logger.warning("RunPod discovery before create failed error=%s", exc)

            existing_endpoint = self._try_assign_existing_endpoint(
                session,
                job_id=job_id,
                quality_profile=quality_profile,
            )
            if existing_endpoint is not None:
                return existing_endpoint

        active_count = self._count_active_pods(session, quality_profile=quality_profile)
        cold_or_busy_count = self._count_busy_or_cold_pods(
            session,
            quality_profile=quality_profile,
        )
        session.commit()
        max_active_pods = max(self._settings.runpod_max_active_pods, 1)
        logger.info(
            "RunPod active capacity count active_count=%s assignable_count=%s "
            "max_active_pods=%s",
            active_count,
            0,
            max_active_pods,
        )
        if cold_or_busy_count > 0:
            logger.info(
                "RunPod starting/creating pods are active capacity but not assignable "
                "cold_or_busy_count=%s",
                cold_or_busy_count,
            )
        if active_count >= max_active_pods:
            logger.info(
                "RunPod pool full, job waiting active_count=%s max_active_pods=%s",
                active_count,
                max_active_pods,
            )
            raise RunPodPoolFullError("All GPU pods are busy. Job will wait.")

        if self._should_wait_instead_of_cold_start(
            session,
            job_id,
            cold_or_busy_count,
        ):
            raise RunPodPoolFullError("short_job_wait_existing_capacity")

        if not self._settings.runpod_auto_create_enabled:
            logger.info("RunPod auto-create disabled; waiting for manual/discovered pod")
            raise RunPodPoolFullError(
                "RunPod auto-create disabled; waiting for manual/discovered pod"
            )

        logger.info(
            "RunPod creating additional pod active_count=%s max_active_pods=%s",
            active_count,
            max_active_pods,
        )
        pod = self._create_and_wait_for_pod(
            session,
            job_id=job_id,
            active_count=active_count,
            quality_profile=quality_profile,
        )
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
                TelegramAdminAlertService(self._settings).send_text_alert(
                    "⚠️ Не удалось удалить idle RunPod pod\n\n"
                    f"Pod ID: {pod.runpod_pod_id}\n"
                    "Проверь RunPod UI и worker logs."
                )
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

    def _get_assignable_existing_pods(self, session: Session) -> list[RunpodPod]:
        statement = (
            select(RunpodPod)
            .where(
                RunpodPod.status.in_(
                    [
                        PodStatus.READY.value,
                        PodStatus.IDLE.value,
                    ]
                ),
                RunpodPod.base_url.is_not(None),
                RunpodPod.runpod_pod_id.is_not(None),
                RunpodPod.terminated_at.is_(None),
                RunpodPod.active_job_id.is_(None),
                RunpodPod.current_job_id.is_(None),
            )
            .order_by(RunpodPod.updated_at.desc())
        )
        return list(session.execute(statement).scalars())

    def _try_assign_existing_endpoint(
        self,
        session: Session,
        *,
        job_id: UUID | None,
        quality_profile: str,
    ) -> ManagedComfyUIEndpoint | None:
        existing_pods = self._get_assignable_existing_pods(session)
        session.commit()
        assignable_count = 0
        for existing in existing_pods:
            if existing.base_url is None:
                continue
            if not self.pod_supports_quality(existing, quality_profile):
                logger.info(
                    "RunPod assignable pod skipped incompatible quality pod_id=%s "
                    "gpu_type=%s template_id=%s quality=%s",
                    existing.runpod_pod_id,
                    existing.gpu_type,
                    existing.template_id,
                    quality_profile,
                )
                continue
            logger.info(
                "RunPod checking assignable pod pod_id=%s status=%s quality=%s",
                existing.runpod_pod_id,
                existing.status,
                quality_profile,
            )
            if self._healthcheck(existing.base_url):
                assignable_count += 1
                logger.info(
                    "RunPod assignable pod found pod_id=%s previous_status=%s",
                    existing.runpod_pod_id,
                    existing.status,
                )
                if self._try_mark_pod_busy(session, existing, job_id):
                    logger.info(
                        "RunPod assigning existing idle pod pod_id=%s job_id=%s",
                        existing.runpod_pod_id,
                        job_id,
                    )
                    logger.info("RunPod reusing existing pod pod_id=%s", existing.runpod_pod_id)
                    return ManagedComfyUIEndpoint(
                        base_url=existing.base_url,
                        managed=True,
                        runpod_pod_id=existing.runpod_pod_id,
                        db_pod_id=existing.id,
                    )
                logger.info(
                    "RunPod existing pod assignment skipped after lock pod_id=%s",
                    existing.runpod_pod_id,
                )
        if assignable_count == 0:
            logger.info("RunPod no healthy assignable pod found")
        return None

    def _should_wait_instead_of_cold_start(
        self,
        session: Session,
        job_id: UUID | None,
        cold_or_busy_count: int,
    ) -> bool:
        if not self._settings.runpod_short_job_cold_start_avoidance_enabled:
            return False
        if cold_or_busy_count <= 0:
            return False

        duration_seconds = self._estimate_job_duration_seconds(session, job_id)
        max_short_duration = Decimal(max(self._settings.runpod_short_job_max_duration_seconds, 1))
        if duration_seconds > max_short_duration:
            return False

        logger.info(
            "RunPod short job cold-start avoidance job_id=%s duration=%s " "cold_start_seconds=%s",
            job_id,
            duration_seconds,
            self._settings.runpod_estimated_cold_start_seconds,
        )
        return True

    def _estimate_job_duration_seconds(self, session: Session, job_id: UUID | None) -> Decimal:
        default_duration = Decimal(max(self._settings.runpod_default_job_duration_seconds, 1))
        if job_id is None:
            return default_duration
        duration = session.scalar(
            select(GenerationJob.audio_duration_seconds).where(GenerationJob.id == job_id)
        )
        session.commit()
        if duration is None:
            return default_duration
        return Decimal(duration)

    def _get_job_quality_profile(self, session: Session, job_id: UUID | None) -> str:
        if job_id is None:
            return VideoQuality.P480.value
        quality = session.scalar(
            select(GenerationJob.quality_profile).where(GenerationJob.id == job_id)
        )
        session.commit()
        if (quality or "").strip().lower() == VideoQuality.P720.value:
            return VideoQuality.P720.value
        return VideoQuality.P480.value

    def pod_supports_quality(self, pod: RunpodPod, quality_profile: str) -> bool:
        quality = (quality_profile or VideoQuality.P480.value).strip().lower()
        if quality != VideoQuality.P720.value:
            return True
        if not self._settings.runpod_720p_require_premium:
            return True
        if "4090" in (pod.gpu_type or "").strip().lower():
            return False
        explicit_premium_template_id = self._settings.runpod_premium_template_id.strip()
        if (
            explicit_premium_template_id
            and pod.template_id
            and pod.template_id == explicit_premium_template_id
        ):
            return True
        return self._gpu_type_is_premium(pod.gpu_type)

    def _gpu_type_is_premium(self, gpu_type: str | None) -> bool:
        normalized = (gpu_type or "").strip().lower()
        if not normalized:
            return False
        if "4090" in normalized:
            return False
        if "blackwell" in normalized or "rtx pro 6000" in normalized:
            return True
        explicit_premium = {
            item.strip().lower()
            for item in self._settings.runpod_premium_allowed_gpu_types.split(",")
            if item.strip()
        }
        return normalized in explicit_premium

    def _create_and_wait_for_pod(
        self,
        session: Session,
        *,
        job_id: UUID | None,
        active_count: int,
        quality_profile: str,
    ) -> RunpodPod:
        if not self._settings.runpod_auto_create_enabled:
            logger.info("RunPod auto-create disabled; waiting for manual/discovered pod")
            raise RunPodPoolFullError(
                "RunPod auto-create disabled; waiting for manual/discovered pod"
            )

        last_error: Exception | None = None
        sleep_seconds = max(self._settings.runpod_create_retry_sleep_seconds, 0)
        duration_seconds = self._estimate_job_duration_seconds(session, job_id)
        strategies = self._create_routing_strategies(
            job_id=job_id,
            duration_seconds=duration_seconds,
            active_count=active_count,
            quality_profile=quality_profile,
        )

        for strategy_index, strategy in enumerate(strategies):
            logger.info(
                "RunPod create strategy started tier=%s template_id=%s gpu_count=%s "
                "max_attempts=%s gpu_types=%s",
                strategy.tier,
                strategy.template_id,
                strategy.gpu_count,
                strategy.max_attempts,
                strategy.gpu_types,
            )
            for attempt in range(1, strategy.max_attempts + 1):
                logger.info(
                    "RunPod create attempt started tier=%s attempt=%s max_attempts=%s",
                    strategy.tier,
                    attempt,
                    strategy.max_attempts,
                )
                for gpu_type in strategy.gpu_types:
                    try:
                        info = self._client.create_pod(
                            gpu_type,
                            template_id=strategy.template_id,
                            gpu_count=strategy.gpu_count,
                        )
                    except RunPodCapacityError as exc:
                        last_error = exc
                        logger.warning(
                            "RunPod capacity unavailable tier=%s gpu_type=%s "
                            "template_id=%s gpu_count=%s attempt=%s",
                            strategy.tier,
                            gpu_type,
                            strategy.template_id,
                            strategy.gpu_count,
                            attempt,
                        )
                        continue

                    logger.info(
                        "RunPod pod created tier=%s gpu_type=%s template_id=%s gpu_count=%s",
                        strategy.tier,
                        info.gpu_type or gpu_type,
                        info.template_id or strategy.template_id,
                        strategy.gpu_count,
                    )
                    pod = self._create_pod_record(
                        session,
                        info,
                        cloud_type=info.cloud_type,
                        gpu_type=gpu_type,
                        template_id=info.template_id or strategy.template_id,
                        hourly_price_usd=self._resolve_created_pod_hourly_price(
                            info=info,
                            strategy=strategy,
                            gpu_type=gpu_type,
                        ),
                        job_id=job_id,
                    )
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
                        logger.info(
                            "ComfyUI ready pod_id=%s cloud_type=%s gpu_type=%s",
                            refreshed.runpod_pod_id,
                            refreshed.cloud_type,
                            refreshed.gpu_type,
                        )
                        return refreshed

                if attempt < strategy.max_attempts:
                    logger.warning(
                        "RunPod retrying create after capacity errors tier=%s sleep_seconds=%s",
                        strategy.tier,
                        sleep_seconds,
                    )
                    time.sleep(sleep_seconds)

            logger.warning("RunPod create strategy exhausted tier=%s", strategy.tier)
            if strategy.tier == "cheap" and strategy_index < len(strategies) - 1:
                logger.warning(
                    "RunPod cheap strategy unavailable, falling back to premium job_id=%s",
                    job_id,
                )

        if last_error is not None:
            logger.warning("RunPod create attempts exhausted")
            raise NoGpuAvailableError(
                "GPU temporarily unavailable. Please try again later."
            ) from last_error
        raise NoGpuAvailableError("GPU temporarily unavailable. Please try again later.")

    def _create_routing_strategies(
        self,
        *,
        job_id: UUID | None,
        duration_seconds: Decimal,
        active_count: int,
        quality_profile: str,
    ) -> list[RunPodCreateStrategy]:
        threshold = Decimal(max(self._settings.runpod_cheap_max_duration_seconds, 0))
        premium = self._premium_create_strategy(
            max_attempts=max(self._settings.runpod_primary_create_max_attempts, 1),
            quality_profile=quality_profile,
        )
        selected = "premium"
        reason = "cheap_disabled"
        strategies: list[RunPodCreateStrategy] = [premium] if premium is not None else []

        if (
            quality_profile == VideoQuality.P720.value
            and self._settings.runpod_720p_require_premium
        ):
            reason = "quality_requires_premium"
        elif self._settings.runpod_cheap_create_enabled:
            if active_count > 0:
                reason = "active_pods_exist"
            elif duration_seconds > threshold:
                reason = "duration_above_cheap_threshold"
            else:
                cheap = self._cheap_create_strategy()
                if cheap is None:
                    reason = "cheap_not_configured"
                else:
                    selected = "cheap"
                    reason = "cheap_enabled_short_job_no_active_pods"
                    premium_fallback = self._premium_create_strategy(
                        max_attempts=max(self._settings.runpod_fallback_create_max_attempts, 1),
                        quality_profile=quality_profile,
                    )
                    strategies = [cheap]
                    if premium_fallback is not None:
                        strategies.append(premium_fallback)

        logger.info(
            "RunPod quality routing decision job_id=%s quality=%s duration=%s selected=%s "
            "reason=%s active_count=%s cheap_threshold_seconds=%s",
            job_id,
            quality_profile,
            duration_seconds,
            selected,
            reason,
            active_count,
            threshold,
        )
        return strategies

    def _cheap_create_strategy(self) -> RunPodCreateStrategy | None:
        template_id = self._settings.runpod_cheap_template_id.strip()
        gpu_types = self._settings.runpod_cheap_allowed_gpu_type_list
        if not template_id or template_id == "change_me" or not gpu_types:
            return None
        return RunPodCreateStrategy(
            tier="cheap",
            template_id=template_id,
            gpu_types=gpu_types,
            gpu_count=max(self._settings.runpod_cheap_gpu_count, 1),
            max_attempts=max(self._settings.runpod_primary_create_max_attempts, 1),
            default_hourly_cost_usd=self._settings.runpod_cheap_default_hourly_cost_usd,
        )

    def _premium_create_strategy(
        self,
        *,
        max_attempts: int,
        quality_profile: str,
    ) -> RunPodCreateStrategy | None:
        template_id = self._settings.runpod_effective_premium_template_id
        gpu_types = self._settings.runpod_premium_allowed_gpu_type_list
        if (
            quality_profile == VideoQuality.P720.value
            and self._settings.runpod_720p_require_premium
        ):
            gpu_types = [
                gpu_type for gpu_type in gpu_types if self._gpu_type_is_premium(gpu_type)
            ]
        if not template_id or template_id == "change_me" or not gpu_types:
            return None
        return RunPodCreateStrategy(
            tier="premium",
            template_id=template_id,
            gpu_types=gpu_types,
            gpu_count=max(self._settings.runpod_premium_gpu_count, 1),
            max_attempts=max(max_attempts, 1),
            default_hourly_cost_usd=(
                self._settings.runpod_premium_default_hourly_cost
                if (
                    self._settings.runpod_premium_template_id.strip()
                    or self._settings.runpod_premium_allowed_gpu_types.strip()
                )
                else None
            ),
        )

    def _create_pod_record(
        self,
        session: Session,
        info: RunPodPodInfo,
        *,
        cloud_type: str | None,
        gpu_type: str,
        template_id: str,
        hourly_price_usd: Decimal,
        job_id: UUID | None,
    ) -> RunpodPod:
        with session.begin():
            pod = RunpodPod(
                provider_pod_id=info.pod_id,
                runpod_pod_id=info.pod_id,
                name=info.name,
                status=PodStatus.STARTING.value,
                cloud_type=cloud_type,
                gpu_type=info.gpu_type or gpu_type,
                template_id=template_id,
                hourly_price_usd=hourly_price_usd,
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

    def _resolve_created_pod_hourly_price(
        self,
        *,
        info: RunPodPodInfo,
        strategy: RunPodCreateStrategy,
        gpu_type: str,
    ) -> Decimal:
        if info.hourly_price_usd:
            try:
                return Decimal(info.hourly_price_usd)
            except InvalidOperation:
                logger.warning(
                    "RunPod hourly price from API is invalid pod_id=%s value=%s",
                    info.pod_id,
                    info.hourly_price_usd,
                )
        if strategy.default_hourly_cost_usd is not None:
            return strategy.default_hourly_cost_usd
        return RunPodCostService(self._settings).get_gpu_hourly_cost(info.gpu_type or gpu_type)

    def _count_active_pods(
        self,
        session: Session,
        *,
        quality_profile: str = VideoQuality.P480.value,
    ) -> int:
        statement = select(RunpodPod).where(
            RunpodPod.status.in_(
                [
                    PodStatus.CREATING.value,
                    PodStatus.STARTING.value,
                    PodStatus.READY.value,
                    PodStatus.IDLE.value,
                    PodStatus.BUSY.value,
                ]
            ),
            RunpodPod.runpod_pod_id.is_not(None),
            RunpodPod.terminated_at.is_(None),
        )
        return len(
            [
                pod
                for pod in session.execute(statement).scalars()
                if self.pod_supports_quality(pod, quality_profile)
            ]
        )

    def _count_busy_or_cold_pods(
        self,
        session: Session,
        *,
        quality_profile: str = VideoQuality.P480.value,
    ) -> int:
        statement = select(RunpodPod).where(
            RunpodPod.status.in_(
                [
                    PodStatus.CREATING.value,
                    PodStatus.STARTING.value,
                    PodStatus.BUSY.value,
                ]
            ),
            RunpodPod.runpod_pod_id.is_not(None),
            RunpodPod.terminated_at.is_(None),
        )
        return len(
            [
                pod
                for pod in session.execute(statement).scalars()
                if self.pod_supports_quality(pod, quality_profile)
            ]
        )

    def _try_mark_pod_busy(
        self,
        session: Session,
        pod: RunpodPod,
        job_id: UUID | None,
    ) -> bool:
        with session.begin():
            refreshed = session.get(RunpodPod, pod.id, with_for_update=True)
            if refreshed is None:
                raise RunPodError(f"RunPod pod record disappeared pod_id={pod.runpod_pod_id}")
            if (
                refreshed.status not in ASSIGNABLE_POD_STATUSES
                or refreshed.terminated_at is not None
                or refreshed.active_job_id is not None
                or refreshed.current_job_id is not None
            ):
                return False
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
            return True

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
        logger.info(
            "Waiting for ComfyUI readiness base_url=%s timeout_seconds=%s interval_seconds=%s",
            base_url,
            self._settings.runpod_pod_ready_timeout_seconds,
            interval,
        )
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
