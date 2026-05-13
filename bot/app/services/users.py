from __future__ import annotations

from aiogram.types import User as TelegramUser

from bot.app.services.backend_client import BotBackendClient, TelegramUserDto


class BotUserService:
    def __init__(self, backend_client: BotBackendClient | None = None) -> None:
        self._backend_client = backend_client or BotBackendClient()

    async def ensure_user(self, telegram_user: TelegramUser) -> TelegramUserDto:
        return await self._backend_client.upsert_telegram_user(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            first_name=telegram_user.first_name,
            last_name=telegram_user.last_name,
            language_code=telegram_user.language_code,
        )
