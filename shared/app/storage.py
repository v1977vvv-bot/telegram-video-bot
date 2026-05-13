from __future__ import annotations

import uuid
from pathlib import Path
from uuid import UUID

from shared.app.enums import FileType
from shared.app.exceptions import AppError

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
AUDIO_EXTENSIONS = {"mp3", "wav", "m4a", "ogg"}
AUDIO_MIME_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/mp4",
    "audio/x-m4a",
    "audio/ogg",
}
VIDEO_EXTENSIONS = {"mp4", "txt"}
VIDEO_MIME_TYPES = {"video/mp4", "text/plain"}

DEBUG_USER_ID = UUID("00000000-0000-0000-0000-000000000000")


def build_storage_key(user_id: UUID | None, file_type: FileType, extension: str) -> str:
    storage_user_id = user_id or DEBUG_USER_ID
    return (
        f"users/{storage_user_id}/{folder_for_file_type(file_type)}/{uuid.uuid4().hex}.{extension}"
    )


def folder_for_file_type(file_type: FileType) -> str:
    return {
        FileType.IMAGE: "images",
        FileType.AUDIO: "audio",
        FileType.VIDEO: "videos",
        FileType.SEGMENT_VIDEO: "videos",
        FileType.LAST_FRAME: "temp",
    }[file_type]


def validated_extension(
    *,
    file_type: FileType,
    original_filename: str | None,
    mime_type: str | None,
) -> str:
    extension = Path(original_filename or "").suffix.lower().lstrip(".")
    normalized_mime = (mime_type or "").lower()

    if file_type == FileType.IMAGE:
        allowed_ext = IMAGE_EXTENSIONS
        allowed_mime = IMAGE_MIME_TYPES
    elif file_type == FileType.AUDIO:
        allowed_ext = AUDIO_EXTENSIONS
        allowed_mime = AUDIO_MIME_TYPES
    elif file_type in {FileType.VIDEO, FileType.SEGMENT_VIDEO}:
        allowed_ext = VIDEO_EXTENSIONS
        allowed_mime = VIDEO_MIME_TYPES
    elif file_type == FileType.LAST_FRAME:
        allowed_ext = IMAGE_EXTENSIONS | {"txt"}
        allowed_mime = IMAGE_MIME_TYPES | {"text/plain"}
    else:
        raise AppError("Unsupported file type", code="unsupported_file_type")

    if extension not in allowed_ext:
        raise AppError("Неподдерживаемое расширение файла", code="unsupported_file_extension")
    if normalized_mime not in allowed_mime:
        raise AppError("Неподдерживаемый MIME type файла", code="unsupported_mime_type")
    if extension == "jpeg":
        return "jpg"
    return extension


def safe_local_path(root: Path, storage_key: str) -> Path:
    path = (root / storage_key).resolve()
    resolved_root = root.resolve()
    if resolved_root not in path.parents and path != resolved_root:
        raise AppError("Invalid storage key", code="invalid_storage_key", status_code=400)
    return path
