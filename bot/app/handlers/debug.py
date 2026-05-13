from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.app.services.backend_client import (
    BackendClientError,
    BackendUnavailableError,
    BotBackendClient,
)
from bot.app.utils.text import safe_html
from shared.app.config import get_settings

router = Router()
backend_client = BotBackendClient()


@router.message(Command("debug_add_balance"))
async def debug_add_balance(message: Message) -> None:
    if message.from_user is None or message.from_user.id not in get_settings().debug_admin_ids:
        await message.answer("Команда недоступна.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Использование: /debug_add_balance 10")
        return

    try:
        amount = Decimal(parts[1])
    except InvalidOperation:
        await message.answer("Сумма должна быть числом.")
        return

    try:
        balance = await backend_client.debug_add_balance(
            telegram_id=message.from_user.id,
            amount_usd=amount,
            reason="telegram debug command",
        )
    except (BackendUnavailableError, BackendClientError) as exc:
        await message.answer(f"Не удалось пополнить debug-баланс: {safe_html(exc, max_len=300)}")
        return

    await message.answer(
        "Debug-баланс обновлён.\n"
        f"Баланс: ${balance.available_usd}\n"
        f"Заморожено: ${balance.frozen_usd}"
    )
