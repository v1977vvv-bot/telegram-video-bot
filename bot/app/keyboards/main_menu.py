from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from shared.app.config import get_settings

MENU_BUTTONS = (
    "Статистика",
    "Сгенерировать видео",
    "Мои генерации",
    "Пополнить баланс",
    "Помощь",
    "Поддержка",
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="Сгенерировать видео")],
        [KeyboardButton(text="Мои генерации"), KeyboardButton(text="Статистика")],
        [KeyboardButton(text="Пополнить баланс")],
        [KeyboardButton(text="Помощь"), KeyboardButton(text="Поддержка")],
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def top_up_amounts_keyboard(
    packages: Sequence[tuple[str, Decimal]] | None = None,
) -> InlineKeyboardMarkup:
    resolved_packages = packages or [
        (_format_amount_label(amount), amount)
        for amount in get_settings().payment_package_amounts_usd
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(resolved_packages), 2):
        row = [
            InlineKeyboardButton(
                text=label,
                callback_data=f"top_up_package:{amount}",
            )
            for label, amount in resolved_packages[index : index + 2]
        ]
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_amount_label(amount: Decimal) -> str:
    amount = amount.quantize(Decimal("0.01"))
    if amount == amount.to_integral_value():
        return f"${int(amount)}"
    return f"${amount}"


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def generation_formats_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                text="Горизонтальное 854×480",
                callback_data="generation_format:854:480",
            )
        ],
        [
            InlineKeyboardButton(
                text="Квадратное 480×480",
                callback_data="generation_format:480:480",
            )
        ],
        [
            InlineKeyboardButton(
                text="Вертикальное 480×854",
                callback_data="generation_format:480:854",
            )
        ],
        [InlineKeyboardButton(text="Отмена", callback_data="generation_cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def generation_confirm_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text="Подтвердить", callback_data="generation_confirm")],
        [InlineKeyboardButton(text="Отмена", callback_data="generation_cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
