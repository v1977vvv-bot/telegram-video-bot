from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.app.keyboards.main_menu import main_menu_keyboard
from bot.app.services.backend_client import BackendClientError, BackendUnavailableError
from bot.app.services.users import BotUserService

logger = logging.getLogger(__name__)
router = Router()
user_service = BotUserService()


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    if message.from_user is not None:
        try:
            user = await user_service.ensure_user(message.from_user)
        except (BackendUnavailableError, BackendClientError):
            logger.exception("Failed to upsert Telegram user")
            await message.answer("Сервис временно недоступен. Попробуйте позже.")
            return

        if user.is_banned:
            await message.answer("Доступ ограничен.")
            return

        logger.info(
            "Telegram user ensured user_id=%s telegram_id=%s", user.id, message.from_user.id
        )

    await message.answer(
        "Привет! Я помогу сгенерировать видео по фото и аудио. "
        "Сейчас доступен базовый каркас, а генерация будет подключена на следующих этапах.",
        reply_markup=main_menu_keyboard(),
    )
