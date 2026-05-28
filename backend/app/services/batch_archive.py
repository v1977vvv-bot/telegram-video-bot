from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a"}
SUPPORTED_EXTENSIONS = SUPPORTED_IMAGE_EXTENSIONS | SUPPORTED_AUDIO_EXTENSIONS


@dataclass(frozen=True)
class BatchArchivePair:
    index: int
    basename: str
    image_filename: str
    audio_filename: str


@dataclass(frozen=True)
class BatchArchiveError:
    code: str
    message: str
    filename: str | None = None


@dataclass(frozen=True)
class BatchArchiveResult:
    pairs: list[BatchArchivePair]
    errors: list[BatchArchiveError]


@dataclass(frozen=True)
class _ArchiveFile:
    filename: str
    basename: str
    normalized_basename: str


def parse_generation_batch_archive(filename: str, content: bytes) -> BatchArchiveResult:
    """Parse a generation batch ZIP and match image/audio files by basename."""
    if PurePosixPath(filename).suffix.casefold() != ".zip":
        return BatchArchiveResult(
            pairs=[],
            errors=[
                BatchArchiveError(
                    code="unsupported_archive_type",
                    message="Only .zip archives are supported.",
                    filename=filename,
                )
            ],
        )

    try:
        with ZipFile(BytesIO(content)) as archive:
            return _parse_zip(archive)
    except BadZipFile:
        return BatchArchiveResult(
            pairs=[],
            errors=[
                BatchArchiveError(
                    code="invalid_zip",
                    message="ZIP archive is invalid or corrupted.",
                    filename=filename,
                )
            ],
        )


def _parse_zip(archive: ZipFile) -> BatchArchiveResult:
    images: dict[str, _ArchiveFile] = {}
    audios: dict[str, _ArchiveFile] = {}
    errors: list[BatchArchiveError] = []

    for info in archive.infolist():
        raw_filename = info.filename
        if info.is_dir() or _is_ignored_service_file(raw_filename):
            continue

        if _is_unsafe_path(raw_filename):
            errors.append(
                BatchArchiveError(
                    code="unsafe_path",
                    message="Archive contains an unsafe path.",
                    filename=raw_filename,
                )
            )
            continue

        normalized_path = _normalize_zip_path(raw_filename)
        path = PurePosixPath(normalized_path)
        extension = path.suffix.casefold()
        if extension not in SUPPORTED_EXTENSIONS:
            errors.append(
                BatchArchiveError(
                    code="unsupported_file",
                    message="Archive contains an unsupported file type.",
                    filename=raw_filename,
                )
            )
            continue

        basename = path.stem
        normalized_basename = _normalize_basename(basename)
        file = _ArchiveFile(
            filename=raw_filename,
            basename=basename.strip(),
            normalized_basename=normalized_basename,
        )
        if extension in SUPPORTED_IMAGE_EXTENSIONS:
            _add_unique(images, file, "duplicate_image", errors)
        else:
            _add_unique(audios, file, "duplicate_audio", errors)

    for normalized_basename, image in images.items():
        if normalized_basename not in audios:
            errors.append(
                BatchArchiveError(
                    code="missing_audio",
                    message="Image has no matching audio file.",
                    filename=image.filename,
                )
            )

    for normalized_basename, audio in audios.items():
        if normalized_basename not in images:
            errors.append(
                BatchArchiveError(
                    code="missing_image",
                    message="Audio has no matching image file.",
                    filename=audio.filename,
                )
            )

    pairs: list[BatchArchivePair] = []
    if not errors:
        for index, normalized_basename in enumerate(sorted(images), start=1):
            image = images[normalized_basename]
            audio = audios[normalized_basename]
            pairs.append(
                BatchArchivePair(
                    index=index,
                    basename=image.basename,
                    image_filename=image.filename,
                    audio_filename=audio.filename,
                )
            )

    return BatchArchiveResult(pairs=pairs, errors=errors)


def _add_unique(
    files: dict[str, _ArchiveFile],
    file: _ArchiveFile,
    duplicate_code: str,
    errors: list[BatchArchiveError],
) -> None:
    existing = files.get(file.normalized_basename)
    if existing is not None:
        errors.append(
            BatchArchiveError(
                code=duplicate_code,
                message="Archive contains duplicate files with the same basename.",
                filename=file.filename,
            )
        )
        return
    files[file.normalized_basename] = file


def _normalize_basename(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip().casefold()


def _normalize_zip_path(filename: str) -> str:
    return filename.replace("\\", "/")


def _path_parts(filename: str) -> list[str]:
    return [part for part in _normalize_zip_path(filename).split("/") if part]


def _is_unsafe_path(filename: str) -> bool:
    normalized = _normalize_zip_path(filename)
    if normalized.startswith("/"):
        return True
    if len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/":
        return True
    return any(part == ".." for part in _path_parts(filename))


def _is_ignored_service_file(filename: str) -> bool:
    parts = _path_parts(filename)
    if not parts:
        return True
    if parts[0] == "__MACOSX":
        return True

    basename = parts[-1]
    normalized_basename = basename.casefold()
    if basename.startswith("._"):
        return True
    return normalized_basename in {".ds_store", "thumbs.db", "desktop.ini"}
