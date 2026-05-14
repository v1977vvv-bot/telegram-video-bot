from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from uuid import UUID

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.app.keyboards.main_menu import (
    cancel_keyboard,
    generation_confirm_keyboard,
    generation_formats_keyboard,
    main_menu_keyboard,
    top_up_amounts_keyboard,
)
from bot.app.services.backend_client import (
    BackendClientError,
    BackendPaymentRequiredError,
    BackendUnavailableError,
    BotBackendClient,
    GenerationDraftDto,
    GenerationFormatSummaryDto,
)
from bot.app.utils.text import safe_html
from shared.app.config import get_settings
from shared.app.enums import AudioSegmentationStrategy, GenerationMode

router = Router()
backend_client = BotBackendClient()


class GenerationStates(StatesGroup):
    waiting_for_image = State()
    waiting_for_audio = State()
    choosing_format = State()
    confirming = State()


@router.message(F.text == "Сгенерировать видео")
async def start_generation_flow(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(GenerationStates.waiting_for_image)
    await message.answer(
        "🎬 Загрузите фото файлом или изображением. Поддерживаются JPG, PNG, WEBP.",
        reply_markup=cancel_keyboard(),
    )


@router.message(F.text == "Отмена")
async def cancel_generation_by_text(message: Message, state: FSMContext) -> None:
    await _cancel_generation(message, state)


@router.message(GenerationStates.waiting_for_image)
async def handle_generation_image(message: Message, state: FSMContext, bot: Bot) -> None:
    image_file = _extract_image_file(message)
    if image_file is None:
        await message.answer("Пожалуйста, загрузите фото в формате JPG, PNG или WEBP.")
        return

    content = await _download_telegram_file(bot, image_file.file_id)
    await state.update_data(
        image_content=content,
        image_filename=image_file.filename,
        image_mime_type=image_file.mime_type,
    )
    await state.set_state(GenerationStates.waiting_for_audio)
    await message.answer(
        "✅ Фото получено. Теперь загрузите аудио файлом. Поддерживаются MP3, WAV, M4A, OGG.",
        reply_markup=cancel_keyboard(),
    )


@router.message(GenerationStates.waiting_for_audio)
async def handle_generation_audio(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.from_user is None:
        await message.answer("Сервис временно недоступен. Попробуйте позже.")
        return

    audio_file = _extract_audio_file(message)
    if audio_file is None:
        await message.answer("Пожалуйста, загрузите аудио в формате MP3, WAV, M4A или OGG.")
        return

    data = await state.get_data()
    image_content = data.get("image_content")
    if not isinstance(image_content, bytes):
        await state.clear()
        await message.answer("Черновик устарел. Начните заново.", reply_markup=main_menu_keyboard())
        return

    audio_content = await _download_telegram_file(bot, audio_file.file_id)
    try:
        draft = await backend_client.create_generation_draft(
            telegram_id=message.from_user.id,
            image_content=image_content,
            image_filename=str(data["image_filename"]),
            image_mime_type=str(data["image_mime_type"]),
            audio_content=audio_content,
            audio_filename=audio_file.filename,
            audio_mime_type=audio_file.mime_type,
        )
    except (BackendUnavailableError, BackendClientError) as exc:
        await message.answer(f"Не удалось создать черновик: {safe_html(exc, max_len=300)}")
        return

    await state.update_data(
        job_id=str(draft.job_id),
        audio_duration_seconds=str(draft.audio_duration_seconds),
        segments_count=draft.segments_count,
        fps=draft.fps,
        price_usd=str(draft.price_usd),
    )
    await state.set_state(GenerationStates.choosing_format)
    await message.answer(
        _format_draft_summary(draft),
        reply_markup=generation_formats_keyboard(),
    )


@router.callback_query(GenerationStates.choosing_format, F.data.startswith("generation_format:"))
async def handle_generation_format(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None or callback.data is None:
        return

    _, width_raw, height_raw = callback.data.split(":")
    data = await state.get_data()
    job_id = UUID(str(data["job_id"]))
    try:
        summary = await backend_client.set_generation_format(
            job_id=job_id,
            telegram_id=callback.from_user.id,
            width=int(width_raw),
            height=int(height_raw),
        )
    except (BackendUnavailableError, BackendClientError) as exc:
        await callback.message.answer(f"Не удалось выбрать формат: {safe_html(exc, max_len=300)}")  # type: ignore[union-attr]
        await callback.answer()
        return

    await state.update_data(width=summary.width, height=summary.height)
    await state.set_state(GenerationStates.confirming)
    await callback.message.answer(  # type: ignore[union-attr]
        _format_confirmation(summary),
        reply_markup=generation_confirm_keyboard(),
    )
    await callback.answer()


@router.callback_query(GenerationStates.confirming, F.data == "generation_confirm")
async def handle_generation_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user is None:
        return

    data = await state.get_data()
    job_id = UUID(str(data["job_id"]))
    segments_count = int(data.get("segments_count") or 1)
    try:
        result = await backend_client.confirm_generation(
            job_id=job_id,
            telegram_id=callback.from_user.id,
        )
    except BackendPaymentRequiredError as exc:
        await callback.message.answer(  # type: ignore[union-attr]
            safe_html(exc, max_len=300),
            reply_markup=top_up_amounts_keyboard(),
        )
        await callback.answer()
        return
    except (BackendUnavailableError, BackendClientError) as exc:
        error_text = safe_html(_format_backend_error(exc), max_len=300)
        await callback.message.answer(f"Не удалось подтвердить задачу: {error_text}")  # type: ignore[union-attr]
        await callback.answer()
        return

    await state.clear()
    mode_note = _generation_mode_note(segments_count)
    await callback.message.answer(  # type: ignore[union-attr]
        "✅ Задача поставлена в очередь.\n"
        f"ID: {result.job_id}\n"
        f"Стоимость заморожена: ${_money(result.price_usd)}\n"
        f"{mode_note}"
        "Вы можете поставить ещё одну задачу.",
        reply_markup=main_menu_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "generation_cancel")
async def cancel_generation_by_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await _cancel_generation(callback, state)
    await callback.answer()


async def _cancel_generation(event: Message | CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    user = event.from_user
    job_id_raw = data.get("job_id")
    if user is not None and isinstance(job_id_raw, str):
        try:
            await backend_client.cancel_generation(job_id=UUID(job_id_raw), telegram_id=user.id)
        except BackendClientError:
            pass

    await state.clear()
    target = event.message if isinstance(event, CallbackQuery) else event
    await target.answer("Генерация отменена.", reply_markup=main_menu_keyboard())


def _extract_image_file(message: Message) -> TelegramFileInfo | None:
    if message.photo:
        return TelegramFileInfo(
            file_id=message.photo[-1].file_id,
            filename="telegram_photo.jpg",
            mime_type="image/jpeg",
        )
    if message.document and (message.document.mime_type or "").startswith("image/"):
        filename = message.document.file_name or "telegram_image.jpg"
        return TelegramFileInfo(
            file_id=message.document.file_id,
            filename=filename,
            mime_type=message.document.mime_type or "application/octet-stream",
        )
    return None


def _extract_audio_file(message: Message) -> TelegramFileInfo | None:
    if message.audio:
        return TelegramFileInfo(
            file_id=message.audio.file_id,
            filename=message.audio.file_name or "telegram_audio.mp3",
            mime_type=message.audio.mime_type or "audio/mpeg",
        )
    if message.voice:
        return TelegramFileInfo(
            file_id=message.voice.file_id,
            filename="telegram_voice.ogg",
            mime_type=message.voice.mime_type or "audio/ogg",
        )
    if message.document and (message.document.mime_type or "").startswith("audio/"):
        return TelegramFileInfo(
            file_id=message.document.file_id,
            filename=message.document.file_name or "telegram_audio.mp3",
            mime_type=message.document.mime_type or "application/octet-stream",
        )
    return None


async def _download_telegram_file(bot: Bot, file_id: str) -> bytes:
    telegram_file = await bot.get_file(file_id)
    buffer = BytesIO()
    await bot.download_file(telegram_file.file_path, buffer)
    return buffer.getvalue()


@dataclass(frozen=True, slots=True)
class TelegramFileInfo:
    file_id: str
    filename: str
    mime_type: str


def _format_draft_summary(draft: GenerationDraftDto) -> str:
    return (
        "✅ Аудио получено.\n\n"
        f"Длительность: {draft.audio_duration_seconds} сек\n"
        f"Сегментов: {draft.segments_count}\n"
        f"FPS: {draft.fps}\n"
        f"Предварительная стоимость: ${_money(draft.price_usd)}\n\n"
        "Выберите формат:"
    )


def _format_confirmation(summary: GenerationFormatSummaryDto) -> str:
    return (
        "Проверьте настройки:\n\n"
        "Фото: получено\n"
        f"Аудио: {summary.audio_duration_seconds} сек\n"
        f"Формат: {summary.width}×{summary.height}\n"
        f"FPS: {summary.fps}\n"
        f"{_segments_summary_line(summary.segments_count)}"
        f"Стоимость: ${_money(summary.price_usd)}\n\n"
        "Подтвердить генерацию?"
    )


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.0001'))}"


def _segments_summary_line(segments_count: int) -> str:
    strategy = get_settings().audio_segmentation_strategy.strip().lower()
    if strategy == AudioSegmentationStrategy.SILENCE.value:
        return (
            f"Сегменты: примерно {segments_count}. Точные стыки будут выбраны по паузам в аудио.\n"
        )
    return f"Сегментов: {segments_count}\n"


def _generation_mode_note(segments_count: int) -> str:
    mode = get_settings().generation_mode.strip().lower()
    if mode == GenerationMode.COMFYUI.value and segments_count > 1:
        return (
            f"Аудио будет разбито на {segments_count} сегментов. "
            "Генерация может занять длительное время.\n"
        )
    if mode == GenerationMode.COMFYUI.value:
        return "Реальная генерация может занять несколько минут.\n"
    return ""


def _format_backend_error(exc: Exception) -> str:
    message = str(exc)
    if "only one segment" in message.lower() or "up to 30 seconds" in message.lower():
        return (
            "Реальная генерация сейчас поддерживает аудио до 30 секунд. "
            "Более длинные аудио будут подключены позже."
        )
    return message
