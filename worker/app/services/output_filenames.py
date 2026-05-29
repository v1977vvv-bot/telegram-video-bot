from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.generation_batch_item import GenerationBatchItem
from backend.app.models.generation_job import GenerationJob
from backend.app.models.uploaded_file import UploadedFile

_SPACES_RE = re.compile(r"\s+")
_MAX_OUTPUT_STEM_LENGTH = 80


def build_generation_output_filename(session: Session, job: GenerationJob) -> str:
    stem = _batch_item_stem(session, job)
    if stem is None:
        stem = _source_audio_stem(session, job)
    if stem is None:
        stem = f"video-{job.id}"
    return sanitize_generation_output_filename(stem, job.id)


def sanitize_generation_output_filename(stem: str | None, job_id: UUID) -> str:
    fallback = f"video-{job_id}"
    sanitized = _sanitize_stem(stem or "")
    if not sanitized:
        sanitized = fallback
    return f"{sanitized}.mp4"


def _batch_item_stem(session: Session, job: GenerationJob) -> str | None:
    if job.batch_id is None:
        return None
    return session.scalar(
        select(GenerationBatchItem.basename)
        .where(GenerationBatchItem.generation_job_id == job.id)
        .limit(1)
    )


def _source_audio_stem(session: Session, job: GenerationJob) -> str | None:
    if job.source_audio_file_id is None:
        return None
    audio_file = session.get(UploadedFile, job.source_audio_file_id)
    if audio_file is None or not audio_file.original_filename:
        return None
    return _filename_stem(audio_file.original_filename)


def _filename_stem(filename: str) -> str:
    normalized = filename.replace("\\", "/")
    basename = normalized.rsplit("/", maxsplit=1)[-1]
    if "." not in basename:
        return basename
    return basename.rsplit(".", maxsplit=1)[0]


def _sanitize_stem(stem: str) -> str:
    cleaned = "".join(
        character for character in stem if character.isalnum() or character in {" ", "-", "_", "."}
    )
    cleaned = _SPACES_RE.sub(" ", cleaned).strip()
    cleaned = cleaned[:_MAX_OUTPUT_STEM_LENGTH].strip()
    if not cleaned.strip(" ."):
        return ""
    return cleaned
