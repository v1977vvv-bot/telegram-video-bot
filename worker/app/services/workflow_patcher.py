from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

REQUIRED_NODE_IDS = ("313", "125", "245", "246", "270", "194", "317")
MODEL_PROFILE_NODE_IDS = ("120", "122")


class ComfyUIUploadedFileRef(Protocol):
    filename: str
    subfolder: str
    type: str


@dataclass(frozen=True)
class _WorkflowUploadedFileRef:
    filename: str
    subfolder: str
    type: str = "input"


@dataclass(frozen=True)
class ComfyUIModelProfile:
    name: str
    wan_video_model: str
    infinite_talk_model: str
    experimental: bool = False


COMFYUI_MODEL_PROFILES: dict[str, ComfyUIModelProfile] = {
    "gguf_q8_480p": ComfyUIModelProfile(
        name="gguf_q8_480p",
        wan_video_model="WanVideo/wan2.1-i2v-14b-480p-Q8_0.gguf",
        infinite_talk_model="WanVideo/InfiniteTalk/Wan2_1-InfiniteTalk_Single_Q8.gguf",
    ),
    "fp8_480p": ComfyUIModelProfile(
        name="fp8_480p",
        wan_video_model="wan2.1_i2v_480p_14B_fp8_e4m3fn.safetensors",
        infinite_talk_model="Wan2_1-InfiniteTalk-Multi_fp8_e4m3fn_scaled_KJ.safetensors",
    ),
    "fp8_720p": ComfyUIModelProfile(
        name="fp8_720p",
        wan_video_model="wan2.1_i2v_720p_14B_fp8_e4m3fn.safetensors",
        infinite_talk_model="Wan2_1-InfiniteTalk-Multi_fp8_e4m3fn_scaled_KJ.safetensors",
        experimental=True,
    ),
}


class WorkflowPatchError(RuntimeError):
    pass


def patch_infinite_talk_workflow(
    *,
    workflow_path: Path,
    image_upload: ComfyUIUploadedFileRef,
    audio_upload: ComfyUIUploadedFileRef,
    width: int,
    height: int,
    fps: int,
    frame_count: int,
    filename_prefix: str,
    model_profile: str = "gguf_q8_480p",
) -> dict[str, Any]:
    workflow = json.loads(workflow_path.read_text())
    patched = copy.deepcopy(workflow)
    _validate_required_nodes(patched)
    _apply_model_profile(patched, model_profile)

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
    model_profile: str = "gguf_q8_480p",
) -> dict[str, Any]:
    patched = patch_infinite_talk_workflow(
        workflow_path=workflow_path,
        image_upload=_WorkflowUploadedFileRef(
            filename=image_filename,
            subfolder=input_subfolder,
        ),
        audio_upload=_WorkflowUploadedFileRef(
            filename=audio_filename,
            subfolder=input_subfolder,
        ),
        width=width,
        height=height,
        fps=fps,
        frame_count=frame_count,
        filename_prefix=f"{output_subfolder}/debug_preview",
        model_profile=model_profile,
    )
    return extract_infinite_talk_node_values(patched)


def validate_infinite_talk_workflow(workflow_path: Path) -> dict[str, Any]:
    workflow = json.loads(workflow_path.read_text())
    _validate_required_nodes(workflow)
    return extract_infinite_talk_node_values(workflow)


def extract_infinite_talk_node_values(workflow: dict[str, Any]) -> dict[str, Any]:
    nodes: dict[str, Any] = {}
    for node_id in (*MODEL_PROFILE_NODE_IDS, *REQUIRED_NODE_IDS):
        if node_id not in workflow:
            continue
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
                    "model",
                    "base_precision",
                    "quantization",
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


def _apply_model_profile(workflow: dict[str, Any], model_profile: str) -> None:
    profile = resolve_comfyui_model_profile(model_profile)
    _validate_model_profile_nodes(workflow)
    workflow["120"]["inputs"]["model"] = profile.infinite_talk_model
    workflow["122"]["inputs"]["model"] = profile.wan_video_model


def resolve_comfyui_model_profile(model_profile: str) -> ComfyUIModelProfile:
    profile_name = model_profile.strip().lower()
    try:
        return COMFYUI_MODEL_PROFILES[profile_name]
    except KeyError as exc:
        allowed = ", ".join(sorted(COMFYUI_MODEL_PROFILES))
        raise WorkflowPatchError(
            f"Unknown COMFYUI_MODEL_PROFILE={model_profile}; use {allowed}"
        ) from exc


def _validate_model_profile_nodes(workflow: dict[str, Any]) -> None:
    missing = [node_id for node_id in MODEL_PROFILE_NODE_IDS if node_id not in workflow]
    if missing:
        raise WorkflowPatchError(
            f"Workflow is missing model profile node ids: {', '.join(missing)}"
        )
    for node_id in MODEL_PROFILE_NODE_IDS:
        node = workflow[node_id]
        if not isinstance(node, dict):
            raise WorkflowPatchError(f"Workflow node {node_id} must be an object")
        if not isinstance(node.setdefault("inputs", {}), dict):
            raise WorkflowPatchError(f"Workflow node {node_id} inputs must be an object")


def _comfy_file_reference(upload: ComfyUIUploadedFileRef) -> str:
    if upload.subfolder:
        return f"{upload.subfolder}/{upload.filename}"
    return upload.filename


def _audio_ui_reference(upload: ComfyUIUploadedFileRef) -> str:
    return (
        f"/api/view?filename={quote(upload.filename)}"
        f"&type={quote(upload.type)}"
        f"&subfolder={quote(upload.subfolder)}"
    )
