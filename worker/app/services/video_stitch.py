from __future__ import annotations

import subprocess
from decimal import Decimal
from pathlib import Path


class VideoStitchService:
    """Worker-side ffmpeg video stitching utilities."""

    def stitch_mp4_segments(self, segment_paths: list[Path], output_path: Path) -> Path:
        if not segment_paths:
            raise RuntimeError("No segment videos to stitch")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        concat_file = output_path.with_suffix(".concat.txt")
        concat_file.write_text(
            "".join(f"file '{_escape_concat_path(path)}'\n" for path in segment_paths)
        )

        copy_result = self._run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
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
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
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
            raise RuntimeError(f"ffmpeg concat failed: {fallback_result.stderr.strip()}")
        return output_path

    def replace_audio_with_original(
        self,
        *,
        video_path: Path,
        original_audio_path: Path,
        output_path: Path,
        target_duration_seconds: Decimal | float,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        copy_result = self._run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-i",
                str(original_audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-shortest",
                "-movflags",
                "+faststart",
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
                str(video_path),
                "-i",
                str(original_audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
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
            raise RuntimeError(f"ffmpeg audio remux failed: {fallback_result.stderr.strip()}")
        return output_path

    def _run_ffmpeg(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )


def _escape_concat_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "\\'")
