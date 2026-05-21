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


class RunPodPoolFullError(RunPodError):
    pass


class ComfyUINotReadyError(RunPodError):
    pass


@dataclass(frozen=True, slots=True)
class RunPodPodInfo:
    pod_id: str
    name: str | None
    status: str | None
    cloud_type: str | None
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

    def create_pod(
        self,
        gpu_type: str,
        min_ram_gb: int | None = None,
        *,
        cloud_type: str | None = None,
        cloud_phase: str | None = None,
    ) -> RunPodPodInfo:
        resolved_min_ram_gb = min_ram_gb or self._settings.runpod_min_ram_gb
        resolved_cloud_type = cloud_type or self._settings.runpod_primary_cloud_type
        payload = self.build_create_pod_payload(
            gpu_type,
            min_ram_gb=resolved_min_ram_gb,
            cloud_type=resolved_cloud_type,
            cloud_phase=cloud_phase,
        )
        logger.info(
            "RunPod pod create requested cloud_type=%s gpu_type=%s min_ram_gb=%s",
            resolved_cloud_type,
            gpu_type,
            resolved_min_ram_gb,
        )
        _log_safe_create_payload(payload)
        try:
            response = self._client.post("/pods", json=payload, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise RunPodCapacityError(
                f"RunPod create request timed out for cloud_type={resolved_cloud_type} "
                f"gpu_type={gpu_type} min_ram_gb={resolved_min_ram_gb}"
            ) from exc
        except httpx.TransportError as exc:
            raise RunPodCapacityError(
                f"RunPod create request failed for cloud_type={resolved_cloud_type} "
                f"gpu_type={gpu_type} min_ram_gb={resolved_min_ram_gb}: {exc.__class__.__name__}"
            ) from exc
        if _is_capacity_error(response):
            body = _safe_response_body(response)
            details = f": {body}" if body else ""
            raise RunPodCapacityError(
                f"RunPod capacity unavailable for cloud_type={resolved_cloud_type} "
                f"gpu_type={gpu_type} "
                f"min_ram_gb={resolved_min_ram_gb}: {_http_status_summary(response)}{details}"
            )
        if _is_retryable_create_error(response):
            body = _safe_response_body(response)
            details = f": {body}" if body else ""
            raise RunPodCapacityError(
                f"RunPod create transient failure for cloud_type={resolved_cloud_type} "
                f"gpu_type={gpu_type} "
                f"min_ram_gb={resolved_min_ram_gb}: {_http_status_summary(response)}{details}"
            )
        self._raise_for_response(response, "RunPod pod create failed")
        info = self._pod_info_from_response(
            response.json(),
            fallback_cloud_type=resolved_cloud_type,
            fallback_gpu_type=gpu_type,
        )
        logger.info(
            "RunPod pod created pod_id=%s cloud_type=%s gpu_type=%s min_ram_gb=%s",
            info.pod_id,
            info.cloud_type or resolved_cloud_type,
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
        cloud_type: str | None = None,
        cloud_phase: str | None = None,
    ) -> dict[str, Any]:
        """Build a RunPod DeployOnDemand-compatible /pods payload."""

        resolved_min_ram_gb = min_ram_gb or self._settings.runpod_min_ram_gb
        resolved_cloud_type = cloud_type or self._settings.runpod_primary_cloud_type
        use_fallback_overrides = (cloud_phase or "").strip().lower() == "fallback"
        ports = (
            _phase_string(
                primary=self._settings.runpod_ports,
                fallback=self._settings.runpod_fallback_ports,
                use_fallback=use_fallback_overrides,
            )
            or f"{self._settings.runpod_comfyui_port}/http"
        )
        allowed_cuda_versions = _phase_csv_list(
            primary=self._settings.runpod_allowed_cuda_versions
            or self._settings.runpod_cuda_version,
            fallback=self._settings.runpod_fallback_allowed_cuda_versions,
            use_fallback=use_fallback_overrides,
        )
        min_download = _phase_optional_int(
            primary=self._settings.runpod_min_download,
            fallback=self._settings.runpod_fallback_min_download,
            use_fallback=use_fallback_overrides,
        )
        min_upload = _phase_optional_int(
            primary=self._settings.runpod_min_upload,
            fallback=self._settings.runpod_fallback_min_upload,
            use_fallback=use_fallback_overrides,
        )
        payload: dict[str, Any] = {
            "name": f"ultronlab-comfyui-{uuid4().hex[:8]}",
            "cloudType": resolved_cloud_type,
            "containerDiskInGb": self._settings.runpod_container_disk_gb,
            "volumeInGb": self._settings.runpod_volume_disk_gb,
            "gpuCount": 1,
            "gpuTypeId": gpu_type,
            "minMemoryInGb": resolved_min_ram_gb,
            "minVcpuCount": self._settings.runpod_min_vcpu,
            "templateId": self._settings.runpod_template_id,
            "allowedCudaVersions": allowed_cuda_versions,
            "volumeKey": None,
            "ports": ports,
            "countryCode": None,
            "supportPublicIp": _phase_bool(
                primary=self._settings.runpod_support_public_ip,
                fallback=self._settings.runpod_fallback_support_public_ip,
                use_fallback=use_fallback_overrides,
            ),
            "startJupyter": _phase_bool(
                primary=self._settings.runpod_start_jupyter,
                fallback=self._settings.runpod_fallback_start_jupyter,
                use_fallback=use_fallback_overrides,
            ),
            "startSsh": _phase_bool(
                primary=self._settings.runpod_start_ssh,
                fallback=self._settings.runpod_fallback_start_ssh,
                use_fallback=use_fallback_overrides,
            ),
            "globalNetwork": _phase_bool(
                primary=self._settings.runpod_global_network,
                fallback=self._settings.runpod_fallback_global_network,
                use_fallback=use_fallback_overrides,
            ),
        }
        if min_download is not None:
            payload["minDownload"] = min_download
        if min_upload is not None:
            payload["minUpload"] = min_upload
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
        fallback_cloud_type: str | None = None,
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
            cloud_type=_first_string(payload, "cloudType", "cloud_type") or fallback_cloud_type,
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


def _phase_string(*, primary: str, fallback: str, use_fallback: bool) -> str:
    if use_fallback and fallback.strip():
        return fallback.strip()
    return primary.strip()


def _phase_csv_list(*, primary: str, fallback: str, use_fallback: bool) -> list[str]:
    raw_value = _phase_string(primary=primary, fallback=fallback, use_fallback=use_fallback)
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _phase_optional_int(
    *,
    primary: int | str | None,
    fallback: int | str | None,
    use_fallback: bool,
) -> int | None:
    raw_value = fallback if use_fallback and str(fallback or "").strip() else primary
    if raw_value is None:
        return None
    raw = str(raw_value).strip()
    if not raw:
        return None
    return int(raw)


def _phase_bool(*, primary: bool, fallback: str, use_fallback: bool) -> bool:
    if not use_fallback or not fallback.strip():
        return primary
    raw = fallback.strip().lower()
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    raise RunPodError(f"Invalid RunPod fallback boolean value: {fallback}")


def _log_safe_create_payload(payload: dict[str, Any]) -> None:
    logger.info(
        "RunPod create payload cloudType=%s templateId=%s gpuTypeId=%s "
        "minMemoryInGb=%s minVcpuCount=%s containerDiskInGb=%s volumeInGb=%s "
        "ports=%s allowedCudaVersions=%s minDownload=%s minUpload=%s "
        "supportPublicIp=%s startJupyter=%s startSsh=%s globalNetwork=%s",
        payload.get("cloudType"),
        payload.get("templateId"),
        payload.get("gpuTypeId"),
        payload.get("minMemoryInGb"),
        payload.get("minVcpuCount"),
        payload.get("containerDiskInGb"),
        payload.get("volumeInGb"),
        payload.get("ports"),
        payload.get("allowedCudaVersions"),
        payload.get("minDownload"),
        payload.get("minUpload"),
        payload.get("supportPublicIp"),
        payload.get("startJupyter"),
        payload.get("startSsh"),
        payload.get("globalNetwork"),
    )


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


def _is_retryable_create_error(response: httpx.Response) -> bool:
    if response.status_code in {401, 403}:
        return False
    if response.status_code == 400:
        body = _safe_response_body(response).lower()
        if any(
            marker in body
            for marker in ("invalid", "schema", "not a valid", "must be one of", "malformed")
        ):
            return False
    return response.status_code in {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524}


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
