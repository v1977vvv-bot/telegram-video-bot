from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath

MAX_JOB_DISPLAY_NAME_LENGTH = 72
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACES = re.compile(r"\s+")
_GENERIC_IMAGE_NAMES = {
    "",
    "image",
    "img",
    "photo",
    "picture",
    "file",
    "document",
    "telegram_image",
    "telegram_photo",
}
_GENERIC_AUDIO_NAMES = {
    "",
    "audio",
    "voice",
    "sound",
    "recording",
    "file",
    "document",
    "telegram_audio",
    "telegram_voice",
}


def build_job_display_name(
    *,
    image_filename: str | None,
    audio_filename: str | None,
    created_at: datetime | None = None,
) -> str:
    image_name = _filename_part(
        image_filename,
        fallback="Фото",
        generic_names=_GENERIC_IMAGE_NAMES,
    )
    audio_name = _filename_part(
        audio_filename,
        fallback="Аудио",
        generic_names=_GENERIC_AUDIO_NAMES,
    )
    if image_name == "Фото" and audio_name == "Аудио" and created_at is not None:
        return f"Видео {created_at.strftime('%d.%m %H:%M')}"
    return _truncate_display_name(f"{image_name} + {audio_name}")


def _filename_part(
    filename: str | None,
    *,
    fallback: str,
    generic_names: set[str],
) -> str:
    name = _basename_without_extension(filename)
    normalized_for_generic = name.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized_for_generic in generic_names:
        return fallback
    cleaned = _clean_text(name)
    return cleaned or fallback


def _basename_without_extension(filename: str | None) -> str:
    if filename is None:
        return ""
    normalized_path = filename.replace("\\", "/")
    basename = PurePosixPath(normalized_path).name
    stem = basename.rsplit(".", maxsplit=1)[0] if "." in basename else basename
    return stem


def _clean_text(value: str) -> str:
    without_controls = _CONTROL_CHARS.sub("", value)
    collapsed = _SPACES.sub(" ", without_controls).strip()
    return collapsed


def _truncate_display_name(value: str) -> str:
    if len(value) <= MAX_JOB_DISPLAY_NAME_LENGTH:
        return value
    return value[: MAX_JOB_DISPLAY_NAME_LENGTH - 3].rstrip() + "..."
