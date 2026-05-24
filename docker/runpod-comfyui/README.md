# SynzAI RunPod ComfyUI image

Docker image for RunPod pods.

Goal:
- Start ComfyUI on `0.0.0.0:8188`
- Avoid installing ComfyUI and Python dependencies during every pod startup
- Provide a stable base image for SynzAI generation workers

RunPod template command:

```bash
/start.sh
```

Optional RTX 5090 experiment:

```env
RUNPOD_EXPERIMENTAL_LOW_VRAM_STARTUP=true
```

When enabled, `/start.sh` adds `--lowvram` to the ComfyUI startup command. The default
is `false`, so RTX 4090 production startup remains unchanged. Startup logs include GPU
name, VRAM, NVIDIA driver/CUDA information, torch version, torch CUDA version, and the
exact ComfyUI command.

RTX PRO CUDA 13 FP8 image profile:

```text
ghcr.io/v1977vvv-bot/synzai-comfyui:rtx-pro-cu130-fp8
```

This tag is built from `Dockerfile.rtxpro-cu130` with FP8 runtime download defaults:

```env
DOWNLOAD_GGUF_Q8=0
DOWNLOAD_WAN_FP8_480P=1
DOWNLOAD_WAN_FP8_720P=0
DOWNLOAD_INFINITETALK_FP8=1
```

The production `latest` tag is not changed. The existing
`rtx-pro-cu130` tag remains buildable with FP8 downloads disabled by default.

FP8 model flags consumed by `/download_models.sh`:

```env
DOWNLOAD_GGUF_Q8=0
DOWNLOAD_WAN_FP8_480P=1
DOWNLOAD_WAN_FP8_720P=0
DOWNLOAD_INFINITETALK_FP8=1
```

`DOWNLOAD_GGUF_Q8` defaults to `1` for production/latest and the non-FP8 CUDA 13
image. The `rtx-pro-cu130-fp8` workflow sets it to `0`, so the FP8 profile does not
download the two baseline GGUF Q8 diffusion models unless explicitly enabled.

FP8 diffusion files are stored directly in:

```text
/workspace/ComfyUI/models/diffusion_models/
```

If `/runpod-volume` is writable, `/workspace/ComfyUI/models` is symlinked to
`/runpod-volume/ComfyUI/models`, so the same relative diffusion model paths apply.

Test order:

1. Test `wan2.1_i2v_480p_14B_fp8_e4m3fn.safetensors` first.
2. Test `wan2.1_i2v_720p_14B_fp8_e4m3fn.safetensors` second by setting
   `DOWNLOAD_WAN_FP8_720P=1`.
3. Compare against the GGUF Q8 baseline: 531.16 seconds for a 60 second video.

Startup logs list available WanVideo and InfiniteTalk files under
`models/diffusion_models`, including FP8 `.safetensors` files.

ComfyUI URL:

```text

https://<pod_id>-8188.proxy.runpod.net

```
