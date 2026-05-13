from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AudioMetadata:
    duration_seconds: float
    mime_type: str | None = None


class AudioService:
    """Audio probing and splitting boundary."""

    async def probe(self, audio_path: Path) -> AudioMetadata:
        raise NotImplementedError("Audio probing is not implemented in stage 1")

    async def split_segments(self, audio_path: Path, max_seconds: int) -> list[Path]:
        raise NotImplementedError("Audio splitting is not implemented in stage 1")
