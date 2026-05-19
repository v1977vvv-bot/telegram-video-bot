#!/usr/bin/env bash
set -euo pipefail

cd /workspace/ComfyUI

if [ "${DOWNLOAD_MODELS:-1}" = "1" ]; then
  /download_models.sh
else
  echo "[models] DOWNLOAD_MODELS=0, skipping model download"
fi

echo "Starting ComfyUI on 0.0.0.0:8188"
python main.py --listen 0.0.0.0 --port 8188
