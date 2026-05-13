from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

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


def top_up_amounts_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="$5", callback_data="top_up_stub:5"),
            InlineKeyboardButton(text="$10", callback_data="top_up_stub:10"),
        ],
        [
            InlineKeyboardButton(text="$20", callback_data="top_up_stub:20"),
            InlineKeyboardButton(text="$50", callback_data="top_up_stub:50"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


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
