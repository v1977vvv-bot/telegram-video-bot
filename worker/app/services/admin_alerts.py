from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.app.models.generation_job import GenerationJob
from backend.app.models.runpod_pod import RunpodPod
from shared.app.config import Settings, get_settings
from shared.app.enums import JobStatus, PodStatus

logger = logging.getLogger(__name__)

_pod_alert_sent_at: dict[UUID, datetime] = {}
_queue_alert_sent_at: datetime | None = None
_queue_alert_active = False


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    waiting_for_pod: int
    queued: int
    generating: int
    active_pods: int
    healthy_pods: int
    idle_healthy_pods: int
    oldest_wait_minutes: int
    recommended_pods: int


class TelegramAdminAlertService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def send_pod_needed_alert(
        self,
        session: Session,
        *,
        job_id: UUID,
        reason: str,
    ) -> bool:
        if not self._enabled():
            return False
        now = datetime.now(UTC)
        cooldown = timedelta(minutes=max(self._settings.admin_pod_alert_cooldown_minutes, 1))
        last_sent_at = _pod_alert_sent_at.get(job_id)
        if last_sent_at is not None and now - last_sent_at < cooldown:
            logger.info("Admin pod alert suppressed by cooldown job_id=%s", job_id)
            return False

        job = session.scalar(
            select(GenerationJob)
            .where(GenerationJob.id == job_id)
            .options(selectinload(GenerationJob.user))
        )
        if job is None:
            return False

        snapshot = self._queue_snapshot(session)
        text = (
            "⚠️ Нужен RunPod pod\n\n"
            f"Job ID: {job.id}\n"
            f"User telegram_id: {job.user.telegram_id}\n"
            f"Duration: {_format_duration(job.audio_duration_seconds)}\n"
            f"Format: {job.width}×{job.height}\n"
            f"Price: {_format_usd(job.price_usd)}\n"
            f"Reason: {reason[:180]}\n"
            f"Waiting jobs: {snapshot.waiting_for_pod}\n"
            f"Active pods: {snapshot.active_pods}\n"
            f"Idle healthy pods: {snapshot.idle_healthy_pods}\n\n"
            "Действие:\n"
            "Создай pod вручную в RunPod UI.\n"
            "Бот сам подхватит его через Sync RunPod pods."
        )
        sent = self._send(text)
        if sent:
            _pod_alert_sent_at[job_id] = now
        return sent

    def send_queue_pressure_alert_if_needed(self, session: Session) -> bool:
        global _queue_alert_active, _queue_alert_sent_at
        if not self._enabled():
            return False

        snapshot = self._queue_snapshot(session)
        pressure = (
            snapshot.waiting_for_pod >= self._settings.admin_queue_alert_min_waiting_jobs
            or snapshot.oldest_wait_minutes >= self._settings.admin_queue_alert_target_wait_minutes
            or (snapshot.waiting_for_pod > 0 and snapshot.idle_healthy_pods == 0)
        )
        if not pressure:
            _queue_alert_active = False
            return False

        now = datetime.now(UTC)
        cooldown = timedelta(minutes=max(self._settings.admin_queue_alert_cooldown_minutes, 1))
        if _queue_alert_sent_at is not None and now - _queue_alert_sent_at < cooldown:
            logger.info("Admin queue alert suppressed by cooldown")
            return False
        if _queue_alert_active and not self._settings.admin_queue_alert_repeat_enabled:
            logger.info("Admin queue alert suppressed because repeat is disabled")
            return False

        text = (
            "⚠️ Очередь генераций растёт\n\n"
            f"Ожидают pod: {snapshot.waiting_for_pod}\n"
            f"Queued: {snapshot.queued}\n"
            f"Generating: {snapshot.generating}\n"
            f"Активных pod’ов в RunPod: {snapshot.active_pods}\n"
            f"Healthy ComfyUI pod’ов: {snapshot.healthy_pods}\n"
            f"Idle healthy pod’ов: {snapshot.idle_healthy_pods}\n"
            f"Самая старая задача ждёт: {snapshot.oldest_wait_minutes} минут\n"
            f"Рекомендуется добавить pod’ов: {snapshot.recommended_pods}\n\n"
            "Действие:\n"
            "Создай дополнительные pod’ы вручную в RunPod UI.\n"
            "Бот сам подхватит их через sync."
        )
        sent = self._send(text)
        if sent:
            _queue_alert_sent_at = now
            _queue_alert_active = True
        return sent

    def send_text_alert(self, text: str) -> bool:
        if not self._enabled():
            return False
        return self._send(text)

    def _queue_snapshot(self, session: Session) -> QueueSnapshot:
        waiting_for_pod = _count_jobs(session, JobStatus.WAITING_FOR_POD.value)
        queued = _count_jobs(session, JobStatus.QUEUED.value)
        generating = _count_jobs(session, JobStatus.GENERATING.value)
        active_pods = _count_pods(
            session,
            [
                PodStatus.CREATING.value,
                PodStatus.STARTING.value,
                PodStatus.READY.value,
                PodStatus.IDLE.value,
                PodStatus.BUSY.value,
            ],
        )
        healthy_pods = _count_pods(
            session,
            [PodStatus.READY.value, PodStatus.IDLE.value, PodStatus.BUSY.value],
            require_healthcheck=True,
        )
        idle_healthy_pods = _count_pods(
            session,
            [PodStatus.READY.value, PodStatus.IDLE.value],
            require_healthcheck=True,
            require_idle=True,
        )
        oldest_wait = session.scalar(
            select(func.min(GenerationJob.waiting_for_pod_since)).where(
                GenerationJob.status == JobStatus.WAITING_FOR_POD.value,
                GenerationJob.waiting_for_pod_since.is_not(None),
            )
        )
        oldest_wait_minutes = 0
        if oldest_wait is not None:
            oldest_wait_minutes = max(
                0,
                int((datetime.now(UTC) - oldest_wait).total_seconds() // 60),
            )
        max_pods = max(self._settings.runpod_max_active_pods, 0)
        recommended_pods = max(0, min(max_pods - active_pods, waiting_for_pod - idle_healthy_pods))
        return QueueSnapshot(
            waiting_for_pod=waiting_for_pod,
            queued=queued,
            generating=generating,
            active_pods=active_pods,
            healthy_pods=healthy_pods,
            idle_healthy_pods=idle_healthy_pods,
            oldest_wait_minutes=oldest_wait_minutes,
            recommended_pods=recommended_pods,
        )

    def _enabled(self) -> bool:
        return (
            self._settings.admin_alerts_enabled
            and bool(str(self._settings.admin_alert_chat_id).strip())
            and self._settings.admin_alert_bot_token_is_configured
        )

    def _send(self, text: str) -> bool:
        token = self._settings.admin_alert_bot_token
        chat_id = str(self._settings.admin_alert_chat_id).strip()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        reply_markup = {
            "inline_keyboard": [
                [{"text": "🔄 Sync pods", "callback_data": "admin:sync_pods"}],
                [{"text": "🚀 Retry waiting", "callback_data": "admin:retry_waiting"}],
                [
                    {
                        "text": "🌐 Web admin",
                        "url": f"{self._settings.backend_public_url.rstrip('/')}/admin",
                    }
                ],
            ]
        }
        try:
            with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
                response = client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "reply_markup": reply_markup,
                        "disable_web_page_preview": True,
                    },
                )
                response.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("Admin Telegram alert failed error=%s", exc.__class__.__name__)
            return False


def _count_jobs(session: Session, status: str) -> int:
    return int(
        session.scalar(select(func.count(GenerationJob.id)).where(GenerationJob.status == status))
        or 0
    )


def _count_pods(
    session: Session,
    statuses: list[str],
    *,
    require_healthcheck: bool = False,
    require_idle: bool = False,
) -> int:
    query = select(func.count(RunpodPod.id)).where(
        RunpodPod.status.in_(statuses),
        RunpodPod.terminated_at.is_(None),
    )
    if require_healthcheck:
        query = query.where(RunpodPod.last_healthcheck_at.is_not(None))
    if require_idle:
        query = query.where(
            RunpodPod.active_job_id.is_(None),
            RunpodPod.current_job_id.is_(None),
        )
    return int(session.scalar(query) or 0)


def _format_duration(value: Decimal | None) -> str:
    if value is None:
        return "unknown"
    return f"{value.quantize(Decimal('0.1'))} sec"


def _format_usd(value: Decimal | None) -> str:
    if value is None:
        return "unknown"
    return f"${value.quantize(Decimal('0.01'))}"
