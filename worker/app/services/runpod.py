from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

from shared.app.config import Settings, get_settings

logger = logging.getLogger(__name__)

RUNPOD_API_BASE_URL = "https://rest.runpod.io/v1"


class RunPodError(RuntimeError):
    pass


class RunPodCapacityError(RunPodError):
    pass


class NoGpuAvailableError(RunPodError):
    pass


class ComfyUINotReadyError(RunPodError):
    pass


@dataclass(frozen=True, slots=True)
class RunPodPodInfo:
    pod_id: str
    name: str | None
    status: str | None
    gpu_type: str | None
    base_url: str
    raw: dict[str, Any]


class RunPodClient:
    """Small sync client for RunPod REST pod lifecycle operations."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.Client | None = None,
        api_base_url: str = RUNPOD_API_BASE_URL,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = http_client or httpx.Client(
            base_url=api_base_url,
            timeout=httpx.Timeout(120.0, connect=30.0),
            follow_redirects=True,
        )
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def create_pod(self, gpu_type: str, min_ram_gb: int | None = None) -> RunPodPodInfo:
        resolved_min_ram_gb = min_ram_gb or self._settings.runpod_min_ram_gb
        payload = self.build_create_pod_payload(gpu_type, min_ram_gb=resolved_min_ram_gb)
        logger.info(
            "RunPod pod create requested gpu_type=%s min_ram_gb=%s",
            gpu_type,
            resolved_min_ram_gb,
        )
        response = self._client.post("/pods", json=payload, headers=self._headers())
        if _is_capacity_error(response):
            body = _safe_response_body(response)
            details = f": {body}" if body else ""
            raise RunPodCapacityError(
                f"RunPod capacity unavailable for gpu_type={gpu_type} "
                f"min_ram_gb={resolved_min_ram_gb}: {_http_status_summary(response)}{details}"
            )
        self._raise_for_response(response, "RunPod pod create failed")
        info = self._pod_info_from_response(response.json(), fallback_gpu_type=gpu_type)
        logger.info(
            "RunPod pod created pod_id=%s gpu_type=%s min_ram_gb=%s",
            info.pod_id,
            gpu_type,
            resolved_min_ram_gb,
        )
        return info

    def get_pod(self, pod_id: str) -> RunPodPodInfo:
        response = self._client.get(f"/pods/{pod_id}", headers=self._headers())
        self._raise_for_response(response, "RunPod pod lookup failed")
        return self._pod_info_from_response(response.json(), fallback_pod_id=pod_id)

    def terminate_pod(self, pod_id: str) -> None:
        response = self._client.delete(f"/pods/{pod_id}", headers=self._headers())
        if response.status_code == 404:
            logger.info("RunPod pod already absent pod_id=%s", pod_id)
            return
        self._raise_for_response(response, "RunPod pod terminate failed")
        logger.info("RunPod pod terminate requested pod_id=%s", pod_id)

    def build_create_pod_payload(
        self,
        gpu_type: str,
        *,
        min_ram_gb: int | None = None,
    ) -> dict[str, Any]:
        """Build the RunPod /pods payload without Network Volume fields."""

        resolved_min_ram_gb = min_ram_gb or self._settings.runpod_min_ram_gb
        payload: dict[str, Any] = {
            "name": f"ultronlab-comfyui-{uuid4().hex[:8]}",
            "cloudType": self._settings.runpod_cloud_type,
            "computeType": "GPU",
            "gpuTypeIds": [gpu_type],
            "gpuCount": 1,
            "templateId": self._settings.runpod_template_id,
            "containerDiskInGb": self._settings.runpod_container_disk_gb,
            "volumeInGb": self._settings.runpod_volume_disk_gb,
            "minVCPUPerGPU": self._settings.runpod_min_vcpu,
            "minRAMPerGPU": resolved_min_ram_gb,
            "ports": [f"{self._settings.runpod_comfyui_port}/http"],
            "supportPublicIp": True,
        }
        cuda_version = self._settings.runpod_cuda_version.strip()
        if cuda_version:
            payload["allowedCudaVersions"] = [cuda_version]
        return payload

    def build_comfyui_base_url(self, pod_id: str, port: int | None = None) -> str:
        resolved_port = port or self._settings.runpod_comfyui_port
        return f"https://{pod_id}-{resolved_port}.proxy.runpod.net"

    def _headers(self) -> dict[str, str]:
        api_key = self._settings.runpod_api_key.strip()
        if not api_key or api_key == "change_me":
            raise RunPodError("RUNPOD_API_KEY is not configured")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _pod_info_from_response(
        self,
        data: Any,
        *,
        fallback_pod_id: str | None = None,
        fallback_gpu_type: str | None = None,
    ) -> RunPodPodInfo:
        if not isinstance(data, dict):
            raise RunPodError("RunPod API returned a non-object response")

        payload = _unwrap_response_payload(data)
        pod_id = _first_string(payload, "id", "podId", "pod_id") or fallback_pod_id
        if not pod_id:
            raise RunPodError(f"RunPod API response did not include pod id: {_safe_payload(data)}")

        gpu_type = (
            _first_string(payload, "gpuTypeId", "gpuType", "machineType")
            or _nested_first_string(payload, ("gpu", "type"), ("gpu", "id"))
            or fallback_gpu_type
        )
        return RunPodPodInfo(
            pod_id=pod_id,
            name=_first_string(payload, "name"),
            status=_first_string(payload, "desiredStatus", "status"),
            gpu_type=gpu_type,
            base_url=self.build_comfyui_base_url(pod_id),
            raw=payload,
        )

    def _raise_for_response(self, response: httpx.Response, message: str) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = _safe_response_body(response)
            if body:
                raise RunPodError(f"{message}: {_http_status_summary(response)}: {body}") from exc
            raise RunPodError(f"{message}: {_http_status_summary(response)}") from exc


def _unwrap_response_payload(data: dict[str, Any]) -> dict[str, Any]:
    for key in ("data", "pod"):
        value = data.get(key)
        if isinstance(value, dict):
            data = value
    return data


def _first_string(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _nested_first_string(data: dict[str, Any], *paths: tuple[str, ...]) -> str | None:
    for path in paths:
        value: Any = data
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _is_capacity_error(response: httpx.Response) -> bool:
    if response.status_code in {401, 403}:
        return False
    body = _safe_response_body(response).lower()
    if response.status_code == 400 and any(
        marker in body
        for marker in ("invalid", "schema", "not a valid", "must be one of", "malformed")
    ):
        return False

    return any(marker in body for marker in _CAPACITY_ERROR_MARKERS)


_CAPACITY_ERROR_MARKERS = (
    "there are no instances currently available",
    "does not have the resources to deploy your pod",
    "please try a different machine",
    "no instances",
    "not available",
    "insufficient resources",
    "capacity",
)


def _http_status_summary(response: httpx.Response) -> str:
    return f"HTTP {response.status_code} {response.reason_phrase}".strip()


def _safe_response_body(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "").lower()
    text = response.text.strip()
    if not text:
        return ""
    if "html" in content_type or text.lower().startswith(("<!doctype", "<html")):
        return ""
    text = " ".join(text.split())
    return text[:500]


def _safe_payload(data: dict[str, Any]) -> str:
    text = " ".join(str(data).split())
    return text[:500]
