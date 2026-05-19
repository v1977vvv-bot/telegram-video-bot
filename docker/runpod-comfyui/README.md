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

ComfyUI URL:

```text

https://<pod_id>-8188.proxy.runpod.net

```
