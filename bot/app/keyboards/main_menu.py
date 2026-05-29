from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from shared.app.config import get_settings

MENU_BUTTONS = (
    "🎬 Создать видео",
    "📦 Пакетная генерация",
    "💰 Баланс",
    "➕ Пополнить",
    "📊 Статистика",
    "🗂 Мои видео",
    "ℹ️ Как это работает",
    "🆘 Поддержка",
)
LEGACY_MENU_BUTTONS = (
    "Статистика",
    "Сгенерировать видео",
    "Мои генерации",
    "Пополнить баланс",
    "Помощь",
    "Поддержка",
)
CREATE_VIDEO_BUTTONS = {"🎬 Создать видео", "Сгенерировать видео"}
BATCH_GENERATION_BUTTONS = {"📦 Пакетная генерация"}
CANCEL_BUTTONS = {"❌ Отмена", "Отмена"}


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="🎬 Создать видео")],
        [KeyboardButton(text="📦 Пакетная генерация")],
        [KeyboardButton(text="💰 Баланс"), KeyboardButton(text="➕ Пополнить")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="🗂 Мои видео")],
        [KeyboardButton(text="ℹ️ Как это работает"), KeyboardButton(text="🆘 Поддержка")],
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


def _format_money_label(amount: Decimal) -> str:
    text = (
        format(amount.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP), "f")
        .rstrip("0")
        .rstrip(".")
    )
    if "." not in text:
        text = f"{text}.00"
    elif len(text.rsplit(".", maxsplit=1)[1]) == 1:
        text = f"{text}0"
    return f"${text}"


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def generation_quality_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="480p", callback_data="generation_quality:480p"),
            InlineKeyboardButton(text="720p", callback_data="generation_quality:720p"),
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="generation_cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def batch_generation_quality_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="480p", callback_data="batch_quality:480p"),
            InlineKeyboardButton(text="720p", callback_data="batch_quality:720p"),
        ],
        [InlineKeyboardButton(text="🌐 Загрузить большой архив", callback_data="batch_web_upload")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="batch_cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def batch_web_upload_keyboard(web_app_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌐 Загрузить большой архив",
                    web_app=WebAppInfo(url=web_app_url),
                )
            ]
        ]
    )


def batch_generation_confirm_keyboard(
    *,
    quality_profile: str,
    price_usd: Decimal,
) -> InlineKeyboardMarkup:
    amount = _format_money_label(price_usd)
    keyboard = [
        [
            InlineKeyboardButton(
                text=f"✅ Запустить пакет {quality_profile} — {amount}",
                callback_data="batch_confirm",
            )
        ],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="batch_cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def generation_formats_keyboard(quality_profile: str = "480p") -> InlineKeyboardMarkup:
    if quality_profile == "720p":
        keyboard = [
            [
                InlineKeyboardButton(
                    text="↔️ Горизонтальное 1280×720",
                    callback_data="generation_format:1280:720",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◼️ Квадратное 720×720",
                    callback_data="generation_format:720:720",
                )
            ],
            [
                InlineKeyboardButton(
                    text="↕️ Вертикальное 720×1280",
                    callback_data="generation_format:720:1280",
                )
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="generation_cancel")],
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton(
                    text="↔️ Горизонтальное 854×480",
                    callback_data="generation_format:854:480",
                )
            ],
            [
                InlineKeyboardButton(
                    text="◼️ Квадратное 480×480",
                    callback_data="generation_format:480:480",
                )
            ],
            [
                InlineKeyboardButton(
                    text="↕️ Вертикальное 480×854",
                    callback_data="generation_format:480:854",
                )
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="generation_cancel")],
        ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def generation_confirm_keyboard(
    *,
    quality_profile: str | None = None,
    price_usd: Decimal | None = None,
) -> InlineKeyboardMarkup:
    label = "✅ Запустить"
    if quality_profile and price_usd is not None:
        amount = _format_money_label(price_usd)
        label = f"✅ Запустить {quality_profile} — {amount}"
    keyboard = [
        [InlineKeyboardButton(text=label, callback_data="generation_confirm")],
        [InlineKeyboardButton(text="↩️ Назад", callback_data="generation_cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
