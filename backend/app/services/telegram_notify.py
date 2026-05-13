from __future__ import annotations

import logging
from html import escape
from typing import Any

import httpx

from shared.app.config import Settings, get_settings
from shared.app.exceptions import AppError

logging.getLogger("httpx").setLevel(logging.WARNING)


class TelegramNotificationService:
    """Async Telegram Bot API client for backend debug/internal notifications."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._token = self._settings.telegram_bot_token.strip()

    async def send_message(self, *, telegram_id: int, message: str) -> bool:
        if not self._settings.telegram_token_is_configured:
            raise AppError("Telegram bot token is not configured", code="telegram_not_configured")

        payload: dict[str, Any] = {
            "chat_id": telegram_id,
            "text": safe_html(message, max_len=1000),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=5.0),
                follow_redirects=True,
            ) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{self._token}/sendMessage",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise AppError(
                "Telegram notification failed",
                code="telegram_notification_failed",
                status_code=502,
            ) from exc
        except ValueError as exc:
            raise AppError(
                "Telegram returned invalid JSON",
                code="telegram_invalid_response",
                status_code=502,
            ) from exc

        return bool(data.get("ok"))


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
