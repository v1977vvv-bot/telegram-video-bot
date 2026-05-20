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
    CANCEL_BUTTONS,
    CREATE_VIDEO_BUTTONS,
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
    GenerationFormatSummaryDto,
)
from bot.app.utils.text import safe_html
from shared.app.config import get_settings

router = Router()
backend_client = BotBackendClient()


class GenerationStates(StatesGroup):
    waiting_for_image = State()
    waiting_for_audio = State()
    choosing_format = State()
    confirming = State()


@router.message(F.text.in_(CREATE_VIDEO_BUTTONS))
async def start_generation_flow(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(GenerationStates.waiting_for_image)
    await message.answer(
        "🎬 Загрузите фото для аватара.\n\n"
        "Поддерживаются JPG, PNG и WEBP.\n"
        "Лучше всего подходит портрет с хорошо видимым лицом.",
        reply_markup=cancel_keyboard(),
    )


@router.message(F.text.in_(CANCEL_BUTTONS))
async def cancel_generation_by_text(message: Message, state: FSMContext) -> None:
    await _cancel_generation(message, state)


@router.message(GenerationStates.waiting_for_image)
async def handle_generation_image(message: Message, state: FSMContext, bot: Bot) -> None:
    image_file = _extract_image_file(message)
    if image_file is None:
        await message.answer(
            "Не удалось принять фото.\n\n"
            "Поддерживаются JPG, PNG и WEBP.\n"
            "Попробуйте загрузить другое изображение."
        )
        return

    content = await _download_telegram_file(bot, image_file.file_id)
    await state.update_data(
        image_content=content,
        image_filename=image_file.filename,
        image_mime_type=image_file.mime_type,
    )
    await state.set_state(GenerationStates.waiting_for_audio)
    await message.answer(
        "✅ Фото получено.\n\n"
        "Теперь загрузите аудио с голосом.\n"
        "Поддерживаются MP3, WAV, M4A и OGG.",
        reply_markup=cancel_keyboard(),
    )


@router.message(GenerationStates.waiting_for_audio)
async def handle_generation_audio(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.from_user is None:
        await message.answer("Сервис временно недоступен.\n\nПопробуйте ещё раз немного позже.")
        return

    audio_file = _extract_audio_file(message)
    if audio_file is None:
        await message.answer(
            "Не удалось принять аудио.\n\n"
            "Поддерживаются MP3, WAV, M4A и OGG.\n"
            "Попробуйте загрузить другой файл."
        )
        return

    data = await state.get_data()
    image_content = data.get("image_content")
    if not isinstance(image_content, bytes):
        await state.clear()
        await message.answer(
            "Действие отменено.\n\nВы можете начать заново в любой момент.",
            reply_markup=main_menu_keyboard(),
        )
        return

    audio_content = await _download_telegram_file(bot, audio_file.file_id)
    await message.answer("✅ Аудио получено.\n\nСчитаю длительность и стоимость генерации.")
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
        await message.answer(_format_backend_error(exc, file_kind="audio"))
        return

    await state.update_data(
        job_id=str(draft.job_id),
        audio_duration_seconds=str(draft.audio_duration_seconds),
        segments_count=draft.segments_count,
        fps=draft.fps,
        price_usd=str(draft.price_usd),
        display_name=draft.display_name,
    )
    await state.set_state(GenerationStates.choosing_format)
    await message.answer("Выберите формат видео:", reply_markup=generation_formats_keyboard())


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
        await callback.message.answer(_format_backend_error(exc))  # type: ignore[union-attr]
        await callback.answer()
        return

    await state.update_data(
        width=summary.width,
        height=summary.height,
        display_name=summary.display_name,
    )
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
    try:
        result = await backend_client.confirm_generation(
            job_id=job_id,
            telegram_id=callback.from_user.id,
        )
    except BackendPaymentRequiredError as exc:
        payment_error = str(exc)
        if "балансе компании" in payment_error.lower():
            await callback.message.answer(  # type: ignore[union-attr]
                "Недостаточно средств на балансе компании.\n\n"
                f"Стоимость генерации: {_money(Decimal(str(data.get('price_usd') or '0')))}\n\n"
                "Обратитесь к администратору компании или в поддержку."
            )
            await callback.answer()
            return
        await callback.message.answer(  # type: ignore[union-attr]
            f"{safe_html(exc, max_len=300)}\n\n" "Пополните баланс одним из доступных пакетов.",
            reply_markup=top_up_amounts_keyboard(),
        )
        await callback.answer()
        return
    except (BackendUnavailableError, BackendClientError) as exc:
        await callback.message.answer(_format_backend_error(exc))  # type: ignore[union-attr]
        await callback.answer()
        return

    await state.clear()
    display_name = safe_html(result.display_name, max_len=90)
    if result.billing_account_type == "business":
        text = (
            "✅ Генерация запущена.\n\n"
            f"Видео: {display_name}\n"
            f"Заморожено на балансе компании: {_money(result.price_usd)}\n\n"
            "Мы пришлём результат, когда видео будет готово.\n\n"
            "Если серверу нужно подготовить модели, первый запуск может занять дольше обычного."
        )
    else:
        text = (
            "✅ Генерация запущена.\n\n"
            f"Видео: {display_name}\n"
            f"Заморожено: {_money(result.price_usd)}\n\n"
            "Мы пришлём результат, когда видео будет готово.\n\n"
            "Если серверу нужно подготовить модели, первый запуск может занять дольше обычного."
        )
    await callback.message.answer(  # type: ignore[union-attr]
        text,
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
    await target.answer(
        "Действие отменено.\n\nВы можете начать заново в любой момент.",
        reply_markup=main_menu_keyboard(),
    )


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


def _format_confirmation(summary: GenerationFormatSummaryDto) -> str:
    return (
        "Проверьте данные:\n\n"
        f"Видео: {safe_html(summary.display_name, max_len=90)}\n"
        f"Аудио: {_duration(summary.audio_duration_seconds)}\n"
        f"Стоимость: {_money(summary.price_usd)}\n\n"
        "Средства будут заморожены до завершения генерации.\n\n"
        "Запустить?"
    )


def _money(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01'))}"


def _duration(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.1'))} сек"


def _format_backend_error(exc: Exception, *, file_kind: str | None = None) -> str:
    message = str(exc)
    lower = message.lower()
    settings = get_settings()
    if "image_too_large" in lower or (file_kind == "image" and "слишком большой" in lower):
        return (
            "Файл слишком большой.\n\n"
            f"Максимальный размер фото: {settings.max_image_size_mb} МБ.\n"
            "Попробуйте загрузить изображение меньшего размера."
        )
    if "audio_too_large" in lower or (file_kind == "audio" and "слишком большой" in lower):
        return (
            "Аудиофайл слишком большой.\n\n"
            f"Максимальный размер аудио: {settings.max_audio_size_mb} МБ.\n"
            "Попробуйте загрузить файл меньшего размера."
        )
    if "audio_too_long" in lower or "слишком длин" in lower:
        max_minutes = Decimal(settings.generation_max_audio_seconds) / Decimal("60")
        return (
            "Аудио слишком длинное.\n\n"
            f"Максимальная длительность: {max_minutes.quantize(Decimal('0.1'))} минут.\n"
            "Попробуйте загрузить более короткий файл."
        )
    if "duration" in lower or "длительность" in lower or "ffprobe" in lower:
        return (
            "Не удалось определить длительность аудио.\n\n"
            "Попробуйте загрузить файл в другом формате: MP3, WAV, M4A или OGG."
        )
    if "user access is restricted" in lower or "user_banned" in lower:
        return (
            "Ваш аккаунт временно ограничен.\n\n"
            "Если вы считаете, что это ошибка, напишите в поддержку."
        )
    if file_kind == "audio":
        return (
            "Не удалось принять аудио.\n\n"
            "Поддерживаются MP3, WAV, M4A и OGG.\n"
            "Попробуйте загрузить другой файл."
        )
    if file_kind == "image":
        return (
            "Не удалось принять фото.\n\n"
            "Поддерживаются JPG, PNG и WEBP.\n"
            "Попробуйте загрузить другое изображение."
        )
    return "Не удалось обработать запрос.\n\nПопробуйте ещё раз через несколько минут."
