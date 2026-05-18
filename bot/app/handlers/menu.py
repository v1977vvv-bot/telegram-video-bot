from __future__ import annotations

from decimal import Decimal, InvalidOperation

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
    "waiting_for_pod": "⏳ ожидает свободный GPU",
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

    balance_lines = (
        "📊 Статистика\n\n"
        f"Баланс: ${_money(statistics.balance.available_usd)}\n"
        f"Заморожено: ${_money(statistics.balance.frozen_usd)}"
    )
    if statistics.business_account is not None:
        business_name = safe_html(statistics.business_account.name, max_len=80)
        balance_lines += (
            "\n\n"
            f"🏢 Баланс компании: ${_money(statistics.business_account.available_usd)}\n"
            f"Заморожено компании: ${_money(statistics.business_account.frozen_usd)}\n"
            f"Компания: {business_name}\n"
            "Ваши генерации оплачиваются с баланса компании."
        )

    await message.answer(
        f"{balance_lines}\n\n"
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
    try:
        packages = await backend_client.get_payment_packages()
        package_buttons = [(package.display_label, package.amount_usd) for package in packages]
    except (BackendUnavailableError, BackendClientError):
        package_buttons = None

    provider = get_settings().payment_provider_normalized
    if provider == "manual":
        text = (
            "💳 Пополнение баланса\n\n"
            "Автоматическое пополнение временно недоступно.\n\n"
            "Для пополнения баланса напишите в поддержку. После оплаты администратор "
            "зачислит средства вручную.\n\n"
            "Для бизнес-пакетов и прямой оплаты поддержке напишите в поддержку."
        )
        package_buttons = None
    else:
        provider_name = _payment_provider_name(provider)
        text = (
            "💳 Пополнение баланса\n\n"
            "Выберите пакет пополнения.\n"
            f"Оплата проходит через {provider_name} в USDT.\n"
            "Баланс в боте отображается в USD.\n\n"
            "После подтверждения платежа баланс пополнится автоматически.\n\n"
            "Для бизнес-пакетов или прямой оплаты поддержке напишите в поддержку — "
            "мы можем зачислить баланс вручную."
        )

    await message.answer(text, reply_markup=top_up_amounts_keyboard(package_buttons))


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


@router.callback_query(F.data.startswith("top_up_package:"))
async def handle_top_up_amount(callback: CallbackQuery) -> None:
    if callback.from_user is None or callback.data is None or callback.message is None:
        return

    try:
        amount = Decimal(callback.data.split(":", maxsplit=1)[1])
    except (InvalidOperation, IndexError):
        await callback.answer("Неверный пакет пополнения.", show_alert=True)
        return

    try:
        invoice = await backend_client.create_payment_invoice(
            telegram_id=callback.from_user.id,
            amount_usd=amount,
        )
    except (BackendUnavailableError, BackendClientError) as exc:
        await callback.answer(safe_html(exc, max_len=200), show_alert=True)
        return

    provider_name = _payment_provider_name(invoice.provider)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"Оплатить через {provider_name}", url=invoice.payment_url)]
        ]
    )
    await callback.message.answer(
        f"Счёт на ${_money(invoice.amount_usd)} создан.\n"
        f"Оплатите его в {safe_html(invoice.provider_currency, max_len=16)} "
        f"через {safe_html(provider_name, max_len=32)}.\n"
        "После подтверждения баланс пополнится автоматически.",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("top_up_stub:"))
async def handle_old_top_up_amount(callback: CallbackQuery) -> None:
    await callback.answer(
        "Сейчас доступны только фиксированные пакеты пополнения: $10, $25, $50, $100.",
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
            "\nРезультат: ⏳ Ожидаем доступный GPU. " "Средства зарезервированы, но не списаны."
        )
    elif item.status == "waiting_for_pod":
        result += (
            "\nРезультат: ⏳ Задача ожидает свободный GPU. "
            "Средства зарезервированы, но не списаны."
        )
    elif item.status in {"queued", "generating"}:
        result += "\nРезультат: ещё не готов"
    return result


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.0001'))}"


def _payment_provider_name(provider: str) -> str:
    if provider == "cryptobot":
        return "CryptoBot"
    if provider == "cryptomus":
        return "Cryptomus"
    return "поддержку"


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
