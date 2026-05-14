from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from shared.app.enums import AudioSegmentationStrategy

logger = logging.getLogger(__name__)
THREE_PLACES = Decimal("0.001")
SILENCE_START_RE = re.compile(r"silence_start:\s*(?P<value>[0-9.]+)")
SILENCE_END_RE = re.compile(
    r"silence_end:\s*(?P<end>[0-9.]+)\s*\|\s*silence_duration:\s*(?P<duration>[0-9.]+)"
)


@dataclass(frozen=True, slots=True)
class SilenceInterval:
    start_seconds: Decimal
    end_seconds: Decimal
    duration_seconds: Decimal
    midpoint_seconds: Decimal


@dataclass(frozen=True, slots=True)
class AudioSegmentBoundary:
    segment_index: int
    start_seconds: Decimal
    end_seconds: Decimal
    duration_seconds: Decimal
    reason: str
    target_end_seconds: Decimal | None = None
    silence: SilenceInterval | None = None


@dataclass(frozen=True, slots=True)
class AudioSegmentPlan:
    strategy: str
    total_duration_seconds: Decimal
    silences: list[SilenceInterval]
    boundaries: list[AudioSegmentBoundary]


@dataclass(frozen=True, slots=True)
class AudioSegmentFile:
    segment_index: int
    path: Path
    start_seconds: Decimal
    end_seconds: Decimal
    duration_seconds: Decimal
    reason: str


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
            duration = _quantize(Decimal(str(raw_duration)))
        except InvalidOperation as exc:
            raise RuntimeError(f"ffprobe returned invalid audio duration: {raw_duration}") from exc
        if duration <= Decimal("0"):
            raise RuntimeError(f"ffprobe returned non-positive audio duration: {raw_duration}")
        return duration

    def detect_silences(
        self,
        input_audio_path: Path,
        threshold_db: int | float,
        min_duration_seconds: Decimal | float,
    ) -> list[SilenceInterval]:
        result = subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(input_audio_path),
                "-af",
                f"silencedetect=noise={threshold_db}dB:d={min_duration_seconds}",
                "-f",
                "null",
                "-",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg silencedetect failed: {result.stderr.strip()}")

        silences = _parse_silencedetect_output(result.stderr)
        logger.info("Audio silencedetect found silences_count=%s", len(silences))
        return silences

    def build_segment_plan(
        self,
        *,
        input_audio_path: Path,
        max_segment_seconds: int,
        total_duration_seconds: Decimal | None = None,
        strategy: str = AudioSegmentationStrategy.FIXED.value,
        silence_threshold_db: int | float = -35,
        silence_min_duration_seconds: Decimal | float = Decimal("0.30"),
        silence_search_window_seconds: Decimal | float = Decimal("7"),
        segment_min_seconds: Decimal | float = Decimal("8"),
    ) -> AudioSegmentPlan:
        total_duration = total_duration_seconds or self.get_duration_seconds(input_audio_path)
        total_duration = _quantize(total_duration)
        strategy_value = _normalise_strategy(strategy)

        if strategy_value == AudioSegmentationStrategy.FIXED.value:
            return AudioSegmentPlan(
                strategy=strategy_value,
                total_duration_seconds=total_duration,
                silences=[],
                boundaries=self.build_fixed_boundaries(total_duration, max_segment_seconds),
            )

        try:
            silences = self.detect_silences(
                input_audio_path=input_audio_path,
                threshold_db=silence_threshold_db,
                min_duration_seconds=silence_min_duration_seconds,
            )
        except RuntimeError:
            logger.warning("Audio silencedetect failed; falling back to fixed segmentation")
            return AudioSegmentPlan(
                strategy=AudioSegmentationStrategy.FIXED.value,
                total_duration_seconds=total_duration,
                silences=[],
                boundaries=self.build_fixed_boundaries(total_duration, max_segment_seconds),
            )

        if not silences:
            logger.info("Audio silencedetect found no silences; fixed boundaries will be used")

        return AudioSegmentPlan(
            strategy=AudioSegmentationStrategy.SILENCE.value,
            total_duration_seconds=total_duration,
            silences=silences,
            boundaries=self.build_silence_boundaries(
                total_duration_seconds=total_duration,
                max_segment_seconds=max_segment_seconds,
                silences=silences,
                search_window_seconds=Decimal(str(silence_search_window_seconds)),
                min_segment_seconds=Decimal(str(segment_min_seconds)),
            ),
        )

    def build_fixed_boundaries(
        self,
        total_duration_seconds: Decimal,
        max_segment_seconds: int,
    ) -> list[AudioSegmentBoundary]:
        boundaries: list[AudioSegmentBoundary] = []
        current_start = Decimal("0.000")
        max_duration = Decimal(max_segment_seconds)
        while current_start < total_duration_seconds:
            target_end = _quantize(current_start + max_duration)
            end = min(total_duration_seconds, target_end)
            reason = "final" if end >= total_duration_seconds else "fixed"
            boundaries.append(_build_boundary(len(boundaries) + 1, current_start, end, reason))
            current_start = end
        return boundaries

    def build_silence_boundaries(
        self,
        *,
        total_duration_seconds: Decimal,
        max_segment_seconds: int,
        silences: list[SilenceInterval],
        search_window_seconds: Decimal,
        min_segment_seconds: Decimal,
    ) -> list[AudioSegmentBoundary]:
        boundaries: list[AudioSegmentBoundary] = []
        current_start = Decimal("0.000")
        max_duration = Decimal(max_segment_seconds)
        search_window = _quantize(search_window_seconds)
        min_segment = _quantize(min_segment_seconds)

        while current_start < total_duration_seconds:
            target_end = _quantize(current_start + max_duration)
            if target_end >= total_duration_seconds:
                boundaries.append(
                    _build_boundary(
                        len(boundaries) + 1,
                        current_start,
                        total_duration_seconds,
                        "final",
                        target_end,
                    )
                )
                break

            selected = _select_silence_cut(
                current_start=current_start,
                target_end=target_end,
                search_window_seconds=search_window,
                min_segment_seconds=min_segment,
                silences=silences,
            )
            if selected is None:
                boundaries.append(
                    _build_boundary(
                        len(boundaries) + 1,
                        current_start,
                        target_end,
                        "fixed",
                        target_end,
                    )
                )
                current_start = target_end
                continue

            cut = selected.midpoint_seconds
            boundaries.append(
                _build_boundary(
                    len(boundaries) + 1,
                    current_start,
                    cut,
                    "silence",
                    target_end,
                    selected,
                )
            )
            current_start = cut
        return boundaries

    def split_audio_to_segments(
        self,
        input_audio_path: Path,
        output_dir: Path,
        max_segment_seconds: int,
        total_duration_seconds: Decimal | None = None,
        strategy: str = AudioSegmentationStrategy.FIXED.value,
        silence_threshold_db: int | float = -35,
        silence_min_duration_seconds: Decimal | float = Decimal("0.30"),
        silence_search_window_seconds: Decimal | float = Decimal("7"),
        segment_min_seconds: Decimal | float = Decimal("8"),
    ) -> list[AudioSegmentFile]:
        plan = self.build_segment_plan(
            input_audio_path=input_audio_path,
            max_segment_seconds=max_segment_seconds,
            total_duration_seconds=total_duration_seconds,
            strategy=strategy,
            silence_threshold_db=silence_threshold_db,
            silence_min_duration_seconds=silence_min_duration_seconds,
            silence_search_window_seconds=silence_search_window_seconds,
            segment_min_seconds=segment_min_seconds,
        )
        return self.split_audio_by_boundaries(input_audio_path, output_dir, plan.boundaries)

    def split_audio_by_boundaries(
        self,
        input_audio_path: Path,
        output_dir: Path,
        boundaries: list[AudioSegmentBoundary],
    ) -> list[AudioSegmentFile]:
        output_dir.mkdir(parents=True, exist_ok=True)

        segments: list[AudioSegmentFile] = []
        for boundary in boundaries:
            output_path = output_dir / f"segment_{boundary.segment_index:03d}.wav"
            self._write_segment(
                input_audio_path=input_audio_path,
                output_path=output_path,
                start_seconds=boundary.start_seconds,
                duration_seconds=boundary.duration_seconds,
            )
            segments.append(
                AudioSegmentFile(
                    segment_index=boundary.segment_index,
                    path=output_path,
                    start_seconds=boundary.start_seconds,
                    end_seconds=boundary.end_seconds,
                    duration_seconds=boundary.duration_seconds,
                    reason=boundary.reason,
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
                "-ss",
                format(start_seconds, "f"),
                "-i",
                str(input_audio_path),
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


def _parse_silencedetect_output(output: str) -> list[SilenceInterval]:
    silences: list[SilenceInterval] = []
    current_start: Decimal | None = None
    for line in output.splitlines():
        start_match = SILENCE_START_RE.search(line)
        if start_match is not None:
            current_start = _quantize(Decimal(start_match.group("value")))
            continue

        end_match = SILENCE_END_RE.search(line)
        if end_match is None or current_start is None:
            continue

        end = _quantize(Decimal(end_match.group("end")))
        duration = _quantize(Decimal(end_match.group("duration")))
        midpoint = _quantize(current_start + duration / Decimal("2"))
        if end > current_start and duration > Decimal("0"):
            silences.append(
                SilenceInterval(
                    start_seconds=current_start,
                    end_seconds=end,
                    duration_seconds=duration,
                    midpoint_seconds=midpoint,
                )
            )
        current_start = None
    return silences


def _normalise_strategy(strategy: str) -> str:
    value = strategy.strip().lower()
    if value in {item.value for item in AudioSegmentationStrategy}:
        return value
    logger.warning("Unknown AUDIO_SEGMENTATION_STRATEGY=%s; falling back to fixed", strategy)
    return AudioSegmentationStrategy.FIXED.value


def _select_silence_cut(
    *,
    current_start: Decimal,
    target_end: Decimal,
    search_window_seconds: Decimal,
    min_segment_seconds: Decimal,
    silences: list[SilenceInterval],
) -> SilenceInterval | None:
    search_start = target_end - search_window_seconds
    candidates = [
        silence
        for silence in silences
        if search_start <= silence.midpoint_seconds <= target_end
        and silence.midpoint_seconds - current_start >= min_segment_seconds
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.midpoint_seconds)


def _build_boundary(
    segment_index: int,
    start_seconds: Decimal,
    end_seconds: Decimal,
    reason: str,
    target_end_seconds: Decimal | None = None,
    silence: SilenceInterval | None = None,
) -> AudioSegmentBoundary:
    start = _quantize(start_seconds)
    end = _quantize(end_seconds)
    duration = _quantize(end - start)
    if duration <= Decimal("0"):
        raise RuntimeError(f"Invalid non-positive segment duration: {duration}")
    target_end = _quantize(target_end_seconds) if target_end_seconds is not None else None
    return AudioSegmentBoundary(
        segment_index=segment_index,
        start_seconds=start,
        end_seconds=end,
        duration_seconds=duration,
        reason=reason,
        target_end_seconds=target_end,
        silence=silence,
    )


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(THREE_PLACES, rounding=ROUND_HALF_UP)
