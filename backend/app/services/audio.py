from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from pathlib import Path

from shared.app.config import Settings, get_settings
from shared.app.exceptions import AppError

THREE_PLACES = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class SegmentPlan:
    segment_index: int
    start_seconds: Decimal
    end_seconds: Decimal
    duration_seconds: Decimal
    frame_count: int


class AudioService:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def get_duration_seconds(self, path: Path) -> Decimal:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="ignore").strip()
            raise AppError(
                f"Не удалось определить длительность аудио: {message or 'ffprobe failed'}",
                code="audio_probe_failed",
                status_code=400,
            )

        payload = json.loads(stdout.decode("utf-8"))
        raw_duration = payload.get("format", {}).get("duration")
        if raw_duration is None:
            raise AppError(
                "Не удалось определить длительность аудио",
                code="audio_duration_missing",
                status_code=400,
            )

        duration = Decimal(str(raw_duration)).quantize(THREE_PLACES, rounding=ROUND_HALF_UP)
        if duration <= Decimal("0"):
            raise AppError("Длительность аудио должна быть больше 0", code="invalid_audio_duration")
        if duration > Decimal(self._settings.generation_max_audio_seconds):
            limit = self._settings.generation_max_audio_seconds
            raise AppError(
                f"Аудио слишком длинное. Максимум {limit} сек.",
                code="audio_too_long",
                status_code=400,
            )
        return duration

    def build_segments(
        self,
        duration_seconds: Decimal,
        max_segment_seconds: int,
        fps: int,
    ) -> list[SegmentPlan]:
        if duration_seconds <= Decimal("0"):
            raise AppError("Длительность аудио должна быть больше 0", code="invalid_audio_duration")
        if duration_seconds > Decimal(self._settings.generation_max_audio_seconds):
            limit = self._settings.generation_max_audio_seconds
            raise AppError(
                f"Аудио слишком длинное. Максимум {limit} сек.",
                code="audio_too_long",
                status_code=400,
            )

        plans: list[SegmentPlan] = []
        segment_count = int(
            (duration_seconds / Decimal(max_segment_seconds)).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        for index in range(segment_count):
            start = Decimal(index * max_segment_seconds).quantize(THREE_PLACES)
            end = min(duration_seconds, Decimal((index + 1) * max_segment_seconds)).quantize(
                THREE_PLACES
            )
            segment_duration = (end - start).quantize(THREE_PLACES)
            frame_count = int(
                (segment_duration * Decimal(fps)).to_integral_value(rounding=ROUND_CEILING)
            )
            plans.append(
                SegmentPlan(
                    segment_index=index + 1,
                    start_seconds=start,
                    end_seconds=end,
                    duration_seconds=segment_duration,
                    frame_count=frame_count,
                )
            )
        return plans
