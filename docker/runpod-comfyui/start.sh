#!/usr/bin/env bash
set -euo pipefail

log() {
  echo "[start] $*"
}

bool_is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

log_gpu_info() {
  if command -v nvidia-smi >/dev/null 2>&1; then
    log "GPU inventory:"
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits \
      | while IFS= read -r line; do
          log "  ${line} MiB"
        done
    log "NVIDIA driver version: $(nvidia-smi --query-gpu=driver_version --format=csv,noheader,nounits | head -n 1 || true)"
    nvidia_cuda_version="$(nvidia-smi | sed -n 's/.*CUDA Version: \([^ |]*\).*/\1/p' | head -n 1 || true)"
    if [ -n "${nvidia_cuda_version}" ]; then
      log "nvidia-smi CUDA version: ${nvidia_cuda_version}"
    fi
  else
    log "nvidia-smi not found"
  fi
}

log_torch_info() {
  log "===== GPU / CUDA / Torch diagnostic ====="

  python - <<'PY' || true
try:
    import torch

    print(f"[start] torch version: {torch.__version__}")
    print(f"[start] torch CUDA version: {torch.version.cuda}")
    print(f"[start] torch CUDA available: {torch.cuda.is_available()}")

    if torch.cuda.is_available():
        print(f"[start] torch device: {torch.cuda.get_device_name(0)}")
        print(f"[start] torch capability: {torch.cuda.get_device_capability(0)}")
        print(f"[start] torch cuda arch list: {torch.cuda.get_arch_list()}")
        print(f"[start] cudnn version: {torch.backends.cudnn.version()}")

except Exception as exc:
    print(f"[start] torch diagnostics unavailable: {exc.__class__.__name__}: {exc}")
PY

  log "========================================="
}

log_diffusion_model_inventory() {
  local diffusion_dir="/workspace/ComfyUI/models/diffusion_models"
  log "===== WanVideo / InfiniteTalk diffusion model inventory ====="
  if [ ! -d "${diffusion_dir}" ]; then
    log "diffusion_models directory not found: ${diffusion_dir}"
    log "============================================================="
    return 0
  fi

  local found=0
  while IFS= read -r model_file; do
    found=1
    local size
    size="$(du -h "${model_file}" | awk '{print $1}')"
    log "${size} ${model_file}"
  done < <(
    find "${diffusion_dir}" -type f \( \
      -iname '*wan*' -o \
      -iname '*infinitetalk*' \
    \) | sort
  )

  if [ "${found}" = "0" ]; then
    log "No WanVideo/InfiniteTalk diffusion model files found in ${diffusion_dir}"
  fi
  log "============================================================="
}

cd /workspace/ComfyUI

log_gpu_info
log_torch_info

if [ "${DOWNLOAD_MODELS:-1}" = "1" ]; then
  /download_models.sh
else
  echo "[models] DOWNLOAD_MODELS=0, skipping model download"
fi

log_diffusion_model_inventory

comfyui_args=(main.py --listen 0.0.0.0 --port 8188)

if bool_is_true "${RUNPOD_EXPERIMENTAL_LOW_VRAM_STARTUP:-false}"; then
  log "RUNPOD_EXPERIMENTAL_LOW_VRAM_STARTUP=true, enabling ComfyUI --lowvram"
  comfyui_args+=(--lowvram)
fi

if [ -n "${COMFYUI_EXTRA_ARGS:-}" ]; then
  # Intentionally split COMFYUI_EXTRA_ARGS so RunPod templates can pass multiple flags.
  read -r -a extra_args <<< "${COMFYUI_EXTRA_ARGS}"
  comfyui_args+=("${extra_args[@]}")
fi

log "ComfyUI startup command: python ${comfyui_args[*]}"
exec python "${comfyui_args[@]}"
