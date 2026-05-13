from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from worker.app.services.comfyui_client import ComfyUIUploadedFile

REQUIRED_NODE_IDS = ("313", "125", "245", "246", "270", "194", "317")


class WorkflowPatchError(RuntimeError):
    pass


def patch_infinite_talk_workflow(
    *,
    workflow_path: Path,
    image_upload: ComfyUIUploadedFile,
    audio_upload: ComfyUIUploadedFile,
    width: int,
    height: int,
    fps: int,
    frame_count: int,
    filename_prefix: str,
) -> dict[str, Any]:
    workflow = json.loads(workflow_path.read_text())
    patched = copy.deepcopy(workflow)
    _validate_required_nodes(patched)

    patched["313"].setdefault("inputs", {})["image"] = _comfy_file_reference(image_upload)

    audio_inputs = patched["125"].setdefault("inputs", {})
    audio_reference = _comfy_file_reference(audio_upload)
    audio_inputs["audio"] = audio_reference
    if "audioUI" in audio_inputs:
        audio_inputs["audioUI"] = _audio_ui_reference(audio_upload)

    patched["245"].setdefault("inputs", {})["value"] = width
    patched["246"].setdefault("inputs", {})["value"] = height
    patched["270"].setdefault("inputs", {})["value"] = frame_count
    patched["194"].setdefault("inputs", {})["fps"] = fps
    patched["317"].setdefault("inputs", {})["frame_rate"] = fps
    patched["317"].setdefault("inputs", {})["filename_prefix"] = filename_prefix
    patched["317"].setdefault("inputs", {})["trim_to_audio"] = True
    return patched


def preview_infinite_talk_patch_values(
    *,
    workflow_path: Path,
    image_filename: str,
    audio_filename: str,
    width: int,
    height: int,
    fps: int,
    frame_count: int,
    input_subfolder: str,
    output_subfolder: str,
) -> dict[str, Any]:
    patched = patch_infinite_talk_workflow(
        workflow_path=workflow_path,
        image_upload=ComfyUIUploadedFile(filename=image_filename, subfolder=input_subfolder),
        audio_upload=ComfyUIUploadedFile(filename=audio_filename, subfolder=input_subfolder),
        width=width,
        height=height,
        fps=fps,
        frame_count=frame_count,
        filename_prefix=f"{output_subfolder}/debug_preview",
    )
    return extract_infinite_talk_node_values(patched)


def validate_infinite_talk_workflow(workflow_path: Path) -> dict[str, Any]:
    workflow = json.loads(workflow_path.read_text())
    _validate_required_nodes(workflow)
    return extract_infinite_talk_node_values(workflow)


def extract_infinite_talk_node_values(workflow: dict[str, Any]) -> dict[str, Any]:
    nodes: dict[str, Any] = {}
    for node_id in REQUIRED_NODE_IDS:
        node = workflow[node_id]
        inputs = node.get("inputs", {})
        nodes[node_id] = {
            "class_type": node.get("class_type"),
            "inputs": {
                key: inputs.get(key)
                for key in (
                    "image",
                    "audio",
                    "audioUI",
                    "value",
                    "fps",
                    "frame_rate",
                    "filename_prefix",
                    "trim_to_audio",
                )
                if key in inputs
            },
        }
    return {"nodes": nodes}


def _validate_required_nodes(workflow: dict[str, Any]) -> None:
    missing = [node_id for node_id in REQUIRED_NODE_IDS if node_id not in workflow]
    if missing:
        raise WorkflowPatchError(f"Workflow is missing required node ids: {', '.join(missing)}")
    for node_id in REQUIRED_NODE_IDS:
        node = workflow[node_id]
        if not isinstance(node, dict):
            raise WorkflowPatchError(f"Workflow node {node_id} must be an object")
        if not isinstance(node.setdefault("inputs", {}), dict):
            raise WorkflowPatchError(f"Workflow node {node_id} inputs must be an object")


def _comfy_file_reference(upload: ComfyUIUploadedFile) -> str:
    if upload.subfolder:
        return f"{upload.subfolder}/{upload.filename}"
    return upload.filename


def _audio_ui_reference(upload: ComfyUIUploadedFile) -> str:
    return (
        f"/api/view?filename={quote(upload.filename)}"
        f"&type={quote(upload.type)}"
        f"&subfolder={quote(upload.subfolder)}"
    )
