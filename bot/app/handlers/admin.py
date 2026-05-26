from __future__ import annotations

import logging
from html import escape
from typing import Any

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.app.services.backend_client import (
    BackendClientError,
    BackendUnavailableError,
    BotBackendClient,
)
from shared.app.config import get_settings

logger = logging.getLogger(__name__)
router = Router()
backend_client = BotBackendClient()


@router.message(Command("admin"))
async def handle_admin_command(message: Message) -> None:
    if message.from_user is None or not _is_admin(message.from_user.id):
        await message.answer("Команда недоступна")
        return
    await _send_admin_overview(message, message.from_user.id)


@router.callback_query(lambda callback: callback.data and callback.data.startswith("admin:"))
async def handle_admin_callback(callback: CallbackQuery) -> None:
    if callback.from_user is None or not _is_admin(callback.from_user.id):
        await callback.answer("Команда недоступна", show_alert=True)
        return

    action = callback.data or ""
    telegram_id = callback.from_user.id
    try:
        if action == "admin:overview":
            if callback.message is not None:
                await _send_admin_overview(callback.message, telegram_id, edit=True)
        elif action == "admin:sync_pods":
            result = await backend_client.sync_runpod_pods(telegram_id=telegram_id)
            text = _format_sync_result(result)
            await _reply_or_edit(callback, text, _post_sync_keyboard())
        elif action == "admin:retry_waiting":
            result = await backend_client.retry_waiting_jobs(telegram_id=telegram_id)
            await _reply_or_edit(
                callback,
                "✅ Waiting jobs отправлены на повторную проверку.\n\n"
                f"Enqueued: {result.get('enqueued', 0)}",
                _admin_keyboard(),
            )
        elif action == "admin:pods":
            result = await backend_client.get_admin_runpod_pods(telegram_id=telegram_id)
            await _reply_or_edit(callback, _format_pods(result), _admin_keyboard())
        elif action == "admin:waiting_jobs":
            result = await backend_client.get_admin_waiting_pod_jobs(telegram_id=telegram_id)
            await _reply_or_edit(callback, _format_waiting_jobs(result), _admin_keyboard())
        elif action == "admin:cleanup_idle":
            result = await backend_client.cleanup_idle_pods(telegram_id=telegram_id)
            await _reply_or_edit(
                callback,
                "✅ Cleanup idle pods завершён.\n\n"
                f"Terminated: {result.get('terminated_count', 0)}",
                _admin_keyboard(),
            )
        elif action == "admin:check_health":
            result = await backend_client.check_runpod_health(telegram_id=telegram_id)
            await _reply_or_edit(
                callback,
                "✅ Healthcheck завершён.\n\n"
                f"Checked: {result.get('checked', 0)}\n"
                f"Healthy: {result.get('healthy', 0)}\n"
                f"Unhealthy: {result.get('unhealthy', 0)}",
                _admin_keyboard(),
            )
        elif action == "admin:web":
            await _reply_or_edit(
                callback,
                f"🌐 Web admin:\n{get_settings().backend_public_url.rstrip('/')}/admin",
                _admin_keyboard(),
            )
        else:
            await callback.answer("Неизвестное действие.", show_alert=True)
            return
        await callback.answer()
    except (BackendUnavailableError, BackendClientError) as exc:
        logger.warning("Telegram admin action failed action=%s error=%s", action, exc)
        await callback.answer("Admin action failed.", show_alert=True)


async def _send_admin_overview(
    message: Message,
    telegram_id: int,
    *,
    edit: bool = False,
) -> None:
    try:
        overview = await backend_client.get_admin_overview(telegram_id=telegram_id)
        pods = await backend_client.get_admin_runpod_pods(telegram_id=telegram_id)
    except (BackendUnavailableError, BackendClientError):
        logger.warning("Telegram admin overview failed", exc_info=True)
        await message.answer("Admin backend недоступен.")
        return

    text = _format_overview(overview, pods)
    if edit:
        await message.edit_text(text, reply_markup=_admin_keyboard())
    else:
        await message.answer(text, reply_markup=_admin_keyboard())


def _is_admin(telegram_id: int) -> bool:
    return telegram_id in get_settings().admin_telegram_id_set


def _admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Sync RunPod pods", callback_data="admin:sync_pods")],
            [InlineKeyboardButton(text="🩺 Check pod health", callback_data="admin:check_health")],
            [
                InlineKeyboardButton(
                    text="🚀 Retry waiting jobs",
                    callback_data="admin:retry_waiting",
                )
            ],
            [
                InlineKeyboardButton(text="📋 Waiting jobs", callback_data="admin:waiting_jobs"),
                InlineKeyboardButton(text="🖥 Pod’ы", callback_data="admin:pods"),
            ],
            [InlineKeyboardButton(text="🧹 Cleanup idle pods", callback_data="admin:cleanup_idle")],
            [InlineKeyboardButton(text="🌐 Web admin", callback_data="admin:web")],
        ]
    )


def _post_sync_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Retry waiting jobs",
                    callback_data="admin:retry_waiting",
                )
            ],
            [InlineKeyboardButton(text="🖥 Pod’ы", callback_data="admin:pods")],
            [InlineKeyboardButton(text="↩️ Назад", callback_data="admin:overview")],
        ]
    )


def _format_overview(overview: dict[str, Any], pods: dict[str, Any]) -> str:
    jobs = dict(overview.get("jobs") or {})
    plan = _queue_plan(overview) or _queue_plan(pods)
    pod_items = list(pods.get("items") or [])
    healthy = _plan_int(
        plan,
        "healthy_pods_count",
        sum(1 for pod in pod_items if pod.get("health_status") == "healthy"),
    )
    busy = _plan_int(
        plan,
        "busy_pods_count",
        sum(1 for pod in pod_items if pod.get("status") == "busy"),
    )
    idle = _plan_int(
        plan,
        "idle_healthy_pods_count",
        sum(
            1
            for pod in pod_items
            if pod.get("status") in {"idle", "ready"} and pod.get("health_status") == "healthy"
        ),
    )
    active = _plan_int(
        plan,
        "active_pods_count",
        int(dict(overview.get("runpod") or {}).get("active_pods") or 0),
    )
    waiting_minutes = _plan_minutes(plan, "total_waiting_audio_minutes")
    target_min = _plan_minutes(plan, "target_minutes_per_pod_min", default="5.0")
    target_max = _plan_minutes(plan, "target_minutes_per_pod_max", default="6.0")
    recommended = _plan_int(plan, "recommended_additional_pods", 0)
    return (
        "⚙️ Админ-панель\n\n"
        "Очередь:\n"
        f"Ожидают pod: {jobs.get('waiting_for_pod', 0)}\n"
        f"Ожидающая длительность: {waiting_minutes} мин\n"
        f"Queued: {jobs.get('queued', 0)}\n"
        f"Generating: {jobs.get('generating', 0)}\n"
        f"Failed 24h: {jobs.get('failed_24h', 0)}\n\n"
        "RunPod:\n"
        f"Активных pod’ов: {active}\n"
        f"Healthy ComfyUI: {healthy}\n"
        f"Busy: {busy}\n"
        f"Idle: {idle}\n\n"
        "План:\n"
        f"Цель: {target_min}–{target_max} мин / pod\n"
        f"Рекомендовано добавить: {recommended} pod’ов"
    )


def _format_sync_result(result: dict[str, Any]) -> str:
    skipped = list(result.get("skipped") or [])
    skipped_text = "\n".join(
        f"- {escape(str(item.get('pod_id'))[:10])}: {escape(str(item.get('reason')))}"
        for item in skipped[:5]
    )
    if not skipped_text:
        skipped_text = "—"
    return (
        "✅ Sync завершён\n\n"
        f"Найдено: {result.get('found', 0)}\n"
        f"Добавлено: {result.get('registered', 0)}\n"
        f"Обновлено: {result.get('updated', 0)}\n"
        f"Healthy: {result.get('healthy', 0)}\n"
        f"Skipped:\n{skipped_text}"
    )


def _format_pods(result: dict[str, Any]) -> str:
    items = list(result.get("items") or [])
    if not items:
        return "🖥 Pod’ы\n\nНет pod’ов в базе."
    lines = ["🖥 Pod’ы"]
    for pod in items[:10]:
        job_id = pod.get("current_job_id") or pod.get("active_job_id") or "—"
        lines.append(
            "\n"
            f"{escape(str(pod.get('runpod_pod_id') or '')[:10])}\n"
            f"GPU: {escape(str(pod.get('gpu_type') or 'unknown'))}\n"
            f"Status: {escape(str(pod.get('status') or 'unknown'))}\n"
            f"Health: {escape(str(pod.get('health_status') or 'unknown'))}\n"
            f"Job: {escape(str(job_id)[:8])}\n"
            f"Last busy: {escape(str(pod.get('last_busy_at') or '—'))}"
        )
    return "\n".join(lines)


def _format_waiting_jobs(result: dict[str, Any]) -> str:
    items = list(result.get("items") or [])
    plan = _queue_plan(result)
    total_jobs = _plan_int(
        plan,
        "waiting_for_pod_jobs_count",
        int(result.get("total_waiting_jobs") or len(items)),
    )
    total_minutes = _plan_minutes(
        plan,
        "total_waiting_audio_minutes",
        default=str(result.get("total_waiting_audio_minutes") or "0.0"),
    )
    oldest_wait = _plan_int(
        plan,
        "oldest_wait_minutes",
        int(result.get("oldest_wait_minutes") or 0),
    )
    recommended = _plan_int(
        plan,
        "recommended_additional_pods",
        int(result.get("recommended_additional_pods") or 0),
    )
    if not items:
        return (
            "📋 Waiting jobs\n\n"
            "Нет задач в waiting_for_pod.\n\n"
            f"Всего waiting: {total_jobs}\n"
            f"Суммарная длительность: {total_minutes} мин\n"
            f"Oldest wait: {oldest_wait} мин\n"
            f"Рекомендовано добавить: {recommended} pod’ов"
        )
    lines = [
        "📋 Waiting jobs",
        "",
        f"Всего waiting: {total_jobs}",
        f"Суммарная длительность: {total_minutes} мин",
        f"Oldest wait: {oldest_wait} мин",
        f"Рекомендовано добавить: {recommended} pod’ов",
    ]
    for job in items[:10]:
        lines.append(
            "\n"
            f"{escape(str(job.get('short_id') or '')[:8])}\n"
            f"User: {job.get('telegram_id')}\n"
            f"Duration: {job.get('audio_duration_seconds') or '—'} sec\n"
            f"Waiting: {job.get('waiting_minutes', 0)} min\n"
            f"Price: ${job.get('price_usd') or '—'}"
        )
    return "\n".join(lines)


async def _reply_or_edit(
    callback: CallbackQuery,
    text: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    if callback.message is None:
        return
    await callback.message.edit_text(text, reply_markup=keyboard)


def _queue_plan(data: dict[str, Any]) -> dict[str, Any] | None:
    plan = data.get("queue_load_plan")
    return plan if isinstance(plan, dict) else None


def _plan_int(plan: dict[str, Any] | None, key: str, default: int) -> int:
    if not plan:
        return default
    try:
        return int(plan.get(key) or default)
    except (TypeError, ValueError):
        return default


def _plan_minutes(
    plan: dict[str, Any] | None,
    key: str,
    *,
    default: str = "0.0",
) -> str:
    if not plan:
        return default
    try:
        return f"{float(plan.get(key) or 0):.1f}"
    except (TypeError, ValueError):
        return default
