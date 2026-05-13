from __future__ import annotations

from pathlib import Path


class VideoStitchService:
    """Video stitching boundary for generated segment videos."""

    async def stitch_segments(self, *, segment_paths: list[Path], output_path: Path) -> Path:
        raise NotImplementedError("Video stitching is not implemented in stage 1")
