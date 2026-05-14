from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from shared.app.config import Settings, get_settings

logger = logging.getLogger(__name__)
TRANSIENT_HTTP_STATUSES = {502, 503, 504, 520, 522, 524}
TRANSIENT_NETWORK_ERRORS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


class ComfyUIError(RuntimeError):
    pass


class ComfyUITimeoutError(ComfyUIError):
    pass


class ComfyUIExecutionError(ComfyUIError):
    pass


class ComfyUITransientError(ComfyUIError):
    def __init__(self, message: str, *, status: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class ComfyUIUploadedFile:
    filename: str
    subfolder: str
    type: str = "input"


@dataclass(frozen=True, slots=True)
class ComfyUIOutputFile:
    filename: str
    subfolder: str
    type: str = "output"


class ComfyUIClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._base_url = self._settings.comfyui_base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=httpx.Timeout(120.0, connect=15.0),
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def healthcheck(self) -> dict[str, Any]:
        response = self._request_with_retries(
            lambda: self._client.get("/system_stats"),
            message="ComfyUI healthcheck failed",
            max_attempts=self._settings.comfyui_transient_retry_max_attempts,
        )
        return dict(response.json())

    def upload_image(self, local_path: Path, filename: str, subfolder: str) -> ComfyUIUploadedFile:
        return self._upload_file(
            endpoint="/upload/image",
            field_name="image",
            local_path=local_path,
            filename=filename,
            subfolder=subfolder,
        )

    def upload_audio(self, local_path: Path, filename: str, subfolder: str) -> ComfyUIUploadedFile:
        try:
            return self._upload_file(
                endpoint="/upload/image",
                field_name="image",
                local_path=local_path,
                filename=filename,
                subfolder=subfolder,
            )
        except ComfyUIError as first_error:
            logger.info("ComfyUI /upload/image audio upload failed filename=%s", filename)
            try:
                return self._upload_file(
                    endpoint="/upload/audio",
                    field_name="audio",
                    local_path=local_path,
                    filename=filename,
                    subfolder=subfolder,
                )
            except ComfyUIError as second_error:
                raise ComfyUIError(
                    f"ComfyUI audio upload failed via /upload/image and /upload/audio: "
                    f"{first_error}; {second_error}"
                ) from second_error

    def queue_prompt(self, prompt: dict[str, Any], client_id: str | None = None) -> str:
        payload = {"prompt": prompt, "client_id": client_id or str(uuid4())}
        response = self._request_with_retries(
            lambda: self._client.post("/prompt", json=payload),
            message="ComfyUI prompt queue failed",
            max_attempts=3,
        )
        data = response.json()
        prompt_id = data.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ComfyUIExecutionError("ComfyUI did not return prompt_id")
        return prompt_id

    def get_history(self, prompt_id: str) -> dict[str, Any]:
        response = self._request_with_retries(
            lambda: self._client.get(f"/history/{prompt_id}"),
            message="ComfyUI history lookup failed",
            max_attempts=self._settings.comfyui_transient_retry_max_attempts,
        )
        return dict(response.json())

    def get_queue(self) -> dict[str, Any]:
        response = self._request_with_retries(
            lambda: self._client.get("/queue"),
            message="ComfyUI queue lookup failed",
            max_attempts=self._settings.comfyui_transient_retry_max_attempts,
        )
        return dict(response.json())

    def wait_for_completion(
        self,
        prompt_id: str,
        timeout_seconds: int,
        poll_interval_seconds: int,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        transient_failures = 0
        while time.monotonic() < deadline:
            try:
                response = self._request_with_retries(
                    lambda: self._client.get(f"/history/{prompt_id}"),
                    message="ComfyUI history lookup failed",
                    max_attempts=1,
                    raise_transient=True,
                )
                history = dict(response.json())
                transient_failures = 0
            except ComfyUITransientError as exc:
                retry_in = self._polling_retry_seconds(
                    poll_interval_seconds,
                    transient_failures,
                )
                transient_failures += 1
                logger.warning(
                    "ComfyUI transient error while polling history prompt_id=%s status=%s "
                    "retry_in=%s",
                    prompt_id,
                    exc.status,
                    retry_in,
                )
                time.sleep(min(retry_in, max(deadline - time.monotonic(), 0)))
                continue

            record = history.get(prompt_id)
            if isinstance(record, dict):
                self._raise_if_failed(record)
                outputs = record.get("outputs")
                if isinstance(outputs, dict) and outputs:
                    return record
                status = record.get("status")
                if isinstance(status, dict) and status.get("completed") is True:
                    self._raise_if_failed(record)
                    raise ComfyUIExecutionError("ComfyUI completed without outputs")
            time.sleep(poll_interval_seconds)
        raise ComfyUITimeoutError(f"ComfyUI prompt timed out prompt_id={prompt_id}")

    def download_output(
        self,
        *,
        filename: str,
        subfolder: str,
        type_: str,
        destination: Path,
    ) -> Path:
        response = self._request_with_retries(
            lambda: self._client.get(
                "/view",
                params={"filename": filename, "subfolder": subfolder, "type": type_},
            ),
            message="ComfyUI output download failed",
            max_attempts=5,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination

    def find_video_output(self, history: dict[str, Any]) -> ComfyUIOutputFile:
        for value in self._walk_values(history):
            if not isinstance(value, dict):
                continue
            filename = value.get("filename") or value.get("name")
            if not isinstance(filename, str) or not filename.lower().endswith(".mp4"):
                continue
            subfolder = value.get("subfolder")
            type_ = value.get("type")
            return _normalise_output_file(
                filename=filename,
                subfolder=subfolder if isinstance(subfolder, str) else "",
                type_=type_ if isinstance(type_, str) else "output",
            )

        for value in self._walk_values(history):
            if isinstance(value, str) and value.lower().endswith(".mp4"):
                return _normalise_output_file(filename=value, subfolder="", type_="output")

        output_keys = sorted(str(key) for key in history.get("outputs", {}).keys())
        raise ComfyUIExecutionError(f"No mp4 output found in ComfyUI history outputs={output_keys}")

    def _upload_file(
        self,
        *,
        endpoint: str,
        field_name: str,
        local_path: Path,
        filename: str,
        subfolder: str,
    ) -> ComfyUIUploadedFile:
        def request() -> httpx.Response:
            with local_path.open("rb") as file_obj:
                return self._client.post(
                    endpoint,
                    data={"type": "input", "subfolder": subfolder, "overwrite": "true"},
                    files={field_name: (filename, file_obj, "application/octet-stream")},
                )

        response = self._request_with_retries(
            request,
            message=f"ComfyUI upload failed endpoint={endpoint}",
            max_attempts=self._settings.comfyui_transient_retry_max_attempts,
        )
        data = response.json()
        uploaded_name = data.get("name") or data.get("filename") or filename
        uploaded_subfolder = data.get("subfolder")
        uploaded_type = data.get("type")
        if not isinstance(uploaded_name, str) or not uploaded_name:
            raise ComfyUIExecutionError("ComfyUI upload did not return a filename")
        return ComfyUIUploadedFile(
            filename=uploaded_name,
            subfolder=uploaded_subfolder if isinstance(uploaded_subfolder, str) else subfolder,
            type=uploaded_type if isinstance(uploaded_type, str) else "input",
        )

    def _request_with_retries(
        self,
        request: Callable[[], httpx.Response],
        *,
        message: str,
        max_attempts: int,
        raise_transient: bool = False,
    ) -> httpx.Response:
        attempts = max(max_attempts, 1)
        for attempt in range(1, attempts + 1):
            try:
                response = request()
            except TRANSIENT_NETWORK_ERRORS as exc:
                status = _network_error_status(exc)
                error_message = f"{message}: {status}"
                if attempt >= attempts:
                    if raise_transient:
                        raise ComfyUITransientError(error_message, status=status) from exc
                    raise ComfyUIExecutionError(error_message) from exc
                retry_in = self._transient_retry_seconds(attempt)
                logger.warning(
                    "ComfyUI transient network error message=%s status=%s attempt=%s/%s "
                    "retry_in=%s",
                    message,
                    status,
                    attempt,
                    attempts,
                    retry_in,
                )
                time.sleep(retry_in)
                continue

            if _is_transient_response(response):
                status = _http_status_summary(response)
                error_message = f"{message}: {status}"
                if attempt >= attempts:
                    if raise_transient:
                        raise ComfyUITransientError(error_message, status=str(response.status_code))
                    raise ComfyUIExecutionError(error_message)
                retry_in = self._transient_retry_seconds(attempt)
                logger.warning(
                    "ComfyUI transient HTTP error message=%s status=%s attempt=%s/%s retry_in=%s",
                    message,
                    response.status_code,
                    attempt,
                    attempts,
                    retry_in,
                )
                time.sleep(retry_in)
                continue

            self._raise_for_response(response, message)
            return response

        raise ComfyUIExecutionError(f"{message}: retry attempts exhausted")

    def _raise_for_response(self, response: httpx.Response, message: str) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            summary = _http_status_summary(response)
            body = _safe_response_body(response)
            if body:
                raise ComfyUIExecutionError(f"{message}: {summary}: {body}") from exc
            raise ComfyUIExecutionError(f"{message}: {summary}") from exc

    def _transient_retry_seconds(self, attempt: int) -> float:
        base = max(self._settings.comfyui_transient_retry_backoff_seconds, 1)
        max_seconds = max(self._settings.comfyui_transient_retry_backoff_max_seconds, base)
        return float(min(base * (2 ** (attempt - 1)), max_seconds))

    def _polling_retry_seconds(self, poll_interval_seconds: int, failures: int) -> float:
        base = max(poll_interval_seconds, self._settings.comfyui_transient_retry_backoff_seconds)
        max_seconds = max(self._settings.comfyui_transient_retry_backoff_max_seconds, base)
        return float(min(base * (2**failures), max_seconds))

    def _raise_if_failed(self, record: dict[str, Any]) -> None:
        status = record.get("status")
        if not isinstance(status, dict):
            return
        status_str = str(status.get("status_str") or "").lower()
        if status_str in {"error", "failed", "failure"}:
            messages = status.get("messages")
            raise ComfyUIExecutionError(f"ComfyUI execution failed: {messages}")

    def _walk_values(self, value: Any) -> list[Any]:
        values: list[Any] = [value]
        if isinstance(value, dict):
            for item in value.values():
                values.extend(self._walk_values(item))
        elif isinstance(value, list):
            for item in value:
                values.extend(self._walk_values(item))
        return values


def _normalise_output_file(*, filename: str, subfolder: str, type_: str) -> ComfyUIOutputFile:
    if "/" not in filename or subfolder:
        return ComfyUIOutputFile(filename=filename, subfolder=subfolder, type=type_)

    path = Path(filename)
    return ComfyUIOutputFile(
        filename=path.name,
        subfolder=str(path.parent),
        type=type_,
    )


def _is_transient_response(response: httpx.Response) -> bool:
    return response.status_code in TRANSIENT_HTTP_STATUSES


def _http_status_summary(response: httpx.Response) -> str:
    phrase = response.reason_phrase
    if not phrase:
        try:
            phrase = HTTPStatus(response.status_code).phrase
        except ValueError:
            phrase = ""
    return f"HTTP {response.status_code} {phrase}".strip()


def _network_error_status(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "network timeout"
    return exc.__class__.__name__


def _safe_response_body(response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "").lower()
    text = response.text.strip()
    if not text:
        return ""
    if "html" in content_type or text.lower().startswith(("<!doctype", "<html")):
        return ""
    if "json" in content_type:
        try:
            text = json.dumps(response.json(), ensure_ascii=False)
        except ValueError:
            pass
    text = " ".join(text.split())
    return text[:500]
