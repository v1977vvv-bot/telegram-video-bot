from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.app.keyboards.main_menu import MENU_BUTTONS, top_up_amounts_keyboard
from bot.app.services.backend_client import (
    BackendClientError,
    BackendNotFoundError,
    BackendUnavailableError,
    BotBackendClient,
    GenerationHistoryItemDto,
)
from bot.app.utils.text import safe_html
from shared.app.config import get_settings

router = Router()
backend_client = BotBackendClient()
TELEGRAM_MESSAGE_SAFE_LIMIT = 3900
TELEGRAM_GENERATION_HISTORY_LIMIT = 5
MENU_BUTTONS_WITHOUT_GENERATION = tuple(
    item for item in MENU_BUTTONS if item != "Сгенерировать видео"
)
STATUS_DISPLAY = {
    "draft": "📝 черновик",
    "queued": "⏳ в очереди",
    "waiting_for_gpu": "⏳ ожидает GPU",
    "generating": "🎬 генерируется",
    "completed": "✅ готово",
    "failed": "❌ ошибка",
    "cancelled": "🚫 отменено",
}


@router.message(F.text.in_(MENU_BUTTONS_WITHOUT_GENERATION))
async def handle_menu_button(message: Message) -> None:
    if message.text is None:
        return

    if message.text == "Статистика":
        await handle_statistics(message)
        return
    if message.text == "Мои генерации":
        await handle_generations(message)
        return
    if message.text == "Пополнить баланс":
        await handle_top_up(message)
        return
    if message.text == "Помощь":
        await handle_help(message)
        return
    if message.text == "Поддержка":
        await handle_support(message)
        return


async def handle_statistics(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Сервис временно недоступен. Попробуйте позже.")
        return
    try:
        statistics = await backend_client.get_statistics(message.from_user.id)
    except BackendNotFoundError:
        await message.answer("Пользователь не найден. Нажмите /start.")
        return
    except (BackendUnavailableError, BackendClientError):
        await message.answer("Сервис временно недоступен. Попробуйте позже.")
        return

    await message.answer(
        "📊 Статистика\n\n"
        f"Баланс: ${_money(statistics.balance.available_usd)}\n"
        f"Заморожено: ${_money(statistics.balance.frozen_usd)}\n\n"
        "Генерации:\n"
        f"Сегодня: {statistics.generations.today}\n"
        f"За месяц: {statistics.generations.month}\n"
        f"За всё время: {statistics.generations.all_time}\n\n"
        "Потрачено:\n"
        f"Сегодня: ${_money(statistics.spending.today_usd)}\n"
        f"За месяц: ${_money(statistics.spending.month_usd)}\n"
        f"За всё время: ${_money(statistics.spending.all_time_usd)}"
    )


async def handle_generations(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Сервис временно недоступен. Попробуйте позже.")
        return
    try:
        generations = await backend_client.get_generations(message.from_user.id, limit=10)
    except BackendNotFoundError:
        await message.answer("Пользователь не найден. Нажмите /start.")
        return
    except (BackendUnavailableError, BackendClientError):
        await message.answer("Сервис временно недоступен. Попробуйте позже.")
        return

    if not generations:
        await message.answer("У вас пока нет генераций.")
        return

    visible_generations = generations[:TELEGRAM_GENERATION_HISTORY_LIMIT]
    parts = [
        _format_generation_item(index, item)
        for index, item in enumerate(visible_generations, start=1)
    ]
    if len("\n\n".join(parts)) > TELEGRAM_MESSAGE_SAFE_LIMIT:
        visible_generations = visible_generations[:TELEGRAM_GENERATION_HISTORY_LIMIT]
        parts = _fit_history_parts(visible_generations)

    if len(generations) > len(visible_generations):
        parts.append("Показаны последние 5 записей.")

    download_buttons = [
        [InlineKeyboardButton(text=f"Скачать результат #{index}", url=item.result_url)]
        for index, item in enumerate(visible_generations, start=1)
        if item.status == "completed" and item.result_url
    ]
    reply_markup = (
        InlineKeyboardMarkup(inline_keyboard=download_buttons) if download_buttons else None
    )
    await message.answer("\n\n".join(parts), reply_markup=reply_markup)


async def handle_top_up(message: Message) -> None:
    await message.answer(
        "💳 Пополнение баланса\n\n"
        "Скоро здесь будет пополнение через USDT / Cryptomus.\n\n"
        "Минимальная сумма пополнения будет настроена позже.",
        reply_markup=top_up_amounts_keyboard(),
    )


async def handle_help(message: Message) -> None:
    await message.answer(
        "❓ Помощь\n\n"
        "Что умеет бот?\n"
        "Бот создаёт видео на основе фото и аудио.\n\n"
        "Какие форматы будут доступны?\n"
        "854×480, 480×480, 480×854.\n\n"
        "Как считается стоимость?\n"
        "По длительности аудио в секундах.\n\n"
        "Как долго хранятся файлы?\n"
        "На MVP результат будет храниться 48 часов."
    )


async def handle_support(message: Message) -> None:
    support_username = safe_html(get_settings().support_telegram_username.lstrip("@"), max_len=64)
    await message.answer(f"🛠 Поддержка\n\nЕсли возникла проблема, напишите: @{support_username}")


async def handle_generate_video(message: Message) -> None:
    await message.answer(
        "🎬 Генерация видео\n\n"
        "На следующем этапе здесь появится загрузка фото и аудио.\n"
        "Планируемый процесс:\n"
        "1. Загрузить фото файлом\n"
        "2. Загрузить аудио\n"
        "3. Выбрать формат\n"
        "4. Подтвердить стоимость\n"
        "5. Поставить задачу в очередь"
    )


@router.callback_query(F.data.startswith("top_up_stub:"))
async def handle_top_up_amount(callback: CallbackQuery) -> None:
    await callback.answer(
        "Пополнение через Cryptomus будет подключено на следующем этапе.",
        show_alert=True,
    )


def _format_generation_item(index: int, item: GenerationHistoryItemDto) -> str:
    duration = (
        f"{item.audio_duration_seconds.quantize(Decimal('0.001'))} сек"
        if item.audio_duration_seconds is not None
        else "неизвестно"
    )
    price = f"${_money(item.price_usd)}" if item.price_usd is not None else "не рассчитана"
    status = safe_html(STATUS_DISPLAY.get(item.status, item.status), max_len=80)
    created_at = safe_html(item.created_at, max_len=80)
    result = (
        f"#{index}\n"
        f"Статус: {status}\n"
        f"Формат: {item.width}×{item.height}\n"
        f"FPS: {item.fps}\n"
        f"Длительность: {duration}\n"
        f"Сегментов: {item.segments_count}\n"
        f"Стоимость: {price}\n"
        f"Дата: {created_at}"
    )
    if item.status == "completed" and item.result_url:
        result += "\nРезультат: доступен для скачивания"
    elif item.status == "completed" and item.mock_result_message:
        result += f"\nРезультат: {safe_html(item.mock_result_message, max_len=300)}"
    elif item.status == "failed" and item.error_message:
        result += f"\nОшибка: {safe_html(item.error_message, max_len=300)}"
    elif item.status == "waiting_for_gpu":
        result += (
            "\nРезультат: ⏳ Ожидаем доступный GPU. " "Задача в очереди, средства пока не списаны."
        )
    elif item.status in {"queued", "generating"}:
        result += "\nРезультат: ещё не готов"
    return result


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.0001'))}"


def _fit_history_parts(
    generations: list[GenerationHistoryItemDto],
) -> list[str]:
    visible = generations
    while visible:
        parts = [
            _format_generation_item(index, item) for index, item in enumerate(visible, start=1)
        ]
        if len("\n\n".join(parts)) <= TELEGRAM_MESSAGE_SAFE_LIMIT:
            return parts
        visible = visible[:-1]
    return ["История генераций слишком длинная для отображения."]
