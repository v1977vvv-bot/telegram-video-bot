from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from shared.app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class ComfyUIError(RuntimeError):
    pass


class ComfyUITimeoutError(ComfyUIError):
    pass


class ComfyUIExecutionError(ComfyUIError):
    pass


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
        response = self._client.get("/system_stats")
        self._raise_for_response(response, "ComfyUI healthcheck failed")
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
            logger.info("ComfyUI /upload/image rejected audio filename=%s", filename)
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
        response = self._client.post("/prompt", json=payload)
        self._raise_for_response(response, "ComfyUI prompt queue failed")
        data = response.json()
        prompt_id = data.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            raise ComfyUIExecutionError("ComfyUI did not return prompt_id")
        return prompt_id

    def get_history(self, prompt_id: str) -> dict[str, Any]:
        response = self._client.get(f"/history/{prompt_id}")
        self._raise_for_response(response, "ComfyUI history lookup failed")
        return dict(response.json())

    def get_queue(self) -> dict[str, Any]:
        response = self._client.get("/queue")
        self._raise_for_response(response, "ComfyUI queue lookup failed")
        return dict(response.json())

    def wait_for_completion(
        self,
        prompt_id: str,
        timeout_seconds: int,
        poll_interval_seconds: int,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            history = self.get_history(prompt_id)
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
        response = self._client.get(
            "/view",
            params={"filename": filename, "subfolder": subfolder, "type": type_},
        )
        self._raise_for_response(response, "ComfyUI output download failed")
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
        with local_path.open("rb") as file_obj:
            response = self._client.post(
                endpoint,
                data={"type": "input", "subfolder": subfolder, "overwrite": "true"},
                files={field_name: (filename, file_obj, "application/octet-stream")},
            )
        self._raise_for_response(response, f"ComfyUI upload failed endpoint={endpoint}")
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

    def _raise_for_response(self, response: httpx.Response, message: str) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = response.text[:500]
            raise ComfyUIExecutionError(f"{message}: HTTP {response.status_code}: {body}") from exc

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
