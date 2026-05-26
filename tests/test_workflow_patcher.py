from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from worker.app.services.workflow_patcher import (
    WorkflowPatchError,
    patch_infinite_talk_workflow,
)


class WorkflowModelProfileTests(unittest.TestCase):
    def test_fp8_480p_profile_uses_fp8_model_names(self) -> None:
        patched = _patch_profile("fp8_480p")

        self.assertEqual(
            patched["122"]["inputs"]["model"],
            "wan2.1_i2v_480p_14B_fp8_e4m3fn.safetensors",
        )
        self.assertEqual(
            patched["120"]["inputs"]["model"],
            "Wan2_1-InfiniteTalk-Multi_fp8_e4m3fn_scaled_KJ.safetensors",
        )

    def test_gguf_q8_480p_profile_preserves_current_model_names(self) -> None:
        patched = _patch_profile("gguf_q8_480p")

        self.assertEqual(
            patched["122"]["inputs"]["model"],
            "WanVideo/wan2.1-i2v-14b-480p-Q8_0.gguf",
        )
        self.assertEqual(
            patched["120"]["inputs"]["model"],
            "WanVideo/InfiniteTalk/Wan2_1-InfiniteTalk_Single_Q8.gguf",
        )

    def test_fp8_720p_profile_is_available_as_experimental_placeholder(self) -> None:
        patched = _patch_profile("fp8_720p")

        self.assertEqual(
            patched["122"]["inputs"]["model"],
            "wan2.1_i2v_720p_14B_fp8_e4m3fn.safetensors",
        )
        self.assertEqual(
            patched["120"]["inputs"]["model"],
            "Wan2_1-InfiniteTalk-Multi_fp8_e4m3fn_scaled_KJ.safetensors",
        )

    def test_unknown_model_profile_is_rejected(self) -> None:
        with self.assertRaises(WorkflowPatchError):
            _patch_profile("bad_profile")


def _patch_profile(model_profile: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp_dir:
        workflow_path = Path(tmp_dir) / "workflow.json"
        workflow_path.write_text(json.dumps(_base_workflow()), encoding="utf-8")
        return patch_infinite_talk_workflow(
            workflow_path=workflow_path,
            image_upload=UploadedFile(filename="image.png", subfolder="input"),
            audio_upload=UploadedFile(filename="audio.wav", subfolder="input"),
            width=480,
            height=480,
            fps=25,
            frame_count=125,
            filename_prefix="InfiniteTalk/test",
            model_profile=model_profile,
        )


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    subfolder: str
    type: str = "input"


def _base_workflow() -> dict[str, dict]:
    return {
        "120": {
            "class_type": "MultiTalkModelLoader",
            "inputs": {
                "model": "WanVideo/InfiniteTalk/Wan2_1-InfiniteTalk_Single_Q8.gguf",
            },
        },
        "122": {
            "class_type": "WanVideoModelLoader",
            "inputs": {
                "model": "WanVideo/wan2.1-i2v-14b-480p-Q8_0.gguf",
                "base_precision": "fp16_fast",
                "quantization": "disabled",
            },
        },
        "125": {"class_type": "LoadAudio", "inputs": {}},
        "194": {"class_type": "WanVideoInjectMultiTalk", "inputs": {}},
        "245": {"class_type": "PrimitiveInt", "inputs": {}},
        "246": {"class_type": "PrimitiveInt", "inputs": {}},
        "270": {"class_type": "PrimitiveInt", "inputs": {}},
        "313": {"class_type": "LoadImage", "inputs": {}},
        "317": {"class_type": "VHS_VideoCombine", "inputs": {}},
    }


if __name__ == "__main__":
    unittest.main()
