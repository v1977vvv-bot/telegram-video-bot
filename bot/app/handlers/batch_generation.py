from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from io import BytesIO
from uuid import UUID

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.app.keyboards.main_menu import (
    BATCH_GENERATION_BUTTONS,
    batch_generation_confirm_keyboard,
    batch_generation_quality_keyboard,
    batch_web_upload_keyboard,
    cancel_keyboard,
    main_menu_keyboard,
    top_up_amounts_keyboard,
)
from bot.app.services.backend_client import (
    BackendClientError,
    BackendPaymentRequiredError,
    BackendUnavailableError,
    BatchDraftDto,
    BotBackendClient,
)
from bot.app.utils.text import safe_html

router = Router()
backend_client = BotBackendClient()
TELEGRAM_DIRECT_ZIP_MAX_BYTES = 20 * 1024 * 1024


class BatchGenerationStates(StatesGroup):
    choosing_quality = State()
    waiting_for_zip = State()
    confirming = State()


@router.message(F.text.in_(BATCH_GENERATION_BUTTONS))
async def start_batch_generation_flow(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(BatchGenerationStates.choosing_quality)
    await message.answer(
        "📦 Пакетная генерация\n\n"
        "Загрузите .zip архив после выбора качества.\n\n"
        "В архиве у каждой картинки и аудио должно быть одинаковое имя без расширения:\n"
        "001.jpg + 001.mp3\n"
        "002.png + 002.wav\n"
        "иван.jpg + иван.mp3\n\n"
        "Фото: jpg, jpeg, png, webp\n"
        "Аудио: mp3, wav, ogg, m4a\n\n"
        "Кириллица, латиница, цифры, пробелы, подчёркивания и дефисы поддерживаются.\n"
        "Служебные файлы macOS __MACOSX и .DS_Store игнорируются.\n\n"
        "Выберите качество:",
        reply_markup=batch_generation_quality_keyboard(),
    )


@router.callback_query(
    BatchGenerationStates.choosing_quality,
    F.data.startswith("batch_quality:"),
)
async def handle_batch_quality(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.data is None:
        return

    _, quality_profile = callback.data.split(":", maxsplit=1)
    await state.update_data(quality_profile=quality_profile)
    await state.set_state(BatchGenerationStates.waiting_for_zip)
    await callback.message.answer(  # type: ignore[union-attr]
        f"Качество: {quality_profile}\n\nТеперь загрузите .zip архив с парами фото и аудио.",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "batch_web_upload")
async def handle_batch_web_upload(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        return
    await state.clear()
    try:
        web_app_url = await backend_client.create_batch_upload_session(
            telegram_id=callback.from_user.id,
        )
    except (BackendUnavailableError, BackendClientError):
        await callback.message.answer(  # type: ignore[union-attr]
            "Сервис временно недоступен.\n\nПопробуйте ещё раз немного позже."
        )
        await callback.answer()
        return

    await callback.message.answer(  # type: ignore[union-attr]
        "Для больших архивов используйте прямую загрузку через Web App.\n\n"
        "Там можно выбрать качество, загрузить .zip и сразу запустить пакет.",
        reply_markup=batch_web_upload_keyboard(web_app_url),
    )
    await callback.answer()


@router.message(BatchGenerationStates.waiting_for_zip, ~F.text.in_({"❌ Отмена", "Отмена"}))
async def handle_batch_zip(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.from_user is None:
        await message.answer("Сервис временно недоступен.\n\nПопробуйте ещё раз немного позже.")
        return

    archive = _extract_zip_file(message)
    if archive is None:
        await message.answer(
            "Нужно загрузить .zip архив документом.\n\nПроверьте файл и отправьте архив ещё раз.",
            reply_markup=cancel_keyboard(),
        )
        return

    if archive.file_size is not None and archive.file_size > TELEGRAM_DIRECT_ZIP_MAX_BYTES:
        try:
            web_app_url = await backend_client.create_batch_upload_session(
                telegram_id=message.from_user.id,
            )
        except (BackendUnavailableError, BackendClientError):
            await message.answer(
                "Архив слишком большой для загрузки через Telegram.\n\n"
                "Сервис Web App временно недоступен. Попробуйте ещё раз немного позже.",
                reply_markup=cancel_keyboard(),
            )
            return

        await message.answer(
            "Архив слишком большой для загрузки через Telegram.\n"
            "Используйте загрузку через Web App.",
            reply_markup=batch_web_upload_keyboard(web_app_url),
        )
        return

    data = await state.get_data()
    quality_profile = str(data.get("quality_profile") or "480p")
    await message.answer("✅ Архив получен.\n\nПроверяю пары файлов и считаю стоимость.")
    content = await _download_telegram_file(bot, archive.file_id)

    try:
        draft = await backend_client.create_batch_draft(
            telegram_id=message.from_user.id,
            filename=archive.filename,
            content=content,
            quality_profile=quality_profile,
        )
    except (BackendUnavailableError, BackendClientError) as exc:
        await message.answer(_format_backend_error(exc))
        return

    if draft.errors:
        await message.answer(_format_batch_errors(draft), reply_markup=cancel_keyboard())
        return

    if draft.batch_id is None:
        await message.answer(
            "Не удалось создать пакетную генерацию.\n\nПопробуйте загрузить архив ещё раз.",
            reply_markup=cancel_keyboard(),
        )
        return

    await state.update_data(
        batch_id=str(draft.batch_id),
        quality_profile=draft.quality_profile,
        total_price_usd=str(draft.total_price_usd or Decimal("0")),
    )
    await state.set_state(BatchGenerationStates.confirming)
    await message.answer(
        _format_batch_summary(draft),
        reply_markup=batch_generation_confirm_keyboard(
            quality_profile=draft.quality_profile,
            price_usd=draft.total_price_usd or Decimal("0"),
        ),
    )


@router.callback_query(BatchGenerationStates.confirming, F.data == "batch_confirm")
async def handle_batch_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        return

    data = await state.get_data()
    batch_id = UUID(str(data["batch_id"]))
    try:
        await backend_client.confirm_batch(
            batch_id=batch_id,
            telegram_id=callback.from_user.id,
        )
    except BackendPaymentRequiredError as exc:
        await callback.message.answer(  # type: ignore[union-attr]
            f"{safe_html(exc, max_len=300)}\n\nПополните баланс одним из доступных пакетов.",
            reply_markup=top_up_amounts_keyboard(),
        )
        await callback.answer()
        return
    except (BackendUnavailableError, BackendClientError) as exc:
        await callback.message.answer(_format_backend_error(exc))  # type: ignore[union-attr]
        await callback.answer()
        return

    await state.clear()
    await callback.message.answer(  # type: ignore[union-attr]
        "✅ Пакетная генерация запущена.\n\nВидео будут приходить по мере готовности.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "batch_cancel")
async def cancel_batch_generation_by_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer(  # type: ignore[union-attr]
        "Пакетная генерация отменена.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.message(BatchGenerationStates.choosing_quality, F.text.in_({"❌ Отмена", "Отмена"}))
@router.message(BatchGenerationStates.waiting_for_zip, F.text.in_({"❌ Отмена", "Отмена"}))
@router.message(BatchGenerationStates.confirming, F.text.in_({"❌ Отмена", "Отмена"}))
async def cancel_batch_generation_by_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Пакетная генерация отменена.", reply_markup=main_menu_keyboard())


@dataclass(frozen=True)
class TelegramZipFileInfo:
    file_id: str
    filename: str
    file_size: int | None


def _extract_zip_file(message: Message) -> TelegramZipFileInfo | None:
    if message.document is None:
        return None
    filename = message.document.file_name or "batch.zip"
    if not filename.casefold().endswith(".zip"):
        return None
    return TelegramZipFileInfo(
        file_id=message.document.file_id,
        filename=filename,
        file_size=message.document.file_size,
    )


async def _download_telegram_file(bot: Bot, file_id: str) -> bytes:
    telegram_file = await bot.get_file(file_id)
    buffer = BytesIO()
    await bot.download_file(telegram_file.file_path, buffer)
    return buffer.getvalue()


def _format_batch_summary(draft: BatchDraftDto) -> str:
    total_duration = (
        _duration(draft.total_duration_seconds)
        if draft.total_duration_seconds is not None
        else "неизвестно"
    )
    total_price = _money(draft.total_price_usd or Decimal("0"))
    lines = [
        "Пакет готов к запуску:\n",
        f"Качество: {draft.quality_profile}",
        f"Пар: {draft.total_jobs}",
        f"Общая длительность: {total_duration}",
        f"Стоимость: {total_price}",
        "",
        "Первые пары:",
    ]
    for item in draft.items[:10]:
        basename = safe_html(item.basename, max_len=80)
        lines.append(
            f"#{item.index} {basename} — {_duration(item.audio_duration_seconds)}, "
            f"{_money(item.price_usd)}"
        )
    remaining = len(draft.items) - 10
    if remaining > 0:
        lines.append(f"... и ещё {remaining}")
    lines.extend(["", "Запустить пакет?"])
    return "\n".join(lines)


def _format_batch_errors(draft: BatchDraftDto) -> str:
    lines = ["Архив не прошёл проверку:\n"]
    for error in draft.errors[:20]:
        filename = f" — {safe_html(error.filename, max_len=120)}" if error.filename else ""
        lines.append(f"• {_error_message(error.code, error.message)}{filename}")
    remaining = len(draft.errors) - 20
    if remaining > 0:
        lines.append(f"• ... и ещё {remaining} ошибок")
    lines.append("\nИсправьте архив и загрузите .zip ещё раз.")
    return "\n".join(lines)


def _error_message(code: str, message: str) -> str:
    mapping = {
        "duplicate_image": "дубликат фото с таким именем",
        "duplicate_audio": "дубликат аудио с таким именем",
        "missing_audio": "для фото нет аудио",
        "missing_image": "для аудио нет фото",
        "unsupported_file": "неподдерживаемый файл",
        "unsafe_path": "небезопасный путь внутри архива",
        "unsupported_archive_type": "поддерживается только .zip",
        "invalid_zip": "архив повреждён или не читается",
        "audio_probe_failed": "не удалось определить длительность аудио",
    }
    return mapping.get(code, safe_html(message, max_len=160))


def _money(value: Decimal) -> str:
    amount = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    text = format(amount, "f").rstrip("0").rstrip(".")
    if "." not in text:
        text = f"{text}.00"
    elif len(text.rsplit(".", maxsplit=1)[1]) == 1:
        text = f"{text}0"
    return f"${text}"


def _duration(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.1"))
    if rounded == rounded.to_integral_value():
        return f"{int(rounded)} сек"
    return f"{rounded} сек"


def _format_backend_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "zip" in message or "archive" in message or "архив" in message:
        return "Не удалось обработать архив.\n\nПроверьте .zip файл и попробуйте ещё раз."
    return "Сервис временно недоступен.\n\nПопробуйте ещё раз немного позже."
