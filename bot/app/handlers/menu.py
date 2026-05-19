from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.app.keyboards.main_menu import LEGACY_MENU_BUTTONS, MENU_BUTTONS, top_up_amounts_keyboard
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
    item
    for item in (*MENU_BUTTONS, *LEGACY_MENU_BUTTONS)
    if item not in {"🎬 Создать видео", "Сгенерировать видео"}
)
STATUS_DISPLAY = {
    "draft": "📝 черновик",
    "queued": "⏳ в очереди",
    "waiting_for_gpu": "⏳ ожидает сервер",
    "waiting_for_pod": "⏳ ожидает сервер",
    "generating": "🎬 генерируется",
    "completed": "✅ готово",
    "failed": "❌ ошибка",
    "cancelled": "🚫 отменено",
}


@router.message(F.text.in_(MENU_BUTTONS_WITHOUT_GENERATION))
async def handle_menu_button(message: Message) -> None:
    if message.text is None:
        return

    if message.text == "💰 Баланс":
        await handle_balance(message)
        return
    if message.text in {"📊 Статистика", "Статистика"}:
        await handle_statistics(message)
        return
    if message.text in {"🗂 Мои видео", "Мои генерации"}:
        await handle_generations(message)
        return
    if message.text in {"➕ Пополнить", "Пополнить баланс"}:
        await handle_top_up(message)
        return
    if message.text in {"ℹ️ Как это работает", "Помощь"}:
        await handle_help(message)
        return
    if message.text in {"🆘 Поддержка", "Поддержка"}:
        await handle_support(message)
        return


async def handle_balance(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Сервис временно недоступен.\n\nПопробуйте ещё раз немного позже.")
        return
    try:
        statistics = await backend_client.get_statistics(message.from_user.id)
    except BackendNotFoundError:
        await message.answer("Я не понял команду.\n\nВыберите действие в меню или нажмите /start.")
        return
    except (BackendUnavailableError, BackendClientError):
        await message.answer("Сервис временно недоступен.\n\nПопробуйте ещё раз немного позже.")
        return

    if statistics.business_account is not None:
        business_name = safe_html(statistics.business_account.name, max_len=80)
        await message.answer(
            f"🏢 Компания: {business_name}\n\n"
            f"Доступно: {_money(statistics.business_account.available_usd)}\n"
            f"Заморожено: {_money(statistics.business_account.frozen_usd)}\n\n"
            "Ваши генерации оплачиваются с баланса компании."
        )
        return

    await message.answer(
        "💰 Баланс\n\n"
        f"Доступно: {_money(statistics.balance.available_usd)}\n"
        f"Заморожено: {_money(statistics.balance.frozen_usd)}\n\n"
        "Замороженные средства — это суммы по задачам, которые ожидают или генерируются."
    )


async def handle_statistics(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Сервис временно недоступен.\n\nПопробуйте ещё раз немного позже.")
        return
    try:
        statistics = await backend_client.get_statistics(message.from_user.id)
    except BackendNotFoundError:
        await message.answer("Я не понял команду.\n\nВыберите действие в меню или нажмите /start.")
        return
    except (BackendUnavailableError, BackendClientError):
        await message.answer("Сервис временно недоступен.\n\nПопробуйте ещё раз немного позже.")
        return

    if statistics.business_account is not None:
        balance = statistics.business_account.available_usd
        frozen = statistics.business_account.frozen_usd
        balance_label = "Баланс компании"
    else:
        balance = statistics.balance.available_usd
        frozen = statistics.balance.frozen_usd
        balance_label = "Баланс"

    await message.answer(
        "📊 Статистика\n\n"
        f"{balance_label}: {_money(balance)}\n"
        f"Заморожено: {_money(frozen)}\n\n"
        f"Всего задач: {statistics.generations.all_time}\n"
        f"Успешных: {statistics.generations.completed_all_time}\n"
        f"Ошибок: {statistics.generations.failed_all_time}\n\n"
        f"Потрачено: {_money(statistics.spending.all_time_usd)}"
    )


async def handle_generations(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Сервис временно недоступен.\n\nПопробуйте ещё раз немного позже.")
        return
    try:
        generations = await backend_client.get_generations(message.from_user.id, limit=10)
    except BackendNotFoundError:
        await message.answer("Я не понял команду.\n\nВыберите действие в меню или нажмите /start.")
        return
    except (BackendUnavailableError, BackendClientError):
        await message.answer("Сервис временно недоступен.\n\nПопробуйте ещё раз немного позже.")
        return

    if not generations:
        await message.answer("У вас пока нет готовых видео.")
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
        [InlineKeyboardButton(text=f"⬇️ Скачать #{index}", url=item.result_url)]
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
            "Автоматическое пополнение временно недоступно.\n\n"
            "Для пополнения баланса напишите в поддержку.\n"
            "После оплаты администратор зачислит средства вручную."
        )
        package_buttons = None
    else:
        text = (
            "Выберите пакет пополнения:\n\n"
            "Оплата проходит через CryptoBot в USDT.\n"
            "Баланс отображается в USD.\n\n"
            "Для бизнес-пакетов и прямого пополнения напишите в поддержку.\n\n"
            "Мы можем зачислить баланс вручную и подключить сотрудников "
            "к общему балансу компании."
        )

    reply_markup = None if provider == "manual" else top_up_amounts_keyboard(package_buttons)
    await message.answer(text, reply_markup=reply_markup)


async def handle_help(message: Message) -> None:
    await message.answer(
        "Как создать AI-видео:\n\n"
        "1. Загрузите фото человека или персонажа.\n"
        "2. Добавьте аудио с голосом.\n"
        "3. Проверьте стоимость и подтвердите генерацию.\n"
        "4. Получите готовое видео.\n\n"
        "Стоимость считается по длительности аудио.\n"
        "Средства сначала замораживаются, а списываются только после успешной генерации."
    )


async def handle_support(message: Message) -> None:
    support_username = safe_html(get_settings().support_telegram_username.lstrip("@"), max_len=64)
    await message.answer(
        f"🆘 Поддержка: @{support_username}\n\n"
        "Напишите нам, если нужна помощь с оплатой, генерацией или бизнес-пакетом."
    )


async def handle_generate_video(message: Message) -> None:
    await handle_help(message)


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

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оплатить через CryptoBot", url=invoice.payment_url)]
        ]
    )
    await callback.message.answer(
        "✅ Счёт создан.\n\n"
        f"Сумма: {_money(invoice.amount_usd)}\n"
        "Оплата: USDT через CryptoBot\n\n"
        "После подтверждения оплаты баланс пополнится автоматически.",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("top_up_stub:"))
async def handle_old_top_up_amount(callback: CallbackQuery) -> None:
    await callback.answer(
        "Сейчас доступны только фиксированные пакеты пополнения: $10, $25, $50, $100.",
        show_alert=True,
    )


@router.message()
async def handle_unknown_message(message: Message) -> None:
    await message.answer("Я не понял команду.\n\nВыберите действие в меню или нажмите /start.")


def _format_generation_item(index: int, item: GenerationHistoryItemDto) -> str:
    duration = (
        _duration(item.audio_duration_seconds)
        if item.audio_duration_seconds is not None
        else "неизвестно"
    )
    price = _money(item.price_usd) if item.price_usd is not None else "не рассчитана"
    status = safe_html(STATUS_DISPLAY.get(item.status, item.status), max_len=80)
    created_at = safe_html(item.created_at, max_len=80)
    display_name = safe_html(item.display_name, max_len=90)
    result = (
        f"#{index}\n"
        f"Видео: {display_name}\n"
        f"Статус: {status}\n"
        f"Длительность: {duration}\n"
        f"Стоимость: {price}\n"
        f"Дата: {created_at}"
    )
    if item.status == "completed" and item.result_url:
        result += "\nРезультат: доступен для скачивания"
    elif item.status == "completed" and item.mock_result_message:
        result += "\nРезультат: готово"
    elif item.status == "failed":
        result += "\nРезультат: не удалось завершить"
    elif item.status in {"waiting_for_gpu", "waiting_for_pod"}:
        result += "\nРезультат: ожидает свободный сервер"
    elif item.status in {"queued", "generating"}:
        result += "\nРезультат: ещё не готов"
    return result


def _money(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01'))}"


def _duration(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'))} сек"


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
