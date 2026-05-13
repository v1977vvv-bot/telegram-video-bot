from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunPodInstance:
    provider_pod_id: str
    comfyui_url: str
    status: str


class RunPodService:
    """RunPod pod lifecycle boundary for future GPU orchestration."""

    async def ensure_ready_pod(self) -> RunPodInstance:
        raise NotImplementedError("RunPod pod provisioning is not implemented in stage 1")

    async def delete_pod(self, provider_pod_id: str) -> None:
        raise NotImplementedError("RunPod pod deletion is not implemented in stage 1")
