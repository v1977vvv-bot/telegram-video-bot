from __future__ import annotations

import subprocess
from decimal import Decimal, InvalidOperation
from pathlib import Path


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
        result = self._run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-sseof",
                "-0.1",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                str(output_image_path),
            ]
        )
        if result.returncode == 0 and _has_output(output_image_path):
            return output_image_path

        duration = self.get_video_duration_seconds(video_path)
        seek_seconds = max(duration - Decimal("0.100"), Decimal("0"))
        fallback_result = self._run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-ss",
                format(seek_seconds, "f"),
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                str(output_image_path),
            ]
        )
        if fallback_result.returncode != 0 or not _has_output(output_image_path):
            raise RuntimeError(
                f"ffmpeg last frame extraction failed: {fallback_result.stderr.strip()}"
            )
        return output_image_path

    def _run_ffmpeg(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )


def _has_output(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0
