#!/usr/bin/env bash
set -euo pipefail

COMFYUI_DIR="${COMFYUI_DIR:-}"
COMFYUI_PORT="${COMFYUI_PORT:-8188}"
COMFYUI_EXTRA_ARGS="${COMFYUI_EXTRA_ARGS:---use-sage-attention}"
SKIP_MODEL_DOWNLOADS="${SKIP_MODEL_DOWNLOADS:-false}"
BOOTSTRAP_ONLY="${BOOTSTRAP_ONLY:-false}"
KILL_EXISTING_COMFYUI="${KILL_EXISTING_COMFYUI:-true}"

log() {
  echo "[bootstrap] $*"
}

error() {
  echo "[bootstrap] ERROR: $*" >&2
}

is_valid_comfyui_dir() {
  local dir="$1"
  local markers=0

  [ -f "$dir/main.py" ] || return 1
  [ -d "$dir/comfy" ] && markers=$((markers + 1))
  [ -d "$dir/custom_nodes" ] && markers=$((markers + 1))
  [ -f "$dir/nodes.py" ] && markers=$((markers + 1))
  [ -f "$dir/execution.py" ] && markers=$((markers + 1))
  [ -f "$dir/server.py" ] && markers=$((markers + 1))

  [ "$markers" -ge 3 ]
}

detect_comfyui_dir() {
  local candidates=(
    "/workspace/ComfyUI"
    "/workspace/comfyui"
    "/ComfyUI"
    "/comfyui"
    "/app/ComfyUI"
    "/app/comfyui"
    "/root/ComfyUI"
    "/root/comfyui"
  )
  local candidate
  local searched
  local found_candidates

  searched="${candidates[*]}"
  for candidate in "${candidates[@]}"; do
    if is_valid_comfyui_dir "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done

  found_candidates="$(
    find / -type d \( -iname "ComfyUI" -o -iname "comfyui" \) \
      -not -path "*/site-packages/*" \
      -not -path "*/.venv/*" \
      -not -path "*/venv/*" \
      2>/dev/null || true
  )"

  while IFS= read -r candidate; do
    [ -n "$candidate" ] || continue
    searched="${searched} ${candidate}"
    if is_valid_comfyui_dir "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done <<<"$found_candidates"

  error "ComfyUI directory not found."
  error "Searched candidates: $searched"
  exit 1
}

is_true() {
  case "${1:-}" in
    true | TRUE | 1 | yes | YES)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

ensure_dir() {
  local dir="$1"
  mkdir -p "$dir"
  log "ensured dir: $dir"
}

download_if_missing() {
  local path="$1"
  local url="$2"
  local tmp="${path}.tmp"

  ensure_dir "$(dirname "$path")"

  if [ -s "$path" ]; then
    log "exists, skipping: $path"
    return 0
  fi

  if [ -e "$path" ]; then
    log "zero-size file, redownloading: $path"
    rm -f "$path"
  fi

  rm -f "$tmp"
  log "downloading: $url"
  log "target: $path"

  if command -v wget >/dev/null 2>&1; then
    wget --progress=bar:force:noscroll -O "$tmp" "$url"
  elif command -v curl >/dev/null 2>&1; then
    curl -L --fail -o "$tmp" "$url"
  else
    error "neither wget nor curl is available"
    exit 1
  fi

  if [ ! -s "$tmp" ]; then
    rm -f "$tmp"
    error "downloaded file is missing or empty: $tmp"
    exit 1
  fi

  mv "$tmp" "$path"
  log "downloaded: $path"
}

check_required_file() {
  local path="$1"
  local size

  if [ ! -s "$path" ]; then
    error "missing or empty required model: $path"
    exit 1
  fi

  size="$(du -h "$path" | awk '{print $1}')"
  log "verified: $path ($size)"
}

start_comfyui() {
  local python_bin

  if command -v python >/dev/null 2>&1; then
    python_bin="python"
  elif command -v python3 >/dev/null 2>&1; then
    python_bin="python3"
  else
    error "python/python3 is not available"
    exit 1
  fi

  if is_true "$KILL_EXISTING_COMFYUI"; then
    log "stopping existing ComfyUI process if present"
    pkill -f "python main.py" || true
    pkill -f "python3 main.py" || true
    sleep 2
  fi

  cd "$COMFYUI_DIR"
  log "starting ComfyUI on 0.0.0.0:${COMFYUI_PORT}"
  log "extra args: ${COMFYUI_EXTRA_ARGS}"
  # Intentionally split COMFYUI_EXTRA_ARGS so template startup can pass multiple flags.
  # shellcheck disable=SC2086
  exec "$python_bin" main.py --listen 0.0.0.0 --port "$COMFYUI_PORT" $COMFYUI_EXTRA_ARGS
}

if [ -n "$COMFYUI_DIR" ]; then
  if ! is_valid_comfyui_dir "$COMFYUI_DIR"; then
    error "Invalid ComfyUI directory: $COMFYUI_DIR"
    error "Expected main.py plus ComfyUI markers: comfy/, custom_nodes/, nodes.py, execution.py, server.py"
    exit 1
  fi
else
  COMFYUI_DIR="$(detect_comfyui_dir)"
fi

MODEL_DIRS=(
  "$COMFYUI_DIR/models/diffusion_models/WanVideo"
  "$COMFYUI_DIR/models/diffusion_models/WanVideo/InfiniteTalk"
  "$COMFYUI_DIR/models/vae/wanvideo"
  "$COMFYUI_DIR/models/text_encoders"
  "$COMFYUI_DIR/models/clip_vision"
  "$COMFYUI_DIR/models/diffusion_models/MelBandRoformer"
  "$COMFYUI_DIR/models/loras/WanVideo/Lightx2v"
)

REQUIRED_MODELS=(
  "$COMFYUI_DIR/models/diffusion_models/WanVideo/wan2.1-i2v-14b-480p-Q8_0.gguf|https://huggingface.co/city96/Wan2.1-I2V-14B-480P-gguf/resolve/main/wan2.1-i2v-14b-480p-Q8_0.gguf"
  "$COMFYUI_DIR/models/diffusion_models/WanVideo/InfiniteTalk/Wan2_1-InfiniteTalk_Single_Q8.gguf|https://huggingface.co/Kijai/WanVideo_comfy_GGUF/resolve/main/InfiniteTalk/Wan2_1-InfiniteTalk_Single_Q8.gguf"
  "$COMFYUI_DIR/models/vae/wanvideo/Wan2_1_VAE_bf16.safetensors|https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan2_1_VAE_bf16.safetensors"
  "$COMFYUI_DIR/models/text_encoders/umt5-xxl-enc-bf16.safetensors|https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-bf16.safetensors"
  "$COMFYUI_DIR/models/clip_vision/clip_vision_h.safetensors|https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors"
  "$COMFYUI_DIR/models/diffusion_models/MelBandRoformer/MelBandRoformer_fp16.safetensors|https://huggingface.co/Kijai/MelBandRoFormer_comfy/resolve/main/MelBandRoformer_fp16.safetensors"
  "$COMFYUI_DIR/models/loras/WanVideo/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors|https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors"
)

log "ComfyUI dir: $COMFYUI_DIR"
log "ComfyUI port: $COMFYUI_PORT"

for dir in "${MODEL_DIRS[@]}"; do
  ensure_dir "$dir"
done

if is_true "$SKIP_MODEL_DOWNLOADS"; then
  log "SKIP_MODEL_DOWNLOADS=true, skipping downloads and verifying existing files"
else
  for item in "${REQUIRED_MODELS[@]}"; do
    path="${item%%|*}"
    url="${item#*|}"
    download_if_missing "$path" "$url"
  done
fi

for item in "${REQUIRED_MODELS[@]}"; do
  path="${item%%|*}"
  check_required_file "$path"
done

if is_true "$BOOTSTRAP_ONLY"; then
  log "BOOTSTRAP_ONLY=true, bootstrap completed without starting ComfyUI"
  exit 0
fi

start_comfyui
