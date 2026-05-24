# RTX PRO 6000 Blackwell InfiniteTalk Benchmark

## Hardware

- GPU: NVIDIA RTX PRO 6000 Blackwell Workstation Edition
- VRAM: 96 GB
- CUDA container: 13.0.2
- PyTorch: 2.12.0+cu130
- Torch CUDA: 13.0
- Capability: sm_120

## Test

- Input: same photo + approximately 60 sec audio
- Output: InfiniteTalk talking avatar video
- Resolution: 480p
- Backend: ComfyUI + WanVideoWrapper + InfiniteTalk

## Results

| Profile | Model format | CUDA/Torch | Time for ~60 sec video | Ratio |
|---|---:|---:|---:|---:|
| GGUF Q8 | GGUF Q8 | cu128 | 545.39 sec | ~9.09x |
| GGUF Q8 | GGUF Q8 | cu130 | 531.16 sec | ~8.85x |
| FP8 480p | safetensors FP8 | cu130 | 534.85 sec | ~8.91x |

## Conclusion

CUDA 13 / PyTorch cu130 works correctly on RTX PRO 6000 Blackwell, but the speed improvement is small.

FP8 480p works correctly and avoids GGUF downloads, but does not materially improve speed versus GGUF Q8.

Production planning should use a conservative baseline of:

- 1 video minute = ~8.8-9.1 GPU minutes on RTX PRO 6000 Blackwell
- 1 RTX PRO pod = ~6.6 video minutes/hour

## Production decision

Use FP8 480p as the preferred production profile because:

- Native safetensors format
- Cleaner path to future 720p quality mode
- Slightly lower VRAM footprint
- GGUF Q8 remains available as fallback

Planned profiles:

- `fp8_480p`: default production
- `fp8_720p`: future quality mode
- `gguf_q8_480p`: fallback
