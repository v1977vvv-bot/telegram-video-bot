from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ComfyUIJobResult:
    segment_paths: list[Path]
    raw_payload: dict[str, object]


class ComfyUIService:
    """ComfyUI API boundary for InfiniteTalk workflow execution."""

    async def submit_workflow(
        self,
        *,
        comfyui_url: str,
        workflow_path: Path,
        image_path: Path,
        audio_path: Path,
    ) -> str:
        raise NotImplementedError("ComfyUI workflow submission is not implemented in stage 1")

    async def wait_for_result(self, *, comfyui_url: str, prompt_id: str) -> ComfyUIJobResult:
        raise NotImplementedError("ComfyUI result polling is not implemented in stage 1")
