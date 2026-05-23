from __future__ import annotations

import logging
import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path

logger = logging.getLogger(__name__)


class VideoProbeService:
    """Small ffprobe/ffmpeg wrapper for worker-side video postprocessing."""

    def get_video_duration_seconds(self, path: Path) -> Decimal:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")

        raw_duration = result.stdout.strip()
        try:
            duration = Decimal(raw_duration)
        except InvalidOperation as exc:
            raise RuntimeError(f"ffprobe returned invalid duration: {raw_duration}") from exc
        if duration <= Decimal("0"):
            raise RuntimeError(f"ffprobe returned non-positive duration: {raw_duration}")
        return duration

    def trim_video_to_duration(
        self,
        *,
        input_path: Path,
        output_path: Path,
        duration_seconds: Decimal,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration_arg = format(duration_seconds, "f")
        copy_result = self._run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-t",
                duration_arg,
                "-c",
                "copy",
                str(output_path),
            ]
        )
        if copy_result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
            return output_path

        fallback_result = self._run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-t",
                duration_arg,
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        if (
            fallback_result.returncode != 0
            or not output_path.exists()
            or output_path.stat().st_size <= 0
        ):
            raise RuntimeError(f"ffmpeg trim failed: {fallback_result.stderr.strip()}")
        return output_path

    def extract_last_frame(self, video_path: Path, output_image_path: Path) -> Path:
        output_image_path.parent.mkdir(parents=True, exist_ok=True)
        duration = self.get_video_duration_seconds(video_path)
        attempt_errors: list[str] = []

        timestamps = _last_frame_candidate_timestamps(duration)
        for timestamp in timestamps:
            if output_image_path.exists():
                output_image_path.unlink()
            timestamp_arg = _format_decimal_seconds(timestamp)
            result = self._run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    timestamp_arg,
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    str(output_image_path),
                ]
            )
            if _has_output(output_image_path):
                logger.info(
                    "Last frame extracted input=%s output=%s duration=%s timestamp=%s",
                    video_path,
                    output_image_path,
                    _format_decimal_seconds(duration),
                    timestamp_arg,
                )
                return output_image_path

            stderr_tail = _stderr_tail(result.stderr)
            attempt_errors.append(
                f"timestamp={timestamp_arg} returncode={result.returncode} "
                f"stderr_tail={stderr_tail}"
            )
            logger.warning(
                "Last frame extraction attempt failed input=%s output=%s duration=%s "
                "timestamp=%s returncode=%s stderr_tail=%s",
                video_path,
                output_image_path,
                _format_decimal_seconds(duration),
                timestamp_arg,
                result.returncode,
                stderr_tail,
            )

        if output_image_path.exists():
            output_image_path.unlink()
        fallback_result = self._run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-sseof",
                "-0.5",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                str(output_image_path),
            ]
        )
        if _has_output(output_image_path):
            logger.info(
                "Last frame extracted input=%s output=%s duration=%s timestamp=%s",
                video_path,
                output_image_path,
                _format_decimal_seconds(duration),
                "sseof:-0.5",
            )
            return output_image_path

        stderr_tail = _stderr_tail(fallback_result.stderr)
        attempt_errors.append(
            f"timestamp=sseof:-0.5 returncode={fallback_result.returncode} "
            f"stderr_tail={stderr_tail}"
        )
        logger.warning(
            "Last frame extraction attempt failed input=%s output=%s duration=%s "
            "timestamp=%s returncode=%s stderr_tail=%s",
            video_path,
            output_image_path,
            _format_decimal_seconds(duration),
            "sseof:-0.5",
            fallback_result.returncode,
            stderr_tail,
        )
        raise RuntimeError("ffmpeg last frame extraction failed: " + " | ".join(attempt_errors))

    def _run_ffmpeg(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )


def _has_output(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _last_frame_candidate_timestamps(duration: Decimal) -> list[Decimal]:
    candidates = [
        duration - Decimal("0.20"),
        duration - Decimal("0.50"),
        duration - Decimal("1.00"),
        duration * Decimal("0.95"),
        duration * Decimal("0.90"),
    ]
    timestamps: list[Decimal] = []
    seen: set[str] = set()
    for candidate in candidates:
        timestamp = max(candidate, Decimal("0.1"))
        key = _format_decimal_seconds(timestamp)
        if key in seen:
            continue
        seen.add(key)
        timestamps.append(timestamp)
    return timestamps


def _format_decimal_seconds(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.001")), "f")


def _stderr_tail(stderr: str, limit: int = 600) -> str:
    text = " ".join(stderr.strip().split())
    if len(text) <= limit:
        return text
    return text[-limit:]
