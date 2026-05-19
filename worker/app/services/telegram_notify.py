from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from html import escape
from typing import Any
from uuid import UUID

import httpx

from shared.app.config import Settings, get_settings

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)


@dataclass(frozen=True, slots=True)
class GenerationNotification:
    telegram_id: int
    job_id: UUID
    display_name: str | None
    audio_duration_seconds: Decimal | None
    price_usd: Decimal | None
    segments_count: int | None = None
    result_url: str | None = None
    error_message: str | None = None
    funds_returned: bool = True


class TelegramNotifyService:
    """Minimal sync Telegram Bot API client for worker notifications."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._token = self._settings.telegram_bot_token.strip()

    def send_generation_completed(self, notification: GenerationNotification) -> bool:
        text = (
            "✅ Видео готово.\n\n"
            f"Видео: {safe_html(_display_name(notification), max_len=90)}\n"
            f"Списано: {_money(notification.price_usd)}"
        )
        reply_markup = None
        if notification.result_url:
            reply_markup = {
                "inline_keyboard": [
                    [{"text": "⬇️ Скачать результат", "url": notification.result_url}]
                ]
            }
        return self._send_message(notification.telegram_id, text, reply_markup=reply_markup)

    def send_generation_failed(self, notification: GenerationNotification) -> bool:
        if notification.funds_returned:
            text = (
                "⚠️ Не удалось завершить генерацию.\n\n"
                f"Видео: {safe_html(_display_name(notification), max_len=90)}\n\n"
                "Средства возвращены на баланс.\n"
                "Попробуйте ещё раз или загрузите другое фото/аудио."
            )
        else:
            text = (
                "⚠️ Генерацию не удалось запустить.\n\n"
                f"Видео: {safe_html(_display_name(notification), max_len=90)}\n\n"
                "Средства не были списаны.\n"
                "Попробуйте позже."
            )
        return self._send_message(notification.telegram_id, text)

    def send_generation_waiting_for_gpu(self, notification: GenerationNotification) -> bool:
        return self._send_waiting_message(notification)

    def send_generation_waiting_for_pod(self, notification: GenerationNotification) -> bool:
        text = (
            "⏳ Серверы сейчас заняты.\n\n"
            f"Видео: {safe_html(_display_name(notification), max_len=90)}\n\n"
            "Задача ожидает свободный сервер.\n"
            "Средства заморожены, но не списаны.\n\n"
            "Если генерацию не удастся запустить, сумма вернётся на баланс."
        )
        return self._send_message(notification.telegram_id, text)

    def _send_waiting_message(self, notification: GenerationNotification) -> bool:
        text = (
            "⏳ Серверы сейчас заняты.\n\n"
            f"Видео: {safe_html(_display_name(notification), max_len=90)}\n\n"
            "Задача ожидает свободный сервер.\n"
            "Средства заморожены, но не списаны.\n\n"
            "Если генерацию не удастся запустить, сумма вернётся на баланс."
        )
        return self._send_message(notification.telegram_id, text)

    def send_debug_message(self, *, telegram_id: int, message: str) -> bool:
        return self._send_message(telegram_id, safe_html(message, max_len=1000))

    def _send_message(
        self,
        telegram_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        if not self._settings.telegram_token_is_configured:
            logger.warning("Telegram notification skipped because bot token is not configured")
            return False

        payload: dict[str, Any] = {
            "chat_id": telegram_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json=payload,
                timeout=httpx.Timeout(15.0, connect=5.0),
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            logger.warning(
                "Telegram notification failed telegram_id=%s error=%s",
                telegram_id,
                exc.__class__.__name__,
            )
            return False
        except ValueError:
            logger.warning(
                "Telegram notification returned invalid JSON telegram_id=%s",
                telegram_id,
            )
            return False

        ok = bool(data.get("ok"))
        if not ok:
            logger.warning("Telegram notification was rejected telegram_id=%s", telegram_id)
        return ok


def safe_html(value: object | None, max_len: int | None = None) -> str:
    if value is None:
        return ""

    text = _strip_control_chars(str(value))
    if max_len is not None and len(text) > max_len:
        text = f"{text[:max_len]}..."
    return escape(text, quote=False)


def _strip_control_chars(value: str) -> str:
    return "".join(
        char
        for char in value
        if char in {"\n", "\r", "\t"} or not (ord(char) < 32 or ord(char) == 127)
    )


def _display_name(notification: GenerationNotification) -> str:
    return notification.display_name or "Видео"


def _duration(value: Decimal | None) -> str:
    if value is None:
        return "неизвестно"
    return f"{value.quantize(Decimal('0.1'))} сек"


def _money(value: Decimal | None) -> str:
    if value is None:
        return "$0.00"
    return f"${value.quantize(Decimal('0.01'))}"
