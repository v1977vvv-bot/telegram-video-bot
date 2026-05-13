# Telegram Video Avatar Bot

Production-ready skeleton for a Telegram bot that will generate videos from a
photo and audio using RunPod, ComfyUI, InfiniteTalk, Cloudflare R2, and Cryptomus.

## What is implemented now

- FastAPI backend with `/health`, `/api/v1/health`, `/api/v1/settings/public`.
- Telegram user upsert endpoint: `POST /api/v1/telegram/users/upsert`.
- User statistics endpoint: `GET /api/v1/users/by-telegram/{telegram_id}/statistics`.
- User generation history endpoint: `GET /api/v1/users/by-telegram/{telegram_id}/generations`.
- Debug endpoint `POST /api/v1/debug/enqueue-ping` that enqueues a Celery task.
- Local debug balance endpoint: `POST /api/v1/debug/users/{telegram_id}/add-balance`.
- Local debug batch endpoint: `POST /api/v1/debug/users/{telegram_id}/mock-generation-jobs`.
- Local debug balance repair endpoint: `POST /api/v1/debug/users/{telegram_id}/repair-frozen-balances`.
- Local debug balance ledger endpoint: `GET /api/v1/debug/users/{telegram_id}/balance-ledger`.
- Local debug storage test endpoint: `POST /api/v1/debug/storage/test-upload`.
- Local debug storage cleanup endpoint: `POST /api/v1/debug/storage/cleanup`.
- Local debug ComfyUI health endpoint: `GET /api/v1/debug/comfyui/health`.
- Local debug workflow validation endpoint: `POST /api/v1/debug/comfyui/validate-workflow`.
- Local debug workflow patch preview endpoint: `POST /api/v1/debug/comfyui/patch-workflow-preview`.
- Local debug Telegram notification endpoint: `POST /api/v1/debug/telegram/test-notification`.
- Private file download endpoint: `GET /api/v1/files/{file_id}/download?telegram_id=...`.
- Generation draft flow endpoints under `/api/v1/generation`.
- Async SQLAlchemy 2.x, PostgreSQL, Redis, and Alembic setup.
- Initial migration with users, balances, generation jobs, uploads, payments, and RunPod pods.
- `0002_balance_transactions` migration with balance ledger records.
- `0003_generation_flow` migration with generation segments and job lifecycle timestamps.
- User, balance, statistics, and generation-history repository/service layers.
- Aiogram 3.x bot with `/start`, statistics, generation history, balance top-up stub, help, support,
  `/debug_add_balance`, and an FSM generation flow.
- Celery worker with a sync SQLAlchemy/psycopg DB layer, mock generation mode, and
  ComfyUI generation mode for one-segment jobs.
- Storage abstraction with local and Cloudflare R2 providers.
- Local source-file storage under `storage/users/{user_id}` when `STORAGE_PROVIDER=local`.
- Cloudflare R2 upload and presigned download URLs when `STORAGE_PROVIDER=cloudflare_r2`.
- InfiniteTalk API workflow patcher for ComfyUI node ids `313`, `125`, `245`, `246`,
  `270`, `194`, and `317`.
- Interface stubs for RunPod, Cryptomus, pricing, audio, and video stitching.
- Placeholder workflow at `workflows/infinite_talk_base.json` and API workflow at
  `workflows/infinite_talk_api.json`.

Spending statistics are calculated from `balance_transactions` with `type='capture'`.
This keeps spending tied to committed ledger operations instead of estimated job prices.
The amount is summed by absolute value, while the project stores `hold`, `capture`,
`refund`, and `release` ledger amounts as positive USD values.

## Local start

Copy environment variables:

```bash
cp .env.example .env
```

Fill real secrets where needed:

- `TELEGRAM_BOT_TOKEN`
- `SUPPORT_TELEGRAM_USERNAME`
- `CLOUDFLARE_R2_*`
- `CRYPTOMUS_*`
- `RUNPOD_*`

Select storage provider:

```env
STORAGE_PROVIDER=local
```

For Cloudflare R2:

```env
STORAGE_PROVIDER=cloudflare_r2
CLOUDFLARE_R2_ACCOUNT_ID=...
CLOUDFLARE_R2_ACCESS_KEY_ID=...
CLOUDFLARE_R2_SECRET_ACCESS_KEY=...
CLOUDFLARE_R2_BUCKET=...
CLOUDFLARE_R2_ENDPOINT_URL=
CLOUDFLARE_R2_PRESIGNED_URL_TTL_SECONDS=86400
CLOUDFLARE_R2_PUBLIC_BASE_URL=
```

If `CLOUDFLARE_R2_ENDPOINT_URL` is empty, the app uses
`https://{CLOUDFLARE_R2_ACCOUNT_ID}.r2.cloudflarestorage.com`. The bucket can stay
private. By default, downloads use presigned URLs; `CLOUDFLARE_R2_PUBLIC_BASE_URL`
is optional.

## ComfyUI configuration

`GENERATION_MODE=mock` keeps generation local to the worker stub and does not call ComfyUI.

`GENERATION_MODE=comfyui` sends one-segment jobs to an already running ComfyUI instance.
RunPod pod lifecycle is still manual at this stage.

ComfyUI settings:

```env
GENERATION_MODE=mock
COMFYUI_BASE_URL=http://localhost:8188
COMFYUI_WORKFLOW_PATH=/app/workflows/infinite_talk_api.json
COMFYUI_TIMEOUT_SECONDS=7200
COMFYUI_POLL_INTERVAL_SECONDS=5
COMFYUI_INPUT_SUBFOLDER=ultronlab
COMFYUI_OUTPUT_SUBFOLDER=InfiniteTalk
```

- `COMFYUI_BASE_URL` is the base ComfyUI URL without a fragment/hash.
- `COMFYUI_WORKFLOW_PATH` is the in-container path to the API workflow JSON.
- `COMFYUI_TIMEOUT_SECONDS` is the maximum generation wait time.
- `COMFYUI_POLL_INTERVAL_SECONDS` is the polling interval for status checks.
- `COMFYUI_INPUT_SUBFOLDER` is the ComfyUI input subfolder.
- `COMFYUI_OUTPUT_SUBFOLDER` is the ComfyUI output subfolder.

Stage 5 limitation: ComfyUI mode supports only one segment, so audio must be no
longer than `GENERATION_MAX_SEGMENT_SECONDS` (30 seconds by default). Longer queued
jobs fail with a clear error and frozen balance is released.

For RunPod proxy URLs, use the base address:

```text
https://od1w1p6nad6xkf-8188.proxy.runpod.net
```

Do not use a browser URL with a fragment/hash such as:

```text
https://od1w1p6nad6xkf-8188.proxy.runpod.net/#...
```

Check a manually running ComfyUI instance:

```bash
curl "$COMFYUI_BASE_URL/system_stats"
```

Check ComfyUI through the backend:

```bash
curl http://localhost:8000/api/v1/debug/comfyui/health
curl -X POST http://localhost:8000/api/v1/debug/comfyui/validate-workflow
curl -X POST http://localhost:8000/api/v1/debug/comfyui/patch-workflow-preview \
  -H "Content-Type: application/json" \
  -d '{"image_filename": "test.png", "audio_filename": "test.mp3", "width": 480, "height": 480, "fps": 25, "frame_count": 250}'
```

Return to mock mode:

```env
GENERATION_MODE=mock
```

Then restart the stack:

```bash
docker compose down
docker compose up --build -d
```

Start the stack:

```bash
docker compose up --build
```

With the default `TELEGRAM_BOT_TOKEN=change_me`, the bot container stays alive but does
not start Telegram polling. Put a real token into `.env` to enable polling.

Apply migrations:

```bash
docker compose exec backend alembic upgrade head
```

Check `/start`:

- Put a real `TELEGRAM_BOT_TOKEN` into `.env`.
- Start the stack.
- Send `/start` to the bot.
- The bot will call `POST /api/v1/telegram/users/upsert` through `BACKEND_INTERNAL_URL`.

Check backend health:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/v1/health
```

Check public settings:

```bash
curl http://localhost:8000/api/v1/settings/public
```

Create or update a Telegram user:

```bash
curl -X POST http://localhost:8000/api/v1/telegram/users/upsert \
  -H "Content-Type: application/json" \
  -d '{
    "telegram_id": 123456789,
    "username": "test_user",
    "first_name": "Test",
    "last_name": "User",
    "language_code": "ru"
  }'
```

Check statistics:

```bash
curl http://localhost:8000/api/v1/users/by-telegram/123456789/statistics
```

Add local test balance without Cryptomus:

```bash
curl -X POST http://localhost:8000/api/v1/debug/users/123456789/add-balance \
  -H "Content-Type: application/json" \
  -d '{"amount_usd": "10.0000", "reason": "local test"}'
```

Check generation history:

```bash
curl "http://localhost:8000/api/v1/users/by-telegram/123456789/generations?limit=10"
```

Enqueue a test Celery task:

```bash
curl -X POST http://localhost:8000/api/v1/debug/enqueue-ping
docker compose logs -f worker
```

Create several local mock generation jobs without Telegram files:

```bash
curl -X POST http://localhost:8000/api/v1/debug/users/123456789/mock-generation-jobs \
  -H "Content-Type: application/json" \
  -d '{"count": 3, "duration_seconds": "1.000"}'
docker compose logs -f worker
```

Inspect the local balance ledger:

```bash
curl http://localhost:8000/api/v1/debug/users/123456789/balance-ledger
```

Repair stale local frozen balances left by old failed/cancelled/completed mock jobs:

```bash
curl -X POST http://localhost:8000/api/v1/debug/users/123456789/repair-frozen-balances
```

Test the configured storage provider:

```bash
curl -X POST http://localhost:8000/api/v1/debug/storage/test-upload \
  -H "Content-Type: application/json" \
  -d '{"content": "hello r2"}'
```

For R2, the response contains `storage_key`, `file_id`, `exists`, and a presigned
`download_url`. The object should also be visible in the Cloudflare R2 dashboard
under the configured bucket. Do not paste presigned URLs into logs; they contain
temporary access signatures.

Delete a debug uploaded file:

```bash
curl -X DELETE http://localhost:8000/api/v1/debug/storage/files/{file_id}
```

Run storage cleanup manually:

```bash
curl -X POST http://localhost:8000/api/v1/debug/storage/cleanup
```

## Generation flow

In Telegram:

1. Send `/start`.
2. Press `Сгенерировать видео`.
3. Upload a JPG, PNG, or WEBP image.
4. Upload an MP3, WAV, M4A, or OGG audio file.
5. Choose one of the supported formats.
6. Confirm the price.
7. Check `Мои генерации` after the mock worker completes.

When `STORAGE_PROVIDER=local`, the backend stores uploaded source files locally:

```text
storage/
  users/
    {user_id}/
      images/
      audio/
      videos/
      temp/
```

In `GENERATION_MODE=mock`, the worker marks each segment as generating, waits one
second per segment, writes a small mock result file through the configured storage
provider, captures frozen balance, and sets `mock_result_message`.

In `GENERATION_MODE=comfyui`, the worker downloads source image/audio from storage,
uploads them to ComfyUI, patches `workflows/infinite_talk_api.json`, queues `/prompt`,
polls `/history/{prompt_id}`, downloads the produced mp4 through `/view`, stores it
through the configured storage provider, captures frozen balance, and sets the job
to `completed`. In `Мои генерации`, completed jobs show a `Скачать результат` button
when a result URL is available.

## Telegram notifications

After a worker job finishes, the worker sends a Telegram notification through the
Bot API. Completed jobs get `✅ Видео готово` with duration, price, and a download
button when a result URL is available. Failed jobs get `❌ Генерация не удалась`
with a short escaped error and a balance-return note.

Notification failures are logged as warnings and never change generation job status
or balance ledger results.

Test Telegram notifications locally:

```bash
curl -X POST http://localhost:8000/api/v1/debug/telegram/test-notification \
  -H "Content-Type: application/json" \
  -d '{"telegram_id": 123456789, "message": "test notification"}'
```

The backend keeps async SQLAlchemy with asyncpg. The Celery worker intentionally uses
a separate sync SQLAlchemy engine with psycopg, initialized inside each Celery child
process, so prefork workers do not share asyncpg connections across event loops.

Backend curl flow:

```bash
curl -X POST http://localhost:8000/api/v1/generation/drafts \
  -F telegram_id=123456789 \
  -F 'image=@/path/to/image.png;type=image/png' \
  -F 'audio=@/path/to/audio.wav;type=audio/wav'

curl -X PATCH http://localhost:8000/api/v1/generation/drafts/{job_id}/format \
  -H "Content-Type: application/json" \
  -d '{"telegram_id": 123456789, "width": 480, "height": 480}'

curl -X POST http://localhost:8000/api/v1/generation/drafts/{job_id}/confirm \
  -H "Content-Type: application/json" \
  -d '{"telegram_id": 123456789}'

curl "http://localhost:8000/api/v1/generation/jobs/{job_id}?telegram_id=123456789"
```

## Workflow

`workflows/infinite_talk_base.json` is only a placeholder. ComfyUI mode uses
`workflows/infinite_talk_api.json`, which must be an API-format InfiniteTalk workflow.
The patcher updates:

- node `313` `LoadImage`: `inputs.image = "{COMFYUI_INPUT_SUBFOLDER}/{filename}"`.
- node `125` `LoadAudio`: always sets
  `inputs.audio = "{COMFYUI_INPUT_SUBFOLDER}/{filename}"`; if `audioUI` exists, also
  sets `/api/view?filename=...&type=input&subfolder=...`.
- node `245` width and node `246` height.
- node `270` max frames.
- node `194` fps.
- node `317` video combine frame rate, filename prefix, and `trim_to_audio=true`.

## Useful commands

```bash
docker compose exec backend alembic revision --autogenerate -m "message"
docker compose exec backend alembic upgrade head
docker compose exec worker celery -A worker.app.main.celery_app inspect registered
```

## Troubleshooting

If Celery logs show `Event loop is closed`, `got Future attached to a different loop`,
or asyncpg loop mismatch, check that the worker task imports `worker.app.database`
and not `shared.app.database`. Worker DB access must stay sync-only. For local
development, `CELERY_WORKER_CONCURRENCY=1` is the default in `.env.example`; this is
a safety setting, not the core fix.

The worker image runs as a non-root `appuser` to avoid Celery's root warning.

If a local database already contains jobs from an older worker build, a failed or
cancelled job can leave `balance_accounts.frozen_usd` above zero. Use:

```bash
curl http://localhost:8000/api/v1/debug/users/123456789/balance-ledger
curl -X POST http://localhost:8000/api/v1/debug/users/123456789/repair-frozen-balances
```

The repair endpoint is available only when `APP_ENV=local`. It releases stale
holds for failed/cancelled jobs and captures stale holds for completed jobs so
spending statistics are rebuilt from real `capture` ledger records.

If Telegram logs show `TelegramBadRequest: can't parse entities: Unsupported start tag`,
dynamic text was sent under HTML parse mode without escaping. Escape backend/env/error
strings before inserting them into bot messages, especially job `error_message`.
The bot uses `safe_html(...)` for generation history and backend error strings.

ComfyUI troubleshooting:

- `No mp4 output found in ComfyUI history`: check that the workflow's
  `VHS_VideoCombine` node writes an `.mp4` and that node `317` is present.
- `LoadAudio did not receive file`: check `COMFYUI_INPUT_SUBFOLDER`, the node `125`
  input name, and the patch preview endpoint.
- `LoadAudio.validate_inputs() missing 1 required positional argument: 'audio'`:
  API workflow node `125` must contain `inputs.audio`; `audioUI` alone is not enough
  for ComfyUI `/prompt` validation.
- `ComfyUI prompt timed out`: increase `COMFYUI_TIMEOUT_SECONDS` or inspect
  `docker compose logs -f worker` and the ComfyUI UI.
- `ComfyUI generated longer video than audio`: node `317` is patched with
  `trim_to_audio=true`, and the worker also runs a postprocess ffmpeg trim before
  saving the mp4 to storage. Inspect `video_duration_before` and
  `video_duration_after` in `docker compose logs -f worker`.
- Missing custom node/model errors must be fixed inside the manually running ComfyUI
  environment.
- `404` on `/view`: the filename, subfolder, or output type returned by history does
  not match the file on the ComfyUI side.
- RunPod proxy URL must be the base URL without `#...`.

## Next stages

- RunPod pod lifecycle management.
- Multi-segment ComfyUI generation and video stitching.
- Production cleanup scheduling and storage lifecycle policies.
- Cryptomus invoice creation and webhook processing.
- User completion notifications from worker to Telegram.
