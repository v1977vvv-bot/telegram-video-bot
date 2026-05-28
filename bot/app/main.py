from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.app.handlers.admin import router as admin_router
from bot.app.handlers.batch_generation import router as batch_generation_router
from bot.app.handlers.debug import router as debug_router
from bot.app.handlers.generation import router as generation_router
from bot.app.handlers.menu import router as menu_router
from bot.app.handlers.start import router as start_router
from shared.app.config import get_settings
from shared.app.logging import configure_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.telegram_token_is_configured:
        logger.warning("TELEGRAM_BOT_TOKEN is not configured; bot polling is paused")
        await asyncio.Event().wait()
        return

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(start_router)
    if settings.admin_bot_token_is_configured:
        logger.info("ADMIN_BOT_TOKEN is configured; /admin is handled by admin bot")
    else:
        logger.info("ADMIN_BOT_TOKEN is not configured; enabling /admin fallback in user bot")
        dispatcher.include_router(admin_router)
    dispatcher.include_router(debug_router)
    dispatcher.include_router(batch_generation_router)
    dispatcher.include_router(generation_router)
    dispatcher.include_router(menu_router)

    logger.info("Starting Telegram bot polling")
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
