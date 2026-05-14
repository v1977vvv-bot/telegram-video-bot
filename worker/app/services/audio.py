from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

THREE_PLACES = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class AudioSegmentFile:
    segment_index: int
    path: Path
    start_seconds: Decimal
    end_seconds: Decimal
    duration_seconds: Decimal


class AudioService:
    """Worker-side ffprobe/ffmpeg audio utilities."""

    def get_duration_seconds(self, path: Path) -> Decimal:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe audio probe failed: {result.stderr.strip()}")

        payload = json.loads(result.stdout)
        raw_duration = payload.get("format", {}).get("duration")
        if raw_duration is None:
            raise RuntimeError("ffprobe audio duration missing")

        try:
            duration = Decimal(str(raw_duration)).quantize(
                THREE_PLACES,
                rounding=ROUND_HALF_UP,
            )
        except InvalidOperation as exc:
            raise RuntimeError(f"ffprobe returned invalid audio duration: {raw_duration}") from exc
        if duration <= Decimal("0"):
            raise RuntimeError(f"ffprobe returned non-positive audio duration: {raw_duration}")
        return duration

    def split_audio_to_segments(
        self,
        input_audio_path: Path,
        output_dir: Path,
        max_segment_seconds: int,
    ) -> list[AudioSegmentFile]:
        duration = self.get_duration_seconds(input_audio_path)
        segment_count = int(
            (duration / Decimal(max_segment_seconds)).to_integral_value(rounding=ROUND_CEILING)
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        segments: list[AudioSegmentFile] = []
        for index in range(segment_count):
            start = Decimal(index * max_segment_seconds).quantize(THREE_PLACES)
            end = min(duration, Decimal((index + 1) * max_segment_seconds)).quantize(THREE_PLACES)
            segment_duration = (end - start).quantize(THREE_PLACES)
            output_path = output_dir / f"segment_{index + 1:03d}.wav"
            self._write_segment(
                input_audio_path=input_audio_path,
                output_path=output_path,
                start_seconds=start,
                duration_seconds=segment_duration,
            )
            segments.append(
                AudioSegmentFile(
                    segment_index=index + 1,
                    path=output_path,
                    start_seconds=start,
                    end_seconds=end,
                    duration_seconds=segment_duration,
                )
            )
        return segments

    def _write_segment(
        self,
        *,
        input_audio_path: Path,
        output_path: Path,
        start_seconds: Decimal,
        duration_seconds: Decimal,
    ) -> None:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_audio_path),
                "-ss",
                format(start_seconds, "f"),
                "-t",
                format(duration_seconds, "f"),
                "-vn",
                "-acodec",
                "pcm_s16le",
                str(output_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size <= 0:
            raise RuntimeError(f"ffmpeg audio split failed: {result.stderr.strip()}")
