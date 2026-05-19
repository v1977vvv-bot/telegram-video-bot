#!/usr/bin/env bash
set -euo pipefail

COMFYUI_DIR="${COMFYUI_DIR:-/workspace/ComfyUI}"

if [ -d "/runpod-volume" ] && [ -w "/runpod-volume" ]; then
  MODELS_DIR="${COMFYUI_MODELS_DIR:-/runpod-volume/ComfyUI/models}"
else
  MODELS_DIR="${COMFYUI_MODELS_DIR:-${COMFYUI_DIR}/models}"
fi

echo "[models] COMFYUI_DIR=${COMFYUI_DIR}"
echo "[models] MODELS_DIR=${MODELS_DIR}"

mkdir -p "${MODELS_DIR}"

if [ "${MODELS_DIR}" != "${COMFYUI_DIR}/models" ]; then
  rm -rf "${COMFYUI_DIR}/models"
  ln -s "${MODELS_DIR}" "${COMFYUI_DIR}/models"
fi

download() {
  local url="$1"
  local target="$2"
  local min_size_mb="${3:-1}"

  mkdir -p "$(dirname "$target")"

  if [ -s "$target" ]; then
    local size_mb
    size_mb=$(du -m "$target" | awk '{print $1}')
    if [ "$size_mb" -ge "$min_size_mb" ]; then
      echo "[models] exists: $target (${size_mb} MB)"
      return 0
    fi
    echo "[models] too small, re-downloading: $target (${size_mb} MB)"
    rm -f "$target"
  fi

  echo "[models] downloading: $url"
  echo "[models] target: $target"

  rm -f "${target}.tmp"

  wget \
    --continue \
    --tries=20 \
    --timeout=60 \
    --read-timeout=60 \
    --waitretry=10 \
    --retry-connrefused \
    --progress=bar:force:noscroll \
    -O "${target}.tmp" \
    "$url"

  mv "${target}.tmp" "$target"

  local final_size_mb
  final_size_mb=$(du -m "$target" | awk '{print $1}')
  if [ "$final_size_mb" -lt "$min_size_mb" ]; then
    echo "[models] ERROR: downloaded file is too small: $target (${final_size_mb} MB)"
    exit 1
  fi

  echo "[models] downloaded: $target (${final_size_mb} MB)"
}

download \
  "https://huggingface.co/city96/Wan2.1-I2V-14B-480P-gguf/resolve/main/wan2.1-i2v-14b-480p-Q8_0.gguf" \
  "${MODELS_DIR}/diffusion_models/WanVideo/wan2.1-i2v-14b-480p-Q8_0.gguf" \
  16000

download \
  "https://huggingface.co/Kijai/WanVideo_comfy_GGUF/resolve/main/InfiniteTalk/Wan2_1-InfiniteTalk_Single_Q8.gguf" \
  "${MODELS_DIR}/diffusion_models/WanVideo/InfiniteTalk/Wan2_1-InfiniteTalk_Single_Q8.gguf" \
  2000

download \
  "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan2_1_VAE_bf16.safetensors" \
  "${MODELS_DIR}/vae/wanvideo/Wan2_1_VAE_bf16.safetensors" \
  200

download \
  "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-bf16.safetensors" \
  "${MODELS_DIR}/text_encoders/umt5-xxl-enc-bf16.safetensors" \
  10000

download \
  "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors" \
  "${MODELS_DIR}/clip_vision/clip_vision_h.safetensors" \
  1000

download \
  "https://huggingface.co/Kijai/MelBandRoFormer_comfy/resolve/main/MelBandRoformer_fp16.safetensors" \
  "${MODELS_DIR}/diffusion_models/MelBandRoformer/MelBandRoformer_fp16.safetensors" \
  400

download \
  "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors" \
  "${MODELS_DIR}/loras/WanVideo/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors" \
  600

echo "[models] all required models are present"
