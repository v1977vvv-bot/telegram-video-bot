from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.app.handlers.admin import router as admin_router
from shared.app.config import get_settings
from shared.app.logging import configure_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.admin_bot_token_is_configured:
        logger.warning("ADMIN_BOT_TOKEN is not configured; admin bot polling is paused")
        await asyncio.Event().wait()
        return

    bot = Bot(
        token=settings.admin_bot_token.strip(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(admin_router)

    logger.info("Starting Telegram admin bot polling")
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
