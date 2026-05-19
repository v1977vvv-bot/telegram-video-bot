#!/usr/bin/env bash
set -euo pipefail

cd /workspace/ComfyUI

echo "Starting ComfyUI on 0.0.0.0:8188"
python main.py --listen 0.0.0.0 --port 8188
