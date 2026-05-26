from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.models.generation_job import GenerationJob
from shared.app.config import Settings, get_settings
from worker.app.services.queue_load_planner import (
    QueueLoadPlan,
    calculate_queue_load_plan_for_session,
)

logger = logging.getLogger(__name__)

_pod_alert_sent_at: dict[UUID, datetime] = {}
_queue_alert_sent_at: datetime | None = None
_queue_alert_active = False


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

        plan = calculate_queue_load_plan_for_session(session, self._settings)
        text = (
            "⚠️ Нужен RunPod pod\n\n"
            f"Job ID: {job.id}\n"
            f"User telegram_id: {job.user.telegram_id}\n"
            f"Duration: {_format_duration(job.audio_duration_seconds)}\n"
            f"Format: {job.width}×{job.height}\n"
            f"Price: {_format_usd(job.price_usd)}\n"
            f"Reason: {reason[:180]}\n"
            f"Waiting jobs: {plan.waiting_for_pod_jobs_count}\n"
            f"Waiting minutes: {_format_minutes(plan.total_waiting_audio_minutes)}\n"
            f"Recommended additional pods: {plan.recommended_additional_pods}\n"
            f"Active pods: {plan.active_pods_count}\n"
            f"Idle healthy pods: {plan.idle_healthy_pods_count}\n\n"
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

        plan = calculate_queue_load_plan_for_session(session, self._settings)
        if not plan.should_alert:
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

        text = _format_queue_pressure_alert(plan)
        sent = self._send(text)
        if sent:
            _queue_alert_sent_at = now
            _queue_alert_active = True
        return sent

    def send_text_alert(self, text: str) -> bool:
        if not self._enabled():
            return False
        return self._send(text)

    def send_pod_ready_alert(
        self,
        *,
        pod_id: str,
        gpu_type: str | None,
        base_url: str | None,
        waiting_jobs: int,
        auto_retry: bool,
    ) -> bool:
        if not self._enabled():
            return False
        retry_text = (
            "Waiting jobs automatically retried."
            if auto_retry
            else "Нажми 🚀 Retry waiting jobs, чтобы забрать очередь."
        )
        return self._send(
            "✅ RunPod pod готов\n\n"
            f"Pod ID: {pod_id}\n"
            f"GPU: {gpu_type or 'unknown'}\n"
            f"ComfyUI: {base_url or 'unknown'}\n"
            f"Waiting jobs: {waiting_jobs}\n"
            f"Auto retry: {'enabled' if auto_retry else 'disabled'}\n\n"
            f"{retry_text}"
        )

    def send_starting_pod_timeout_alert(
        self,
        *,
        pod_id: str,
        gpu_type: str | None,
        base_url: str | None,
        age_minutes: int | None,
        timeout_minutes: int,
    ) -> bool:
        if not self._enabled():
            return False
        return self._send(
            "⚠️ Pod не стал healthy за "
            f"{timeout_minutes} минут\n\n"
            f"Pod ID: {pod_id}\n"
            f"GPU: {gpu_type or 'unknown'}\n"
            f"ComfyUI: {base_url or 'unknown'}\n"
            f"Age: {age_minutes if age_minutes is not None else 'unknown'} мин\n\n"
            "Проверь RunPod UI, template logs и доступность /system_stats."
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
                [{"text": "🖥 Pod’ы", "callback_data": "admin:pods"}],
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


def _format_queue_pressure_alert(plan: QueueLoadPlan) -> str:
    return (
        "⚠️ Очередь генераций растёт\n\n"
        f"Ожидают pod: {plan.waiting_for_pod_jobs_count} задач\n"
        f"Ожидающая длительность: {_format_minutes(plan.total_waiting_audio_minutes)} мин\n"
        f"Queued: {plan.queued_jobs_count}\n"
        f"Generating: {plan.generating_jobs_count}\n\n"
        "RunPod:\n"
        f"Active pod’ов: {plan.active_pods_count}\n"
        f"Healthy pod’ов: {plan.healthy_pods_count}\n"
        f"Idle healthy: {plan.idle_healthy_pods_count}\n"
        f"Busy: {plan.busy_pods_count}\n\n"
        "План нагрузки:\n"
        "Цель: "
        f"{_format_minutes(plan.target_minutes_per_pod_min)}–"
        f"{_format_minutes(plan.target_minutes_per_pod_max)} мин очереди на 1 pod\n"
        "Текущая нагрузка: "
        f"{_format_minutes(plan.total_waiting_audio_minutes)} мин "
        f"на {plan.healthy_pods_count} healthy pod’ов\n"
        f"Рекомендуется добавить: {plan.recommended_additional_pods} pod’ов\n"
        f"Лимит RUNPOD_MAX_ACTIVE_PODS: {plan.max_active_pods}\n\n"
        "Действие:\n"
        "Создай дополнительные pod’ы вручную в RunPod UI.\n"
        "После запуска нажми 🔄 Sync RunPod pods и 🚀 Retry waiting jobs."
    )


def _format_minutes(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.1")))


def _format_duration(value: Decimal | None) -> str:
    if value is None:
        return "unknown"
    return f"{value.quantize(Decimal('0.1'))} sec"


def _format_usd(value: Decimal | None) -> str:
    if value is None:
        return "unknown"
    return f"${value.quantize(Decimal('0.01'))}"
