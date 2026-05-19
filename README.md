# Telegram Video Avatar Bot

Production-ready skeleton for a Telegram bot that generates videos from a photo and
audio using RunPod, ComfyUI, InfiniteTalk, Cloudflare R2, and Telegram Crypto Pay.

## What is implemented now

- FastAPI backend with `/health`, `/api/v1/health`, `/api/v1/settings/public`.
- Safe operational status endpoint: `GET /api/v1/ops/status`.
- Startup configuration sanity checks for production launch-critical settings.
- Telegram user upsert endpoint: `POST /api/v1/telegram/users/upsert`.
- User statistics endpoint: `GET /api/v1/users/by-telegram/{telegram_id}/statistics`.
- User generation history endpoint: `GET /api/v1/users/by-telegram/{telegram_id}/generations`.
- Fixed payment package endpoints: `GET /api/v1/payments/packages`,
  `POST /api/v1/payments/invoices`, `POST /api/v1/payments/cryptobot/invoices`,
  `POST /api/v1/payments/cryptobot/webhook`, plus legacy disabled-by-config
  Cryptomus endpoints.
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
- Local debug operations anomalies endpoint: `GET /api/v1/debug/ops/anomalies`.
- Local debug segment status endpoint: `GET /api/v1/debug/generation/jobs/{job_id}/segments`.
- Local debug audio segment-plan endpoint: `POST /api/v1/debug/audio/segment-plan`.
- Local debug RunPod pod endpoints: `GET /api/v1/debug/runpod/pods`,
  `POST /api/v1/debug/runpod/create-pod`, `POST /api/v1/debug/runpod/cleanup-idle`,
  `POST /api/v1/debug/runpod/keeper-tick`, and
  `DELETE /api/v1/debug/runpod/pods/{runpod_pod_id}`.
- Private file download endpoint: `GET /api/v1/files/{file_id}/download?telegram_id=...`.
- Generation draft flow endpoints under `/api/v1/generation`.
- Async SQLAlchemy 2.x, PostgreSQL, Redis, and Alembic setup.
- Initial migration with users, balances, generation jobs, uploads, payments, and RunPod pods.
- `0002_balance_transactions` migration with balance ledger records.
- `0003_generation_flow` migration with generation segments and job lifecycle timestamps.
- `0004_runpod_manager` migration with RunPod pod lifecycle fields.
- User, balance, statistics, and generation-history repository/service layers.
- Aiogram 3.x bot with `/start`, statistics, generation history, balance top-up stub, help, support,
  `/debug_add_balance`, and an FSM generation flow.
- Celery worker with a sync SQLAlchemy/psycopg DB layer, mock generation mode, and
  ComfyUI generation mode with sequential 30-second segments for long audio.
- RunPod auto-manager for creating a pod from a template, waiting for ComfyUI readiness,
  reusing one idle pod, and terminating idle pods through debug cleanup.
- Storage abstraction with local and Cloudflare R2 providers.
- Local source-file storage under `storage/users/{user_id}` when `STORAGE_PROVIDER=local`.
- Cloudflare R2 upload and presigned download URLs when `STORAGE_PROVIDER=cloudflare_r2`.
- InfiniteTalk API workflow patcher for ComfyUI node ids `313`, `125`, `245`, `246`,
  `270`, `194`, and `317`.
- Payment provider abstraction with CryptoBot/Crypto Pay and legacy Cryptomus support,
  plus implemented pricing, audio, video stitching, storage, ComfyUI, and RunPod pod
  lifecycle boundaries.
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
- `CRYPTOBOT_PAY_API_TOKEN`
- `RUNPOD_*`

For production, set `APP_ENV=production`. `.env.example` keeps safe MVP launch defaults:
distributed generation disabled, `RUNPOD_MAX_ACTIVE_PODS=1`, `RUNPOD_MIN_WARM_PODS=0`,
and debug endpoints enabled only for local development. Use
`DEBUG_ENDPOINTS_ENABLED=false` or `DEBUG_ENDPOINTS_LOCAL_ONLY=true` before exposing a
production backend.

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

## Payment packages

Top-ups are fixed packages only:

- `$10`
- `$25`
- `$50`
- `$100`

Users pay through the configured payment provider in USDT. The default MVP provider is
Telegram CryptoBot / Crypto Pay. The bot balance is displayed and accounted in USD.
For the MVP, USD and USDT are treated as `1:1` for package accounting. Custom top-up
amounts are disabled, bonuses are not applied, and the bot does not show an estimated
number of generations per package.

Payment settings:

```env
PAYMENT_PROVIDER=cryptobot
PAYMENT_PACKAGES_ENABLED=true
PAYMENT_CUSTOM_AMOUNT_ENABLED=false
PAYMENT_PACKAGES_USD=10,25,50,100
PAYMENT_DISPLAY_CURRENCY=USD
PAYMENT_PROVIDER_CURRENCY=USDT
PAYMENT_USD_USDT_RATE=1
PAYMENT_SHOW_ESTIMATED_GENERATIONS=false
CRYPTOBOT_PAY_ENABLED=true
CRYPTOBOT_PAY_API_TOKEN=...
CRYPTOBOT_PAY_API_BASE_URL=https://pay.crypt.bot/api
CRYPTOBOT_PAY_ASSET=USDT
CRYPTOBOT_PAY_WEBHOOK_URL=https://YOUR_DOMAIN/api/v1/payments/cryptobot/webhook
CRYPTOMUS_ENABLED=false
PRICING_MIN_JOB_PRICE_USD=0.30
```

Payment provider modes:

- `PAYMENT_PROVIDER=cryptobot`: create CryptoBot/Crypto Pay invoices through
  `POST /api/v1/payments/invoices`.
- `PAYMENT_PROVIDER=cryptomus`: legacy Cryptomus integration, enabled only when
  `CRYPTOMUS_ENABLED=true`.
- `PAYMENT_PROVIDER=manual`: automatic invoice creation is disabled; users are told to
  contact support, and admins can use manual top-up actions.

CryptoBot setup:

1. Open `@CryptoBot`.
2. Open Crypto Pay / Apps and create an app for the bot.
3. Copy the Crypto Pay API token into `CRYPTOBOT_PAY_API_TOKEN`.
4. Configure the webhook URL:
   `https://YOUR_DOMAIN/api/v1/payments/cryptobot/webhook`.
5. Keep `CRYPTOBOT_PAY_ASSET=USDT`.

CryptoBot webhooks are verified with the `crypto-pay-api-signature` HMAC header and
then rechecked through `getInvoices` before any balance is credited. Duplicate paid
webhooks are idempotent and do not double-credit the balance.

If a CryptoBot payment was paid while the webhook was disabled, use the protected
admin recheck action. It loads the local `cryptobot` payment, rechecks the provider
invoice through CryptoBot, verifies invoice id, amount, and USDT asset, then credits
the user balance once if the invoice is paid:

```bash
curl -u admin:replace_with_a_strong_password \
  -X POST http://localhost:8000/api/v1/admin/payments/{payment_id}/recheck \
  -H 'Content-Type: application/json' \
  -d '{"reason":"Recheck CryptoBot invoice after webhook was disabled"}'
```

Generation funds are reserved before generation and captured only after successful
output upload. Failed generations refund the reserved balance. Business packages or
direct support top-ups can be handled manually later through admin/manual balance
adjustment.

## Business/shared balance accounts

Business accounts are shared USD balances for teams. Personal balance remains the
default for all users. If a Telegram user is an active member of exactly one active
business account, generation confirmation reserves funds from the business balance.
If the user has no active business account, the existing personal balance flow is used.

MVP rules:

- Business members spend from the shared business balance automatically.
- There is no automatic fallback to personal balance when business balance is
  insufficient, to avoid unexpected personal charges.
- Business top-up is manual through protected local debug/admin endpoints and can
  represent direct payment to support.
- Generation funds are held before generation and captured only after successful
  output upload. Failed or cancelled business jobs refund the held amount back to the
  business balance.
- The future admin panel will expose this UI; Stage 10.3 only adds local protected
  endpoints.

Debug/admin commands:

```bash
curl -X POST http://localhost:8000/api/v1/debug/business-accounts \
  -H 'Content-Type: application/json' \
  -d '{"name":"Company ABC"}'

curl http://localhost:8000/api/v1/debug/business-accounts

curl -X POST http://localhost:8000/api/v1/debug/business-accounts/{id}/members \
  -H 'Content-Type: application/json' \
  -d '{"telegram_id":123456789,"role":"owner"}'

curl -X POST http://localhost:8000/api/v1/debug/business-accounts/{id}/top-up \
  -H 'Content-Type: application/json' \
  -d '{"amount_usd":"100.00","reason":"Direct business payment"}'

curl http://localhost:8000/api/v1/debug/business-accounts/{id}/usage
curl http://localhost:8000/api/v1/debug/business-accounts/{id}/transactions

curl -X DELETE http://localhost:8000/api/v1/debug/business-accounts/{id}/members/{user_id}
```

Telegram statistics show company balance when a user has an active business account.
The top-up menu remains the personal fixed-package flow through the configured
payment provider; users with business balance see a note that company top-ups are
handled through support.

## Admin panel MVP

Stage 11.1 adds a read-only operator dashboard and read-only admin API. It is disabled
by default and uses HTTP Basic Auth for the MVP. Debug endpoints remain separate and
unchanged.

Local enablement:

```env
ADMIN_PANEL_ENABLED=true
ADMIN_BASIC_AUTH_ENABLED=true
ADMIN_BASIC_AUTH_USERNAME=admin
ADMIN_BASIC_AUTH_PASSWORD=replace_with_a_strong_password
ADMIN_SESSION_COOKIE_NAME=admin_session
ADMIN_SESSION_SECRET=
ADMIN_SESSION_TTL_SECONDS=86400
ADMIN_ACTIONS_ENABLED=false
ADMIN_MAX_MANUAL_TOPUP_USD=500
ADMIN_REQUIRE_ACTION_REASON=true
```

Open:

```text
http://localhost:8000/admin
```

Read-only API endpoints:

```bash
curl -u admin:replace_with_a_strong_password http://localhost:8000/api/v1/admin/overview
curl -u admin:replace_with_a_strong_password http://localhost:8000/api/v1/admin/users
curl -u admin:replace_with_a_strong_password http://localhost:8000/api/v1/admin/jobs
curl -u admin:replace_with_a_strong_password http://localhost:8000/api/v1/admin/payments
curl -u admin:replace_with_a_strong_password http://localhost:8000/api/v1/admin/runpod/pods
curl -u admin:replace_with_a_strong_password \
  http://localhost:8000/api/v1/admin/business-accounts
curl -u admin:replace_with_a_strong_password \
  "http://localhost:8000/api/v1/admin/reports/finance/summary"
curl -u admin:replace_with_a_strong_password \
  "http://localhost:8000/api/v1/admin/reports/finance/daily"
curl -u admin:replace_with_a_strong_password \
  "http://localhost:8000/api/v1/admin/reports/users/spending"
curl -u admin:replace_with_a_strong_password \
  "http://localhost:8000/api/v1/admin/reports/business/spending"
curl -u admin:replace_with_a_strong_password http://localhost:8000/api/v1/admin/audit-logs
```

Pages:

- `/admin` overview
- `/admin/users`
- `/admin/jobs`
- `/admin/payments`
- `/admin/runpod`
- `/admin/business`
- `/admin/reports`
- `/admin/reports/users`
- `/admin/reports/business`
- `/admin/audit`

Stage 11.1 does not expose destructive buttons or write actions. Manual top-up,
fail-refund, RunPod termination, and other operator actions remain available only
through existing protected local debug endpoints until Stage 11.2.

Stage 11.2 adds protected operator actions behind `ADMIN_ACTIONS_ENABLED=true`:

- manual personal balance top-up
- manual business balance top-up
- add/deactivate business account members
- fail/refund a generation job through the existing safe refund path
- recheck pending CryptoBot payments after webhook downtime
- retry waiting GPU/pod jobs
- terminate non-busy managed RunPod pods
- block/unblock users

Admin action examples:

```bash
curl -u admin:replace_with_a_strong_password \
  -X POST http://localhost:8000/api/v1/admin/users/{user_id}/balance/top-up \
  -H 'Content-Type: application/json' \
  -d '{"amount_usd":"10.00","reason":"Direct payment"}'

# {user_id} should be the internal user UUID from /admin/users.
# The endpoint also accepts a Telegram ID as a fallback for operator recovery.

curl -u admin:replace_with_a_strong_password \
  -X POST http://localhost:8000/api/v1/admin/business-accounts/{id}/balance/top-up \
  -H 'Content-Type: application/json' \
  -d '{"amount_usd":"100.00","reason":"Direct business payment"}'

curl -u admin:replace_with_a_strong_password \
  -X POST http://localhost:8000/api/v1/admin/business-accounts/{id}/members \
  -H 'Content-Type: application/json' \
  -d '{"telegram_id":123456789,"role":"member","reason":"Added by admin"}'

curl -u admin:replace_with_a_strong_password \
  -X POST http://localhost:8000/api/v1/admin/business-accounts/{id}/members/{user_id}/deactivate \
  -H 'Content-Type: application/json' \
  -d '{"reason":"Removed by admin"}'

curl -u admin:replace_with_a_strong_password \
  -X POST http://localhost:8000/api/v1/admin/jobs/{job_id}/fail-refund \
  -H 'Content-Type: application/json' \
  -d '{"reason":"Manual refund after support review"}'

curl -u admin:replace_with_a_strong_password \
  -X POST http://localhost:8000/api/v1/admin/payments/{payment_id}/recheck \
  -H 'Content-Type: application/json' \
  -d '{"reason":"Recheck CryptoBot invoice after webhook downtime"}'

curl -u admin:replace_with_a_strong_password \
  -X POST http://localhost:8000/api/v1/admin/jobs/retry-waiting \
  -H 'Content-Type: application/json' \
  -d '{"reason":"Manual retry from admin panel"}'

curl -u admin:replace_with_a_strong_password \
  -X POST http://localhost:8000/api/v1/admin/runpod/pods/{runpod_pod_id}/terminate \
  -H 'Content-Type: application/json' \
  -d '{"reason":"Terminate idle/stuck pod"}'
```

Every admin action requires Basic Auth, `ADMIN_PANEL_ENABLED=true`,
`ADMIN_ACTIONS_ENABLED=true`, and a reason when `ADMIN_REQUIRE_ACTION_REASON=true`.
Actions write `admin_audit_logs`. Balance changes always go through balance ledger
transactions. Telegram notifications are attempted for top-ups, business membership
changes, and fail-refund, but notification failure does not roll back the action.

Admin finance reports:

- `/api/v1/admin/reports/finance/summary` returns totals for the selected period.
- `/api/v1/admin/reports/finance/daily` groups top-ups, revenue, refunds, RunPod
  estimated cost, gross margin, completed jobs, failed jobs, and new users by day.
- `/api/v1/admin/reports/users/spending` groups personal and business generation
  spend by day and user. Business spend is attributed to the Telegram user who ran
  the generation, but business top-ups are reported on the business report to avoid
  double-counting.
- `/api/v1/admin/reports/business/spending` groups business top-ups and business
  generation spend by day, account, and generating user.

Report filters:

- `date_from=YYYY-MM-DD` and `date_to=YYYY-MM-DD`; defaults to current month to date.
- `billing_account_type=personal|business|all`.
- `user_id`, `telegram_id`, and `business_account_id` where supported.

Definitions:

- `payment_topups_usd`: successful automatic package payments from the active provider.
- `manual_personal_topups_usd`: admin/manual personal balance adjustments.
- `manual_business_topups_usd`: manual business account top-ups.
- `captured_revenue_usd`: completed generation job revenue with a capture ledger
  transaction.
- `refunded_usd`: refund/release ledger transactions for generation jobs.
- `estimated_runpod_cost_usd`: sum of `generation_jobs.cost_usd`; old jobs with null
  cost count as zero in report totals.
- `gross_margin_usd`: `captured_revenue_usd - estimated_runpod_cost_usd`.
- `gross_margin_percent`: gross margin divided by captured revenue; null when revenue
  is zero.

CSV exports:

```bash
curl -u admin:replace_with_a_strong_password \
  "http://localhost:8000/api/v1/admin/reports/finance/daily.csv?date_from=2026-05-01&date_to=2026-05-31" \
  -o finance_daily_may.csv

curl -u admin:replace_with_a_strong_password \
  "http://localhost:8000/api/v1/admin/reports/users/spending.csv?date_from=2026-05-01&date_to=2026-05-31" \
  -o user_spending_may.csv

curl -u admin:replace_with_a_strong_password \
  "http://localhost:8000/api/v1/admin/reports/business/spending.csv?date_from=2026-05-01&date_to=2026-05-31" \
  -o business_spending_may.csv
```

`cost_usd` is an estimate from configured RunPod hourly prices, not official RunPod
billing. Finance reports are read-only and do not require `ADMIN_ACTIONS_ENABLED`.

Production safety:

- Do not enable the admin panel without HTTPS.
- Use a strong Basic Auth password, at least 12 characters.
- Prefer an IP allowlist or private reverse-proxy path in addition to Basic Auth.
- Keep `DEBUG_ENDPOINTS_ENABLED=false` in production.
- Keep `ADMIN_ACTIONS_ENABLED=false` unless an operator needs write controls.
- If `APP_ENV=production` and `ADMIN_PANEL_ENABLED=true`, startup sanity checks require
  configured admin credentials.

## ComfyUI configuration

`GENERATION_MODE=mock` keeps generation local to the worker stub and does not call ComfyUI.

`GENERATION_MODE=comfyui` sends jobs to ComfyUI. If `RUNPOD_API_KEY` and
`RUNPOD_TEMPLATE_ID` are configured, the worker uses the RunPod auto-manager to create
or reuse a pod and then talks to `https://{pod_id}-{RUNPOD_COMFYUI_PORT}.proxy.runpod.net`.
If RunPod is not configured, the worker falls back to `COMFYUI_BASE_URL`.

ComfyUI settings:

```env
GENERATION_MODE=mock
COMFYUI_BASE_URL=http://localhost:8188
COMFYUI_WORKFLOW_PATH=/app/workflows/infinite_talk_api.json
COMFYUI_TIMEOUT_SECONDS=7200
COMFYUI_POLL_INTERVAL_SECONDS=5
COMFYUI_TRANSIENT_RETRY_MAX_ATTEMPTS=5
COMFYUI_TRANSIENT_RETRY_BACKOFF_SECONDS=5
COMFYUI_TRANSIENT_RETRY_BACKOFF_MAX_SECONDS=30
COMFYUI_INPUT_SUBFOLDER=ultronlab
COMFYUI_OUTPUT_SUBFOLDER=InfiniteTalk
SEGMENT_IMAGE_STRATEGY=last_frame
AUDIO_SEGMENTATION_STRATEGY=fixed
AUDIO_SILENCE_THRESHOLD_DB=-35
AUDIO_SILENCE_MIN_DURATION_SECONDS=0.30
AUDIO_SILENCE_SEARCH_WINDOW_SECONDS=7
AUDIO_SEGMENT_MIN_SECONDS=8
```

- `COMFYUI_BASE_URL` is the base ComfyUI URL without a fragment/hash.
- `COMFYUI_WORKFLOW_PATH` is the in-container path to the API workflow JSON.
- `COMFYUI_TIMEOUT_SECONDS` is the maximum generation wait time.
- `COMFYUI_POLL_INTERVAL_SECONDS` is the polling interval for status checks.
- `COMFYUI_TRANSIENT_RETRY_MAX_ATTEMPTS` controls per-request retry attempts for
  transient RunPod proxy or network errors.
- `COMFYUI_TRANSIENT_RETRY_BACKOFF_SECONDS` is the initial retry backoff.
- `COMFYUI_TRANSIENT_RETRY_BACKOFF_MAX_SECONDS` caps retry backoff.
- `COMFYUI_INPUT_SUBFOLDER` is the ComfyUI input subfolder.
- `COMFYUI_OUTPUT_SUBFOLDER` is the ComfyUI output subfolder.
- `SEGMENT_IMAGE_STRATEGY` controls the image input for long segmented jobs:
  `last_frame` or `source_image`.
- `AUDIO_SEGMENTATION_STRATEGY` controls segment boundaries: `fixed` or `silence`.
- `AUDIO_SILENCE_THRESHOLD_DB` is the ffmpeg `silencedetect` noise threshold.
- `AUDIO_SILENCE_MIN_DURATION_SECONDS` is the minimum pause length to consider.
- `AUDIO_SILENCE_SEARCH_WINDOW_SECONDS` is the look-back window before the max segment
  length where the worker searches for a pause.
- `AUDIO_SEGMENT_MIN_SECONDS` prevents intermediate segments from becoming too short.

ComfyUI mode supports long audio by splitting it into sequential segments of
`GENERATION_MAX_SEGMENT_SECONDS` seconds (30 seconds by default). The hard MVP limit
is still `GENERATION_MAX_AUDIO_SECONDS`; longer audio is rejected before queueing.

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

## RunPod auto-manager

Stage 7 runs without RunPod Network Volume. The pod template or image must already
contain ComfyUI, custom nodes, models, and startup logic needed to expose ComfyUI on
`RUNPOD_COMFYUI_PORT`.

RunPod settings:

```env
RUNPOD_API_KEY=change_me
RUNPOD_TEMPLATE_ID=change_me
RUNPOD_CLOUD_TYPE=COMMUNITY
RUNPOD_ALLOWED_GPU_TYPES=NVIDIA GeForce RTX 5090,NVIDIA GeForce RTX 4090
RUNPOD_MIN_VCPU=8
RUNPOD_MIN_RAM_GB=48
RUNPOD_FALLBACK_MIN_RAM_GB=48
RUNPOD_CONTAINER_DISK_GB=50
RUNPOD_VOLUME_DISK_GB=100
RUNPOD_CUDA_VERSION=12.8
RUNPOD_COMFYUI_PORT=8188
RUNPOD_POD_IDLE_SHUTDOWN_MINUTES=20
RUNPOD_POD_READY_TIMEOUT_SECONDS=900
RUNPOD_HEALTHCHECK_INTERVAL_SECONDS=10
RUNPOD_AUTO_TERMINATE=true
RUNPOD_KEEPER_ENABLED=true
RUNPOD_KEEPER_INTERVAL_SECONDS=120
RUNPOD_MAX_ACTIVE_PODS=1
RUNPOD_WARM_POD_ENABLED=true
RUNPOD_AUTOSCALING_ENABLED=true
RUNPOD_AUTOSCALING_STRATEGY=queue_time
RUNPOD_TARGET_QUEUE_WAIT_MINUTES=30
RUNPOD_MIN_WARM_PODS=0
RUNPOD_SCALE_UP_COOLDOWN_SECONDS=120
RUNPOD_SCALE_DOWN_COOLDOWN_SECONDS=300
RUNPOD_MAX_WARM_PODS_TO_CREATE_PER_TICK=1
RUNPOD_ESTIMATED_GENERATION_SPEED_FACTOR=20
RUNPOD_MAX_ESTIMATED_GPU_MINUTES_PER_TICK=240
RUNPOD_MAX_ESTIMATED_HOURLY_GPU_COST_USD=3.00
RUNPOD_ESTIMATED_POD_HOURLY_COST_USD=0.80
RUNPOD_DEFAULT_JOB_DURATION_SECONDS=60
RUNPOD_ESTIMATED_COLD_START_SECONDS=720
RUNPOD_SHORT_JOB_COLD_START_AVOIDANCE_ENABLED=true
RUNPOD_SHORT_JOB_MAX_DURATION_SECONDS=90
RUNPOD_CREATE_MAX_ATTEMPTS=3
RUNPOD_CREATE_RETRY_SLEEP_SECONDS=20
RUNPOD_COST_TRACKING_ENABLED=true
RUNPOD_DEFAULT_HOURLY_COST_USD=0.80
RUNPOD_GPU_HOURLY_COSTS_USD=NVIDIA GeForce RTX 5090:0.80,NVIDIA L40S:0.75,NVIDIA GeForce RTX 4090:0.55
RUNPOD_COST_INCLUDE_COLD_START=true
RUNPOD_COST_INCLUDE_IDLE_TIME=false
RUNPOD_COST_MIN_BILLING_SECONDS=60
RUNPOD_COST_ROUNDING_MODE=up_to_second
RUNPOD_WAITING_GPU_ENABLED=true
RUNPOD_WAITING_GPU_RETRY_SECONDS=120
RUNPOD_WAITING_GPU_MAX_WAIT_MINUTES=30
RUNPOD_QUEUE_WAIT_ENABLED=true
RUNPOD_QUEUE_RETRY_SECONDS=60
RUNPOD_QUEUE_MAX_WAIT_MINUTES=60

DISTRIBUTED_SEGMENT_GENERATION_ENABLED=false
DISTRIBUTED_MIN_AUDIO_DURATION_SECONDS=60
DISTRIBUTED_MAX_PARALLEL_SEGMENTS_PER_JOB=2
DISTRIBUTED_SEGMENT_TARGET_SECONDS=30
DISTRIBUTED_SEGMENT_MAX_RETRIES=2
DISTRIBUTED_REQUIRE_WARM_PODS=true
DISTRIBUTED_ALLOW_CREATE_EXTRA_PODS=false
DISTRIBUTED_STITCH_STRATEGY=concat
DISTRIBUTED_EXPERIMENTAL_LOGGING=true
DISTRIBUTED_SEGMENT_IMAGE_STRATEGY=source_image
```

How it works:

- Worker first looks for a DB pod record with status `starting`, `creating`, `ready`,
  or `idle`.
- If the pod passes ComfyUI `/system_stats`, it is marked `busy` and reused.
- If no ready pod exists, the worker creates a pod from `RUNPOD_TEMPLATE_ID`.
- GPU types are tried in `RUNPOD_ALLOWED_GPU_TYPES` order. If `NVIDIA GeForce RTX 5090`
  is unavailable, the worker tries `NVIDIA GeForce RTX 4090`.
- The RunPod create payload includes `cloudType`, `computeType=GPU`, `gpuTypeIds`,
  `gpuCount=1`, `templateId`, `allowedCudaVersions=[RUNPOD_CUDA_VERSION]`, disk
  sizes, minimum vCPU/RAM, public HTTP port `{RUNPOD_COMFYUI_PORT}/http`, and
  `supportPublicIp=true`.
- The payload intentionally does not include `networkVolumeId`.
- ComfyUI base URL is resolved as
  `https://{runpod_pod_id}-{RUNPOD_COMFYUI_PORT}.proxy.runpod.net`.
- If ComfyUI does not become healthy before `RUNPOD_POD_READY_TIMEOUT_SECONDS`, the pod
  is marked failed and terminated when `RUNPOD_AUTO_TERMINATE=true`.
- Stage 8.1 still uses one active pod and one local worker process. If GPU capacity is
  unavailable and waiting is enabled, the job moves to `waiting_for_gpu`; otherwise it
  fails with a clear message and frozen balance is refunded.
- On capacity errors, the manager retries pod creation up to
  `RUNPOD_CREATE_MAX_ATTEMPTS`, sleeping `RUNPOD_CREATE_RETRY_SLEEP_SECONDS` between
  full passes over `RUNPOD_ALLOWED_GPU_TYPES`.
- RAM fallback is two-phase. The manager first exhausts all GPU types and all
  `RUNPOD_CREATE_MAX_ATTEMPTS` with `RUNPOD_MIN_RAM_GB`. Only if those failures are
  capacity errors and `RUNPOD_FALLBACK_MIN_RAM_GB < RUNPOD_MIN_RAM_GB`, it repeats the
  full retry cycle with `RUNPOD_FALLBACK_MIN_RAM_GB`. A fallback such as `48` GB is an
  emergency capacity fallback for MVP deployments where the workflow is known to run
  with that RAM/GPU combination.
- Capacity exhaustion can put a job into `waiting_for_gpu` instead of failing. In this
  state the user's balance remains frozen, no capture/refund happens, and the worker
  schedules `retry_waiting_for_gpu_jobs` after `RUNPOD_WAITING_GPU_RETRY_SECONDS`.
  If the job waits longer than `RUNPOD_WAITING_GPU_MAX_WAIT_MINUTES`, the next capacity
  failure marks it failed and refunds the frozen balance.
- Stage 8.2 adds the RunPod keeper. `runpod_keeper_tick` healthchecks managed pods,
  marks healthy `starting`/`ready` pods as `idle`, terminates idle pods after
  `RUNPOD_POD_IDLE_SHUTDOWN_MINUTES`, and can keep warm pods ready when queued,
  `waiting_for_gpu`, or `waiting_for_pod` jobs exist. It never terminates `busy` pods or pods with an
  `active_job_id`, and it never changes balances.
- Warm pods reduce the first-job latency but cost money while idle. Use
  `RUNPOD_WARM_POD_ENABLED=false` to disable proactive pod creation, or lower
  `RUNPOD_POD_IDLE_SHUTDOWN_MINUTES` to reduce idle cost.
- Stage 8.3 allows `RUNPOD_MAX_ACTIVE_PODS > 1`. Each pod runs one job at a time.
  The worker reuses a healthy idle pod first; if all pods are busy and active pod count
  is still below `RUNPOD_MAX_ACTIVE_PODS`, it creates another pod. If the pool is full,
  the job moves to `waiting_for_pod`, keeps the balance frozen, and retries after
  `RUNPOD_QUEUE_RETRY_SECONDS`. If it waits longer than
  `RUNPOD_QUEUE_MAX_WAIT_MINUTES`, the job fails and refunds.
- Stage 8.4 adds queue-time autoscaling. One generation job still runs on one pod; the
  worker does not distribute one job's segments across multiple pods. The autoscaler
  estimates workload as:
  `pending_gpu_minutes = sum(job_duration_minutes * RUNPOD_ESTIMATED_GENERATION_SPEED_FACTOR)`.
  Then `desired_pods = ceil(pending_gpu_minutes / RUNPOD_TARGET_QUEUE_WAIT_MINUTES)`.
  Desired pods are capped by `RUNPOD_MAX_ACTIVE_PODS`, by the estimated hourly cost cap,
  and are never lower than the number of busy pods. Defaults are conservative:
  `RUNPOD_MAX_ACTIVE_PODS=1`, `RUNPOD_MIN_WARM_PODS=0`, and
  `RUNPOD_MAX_ESTIMATED_HOURLY_GPU_COST_USD=3.00`.
- Stage 8.5 adds experimental distributed segment generation behind
  `DISTRIBUTED_SEGMENT_GENERATION_ENABLED=false` by default. When enabled, long jobs
  can run independent audio segments on multiple already-warm RunPod pods, then stitch
  the segment mp4 files in segment order and remux the final result with the original
  full audio. The stable single-pod path remains the fallback. MVP distributed mode uses
  `DISTRIBUTED_SEGMENT_IMAGE_STRATEGY=source_image`, so each segment starts from the
  original uploaded image rather than the previous segment's last frame.
- Stage 8.4.1 makes scheduling cold-start-aware. `RUNPOD_ESTIMATED_COLD_START_SECONDS`
  is the assumed pod startup cost. Short jobs up to
  `RUNPOD_SHORT_JOB_MAX_DURATION_SECONDS` prefer waiting for existing busy or
  starting/creating capacity instead of creating a cold pod. Starting/creating pods
  count toward `RUNPOD_MAX_ACTIVE_PODS`, but they are not assignable until ComfyUI
  `/system_stats` is healthy and the pod is idle/ready.

Example fallback logs:

```text
RunPod create phase started phase=primary min_ram_gb=80
RunPod create phase exhausted phase=primary min_ram_gb=80
RunPod create phase started phase=fallback min_ram_gb=48
RunPod pod created gpu_type=NVIDIA GeForce RTX 5090 min_ram_gb=48 phase=fallback
```

Debug commands:

```bash
curl http://localhost:8000/api/v1/debug/runpod/pods
curl http://localhost:8000/api/v1/debug/runpod/autoscaling-plan
curl http://localhost:8000/api/v1/debug/ops/anomalies
curl -X POST http://localhost:8000/api/v1/debug/runpod/create-pod
curl -X POST http://localhost:8000/api/v1/debug/runpod/cleanup-idle
curl -X POST http://localhost:8000/api/v1/debug/runpod/keeper-tick
curl -X DELETE http://localhost:8000/api/v1/debug/runpod/pods/{runpod_pod_id}
curl -X POST http://localhost:8000/api/v1/debug/generation/retry-waiting-gpu
curl -X POST http://localhost:8000/api/v1/debug/generation/retry-waiting
```

There is no Celery beat scheduler in the MVP compose setup. In production, schedule the
Celery task `runpod_keeper_tick` every `RUNPOD_KEEPER_INTERVAL_SECONDS` seconds with
cron, Cloud Scheduler, or Celery beat. The HTTP endpoint is local/debug-only and is meant
for manual checks, not as the production scheduler surface.

## RunPod cost tracking

Stage 10.2 estimates infrastructure cost per generation job without calling the RunPod
billing API. User billing is unchanged: `generation_jobs.price_usd` remains captured
user revenue, and `generation_jobs.cost_usd` is only estimated infrastructure cost for
later finance/admin reporting.

Formula:

```text
cost_usd = hourly_gpu_price_usd * billable_seconds / 3600
```

`billable_seconds` is rounded up to the next second and is at least
`RUNPOD_COST_MIN_BILLING_SECONDS`. GPU hourly prices come from
`RUNPOD_GPU_HOURLY_COSTS_USD`; unknown GPU types use
`RUNPOD_DEFAULT_HOURLY_COST_USD`. With `RUNPOD_COST_INCLUDE_COLD_START=true`, the job
cost interval starts before endpoint acquisition, so a job that creates a cold pod
includes ComfyUI readiness time. `RUNPOD_COST_INCLUDE_IDLE_TIME=false` means warm/idle
pod cost is not allocated per job in this MVP.

Examples:

- `1200` seconds at `$0.80/h` -> `$0.2667`.
- `30` seconds at `$0.80/h` with `RUNPOD_COST_MIN_BILLING_SECONDS=60` -> `$0.0133`.
- A one-minute user video with user price around `$0.7200` and about `20` GPU minutes
  at `$0.80/h` has estimated cost `$0.2667` and gross margin around `$0.4533`.

Debug and ops visibility:

```bash
curl http://localhost:8000/api/v1/debug/generation/jobs?limit=20
curl http://localhost:8000/api/v1/debug/runpod/pods
curl http://localhost:8000/api/v1/ops/status
```

The debug jobs response includes `price_usd`, `cost_usd`, `gross_margin_usd`, and
`gross_margin_percent` when cost is available. The RunPod pod debug response includes
informational estimated runtime/cost for the pod record. These values are estimates;
actual RunPod billing API reconciliation can be added later.

## Stage 7.1 - RunPod bootstrap script

The Stage 7 auto-manager can create a pod, but the pod still has to prepare ComfyUI
models before the workflow can run. `scripts/runpod_bootstrap_comfyui.sh` is the
intermediate no-Network-Volume and no-custom-image bootstrap path.

The script:

- auto-detects `COMFYUI_DIR` when the env var is not set;
- validates ComfyUI by checking `main.py` plus structural markers such as `comfy/`,
  `custom_nodes/`, `nodes.py`, `execution.py`, and `server.py`;
- creates all required model directories;
- downloads missing model files;
- deletes and redownloads zero-byte files;
- verifies that every required model exists and has a non-zero size;
- starts ComfyUI on `0.0.0.0:8188` by default;
- is idempotent and skips files that already exist with a non-zero size.

The first pod boot can take a long time because the required models are tens of GB.
Without Network Volume, the models are lost when the pod is terminated. The production
solution should be a custom Docker image or reliable persistent storage.

Supported bootstrap env:

```env
COMFYUI_DIR=
COMFYUI_PORT=8188
COMFYUI_EXTRA_ARGS=--use-sage-attention
SKIP_MODEL_DOWNLOADS=false
BOOTSTRAP_ONLY=false
KILL_EXISTING_COMFYUI=true
```

RunPod template startup command, if the script already exists inside the template or
image:

```bash
bash /workspace/ComfyUI/scripts/runpod_bootstrap_comfyui.sh
```

RunPod template startup command, if the script should be fetched from GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/scripts/runpod_bootstrap_comfyui.sh -o /tmp/runpod_bootstrap_comfyui.sh && bash /tmp/runpod_bootstrap_comfyui.sh
```

Replace `OWNER/REPO` after the repository is published.

When `COMFYUI_DIR` is not set, the script first checks common paths such as
`/workspace/ComfyUI`, `/ComfyUI`, `/app/ComfyUI`, and `/root/ComfyUI`. If none are
valid, it searches only directories named `ComfyUI` or `comfyui` and still validates
their structure. It does not select arbitrary `main.py` files from Python packages.

Manual check inside a RunPod terminal:

```bash
cd /workspace/ComfyUI
bash scripts/runpod_bootstrap_comfyui.sh
```

If you only want to download and verify models without starting ComfyUI:

```bash
cd /workspace/ComfyUI
BOOTSTRAP_ONLY=true bash scripts/runpod_bootstrap_comfyui.sh
```

Check ComfyUI from your Mac:

```bash
curl https://PODID-8188.proxy.runpod.net/system_stats
```

Check required model sizes inside the pod:

```bash
du -h /workspace/ComfyUI/models/diffusion_models/WanVideo/wan2.1-i2v-14b-480p-Q8_0.gguf
du -h /workspace/ComfyUI/models/diffusion_models/WanVideo/InfiniteTalk/Wan2_1-InfiniteTalk_Single_Q8.gguf
du -h /workspace/ComfyUI/models/vae/wanvideo/Wan2_1_VAE_bf16.safetensors
du -h /workspace/ComfyUI/models/text_encoders/umt5-xxl-enc-bf16.safetensors
du -h /workspace/ComfyUI/models/clip_vision/clip_vision_h.safetensors
du -h /workspace/ComfyUI/models/diffusion_models/MelBandRoformer/MelBandRoformer_fp16.safetensors
du -h /workspace/ComfyUI/models/loras/WanVideo/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors
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

Add local test balance without an automatic payment provider:

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
7. Check `Мои генерации` after the worker completes.

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
to `completed`. For audio longer than one segment, the worker generates each segment
sequentially and stitches the segment videos into one final mp4. In `Мои генерации`,
completed jobs show a `Скачать результат` button when a result URL is available.

Inspect segment status for a local job:

```bash
curl http://localhost:8000/api/v1/debug/generation/jobs/{job_id}/segments
```

## Long audio / segmented generation

Long ComfyUI jobs are processed as a linear segment pipeline:

1. The backend creates an initial fixed `generation_segments` estimate from the
   original audio duration using `GENERATION_MAX_SEGMENT_SECONDS`.
2. The worker downloads the original image and audio from storage.
3. The worker builds the final segment plan, either fixed or silence-based.
4. If `AUDIO_SEGMENTATION_STRATEGY=silence`, `ffmpeg silencedetect` searches for
   natural pauses before the 30-second boundary. If no pause is found, the worker
   falls back to the fixed boundary.
5. If silence boundaries differ from the backend estimate, the worker updates
   `generation_segments` and `generation_jobs.segments_count` before the first
   ComfyUI prompt. The confirmed price and frozen balance are not recalculated.
6. `ffmpeg` splits audio into local WAV files: `segment_001.wav`, `segment_002.wav`,
   and so on.
7. Segment 1 uses the original source image.
8. After each non-final segment, the worker extracts the last frame as PNG and uses it
   as the next segment's image input.
9. Each segment is sent to ComfyUI as a separate `/prompt`.
10. Segment mp4 files are concatenated with `ffmpeg` concat demuxer.
11. For multi-segment jobs, the final video audio track is remuxed from the original
   uploaded full audio to reduce volume or codec jumps at segment joins.
12. The final mp4 is trimmed to the original audio duration if ComfyUI, concat, or
   remux output runs long, then uploaded to the configured storage provider.

Audio segmentation strategies:

- `AUDIO_SEGMENTATION_STRATEGY=fixed`: cut every
  `GENERATION_MAX_SEGMENT_SECONDS` seconds.
- `AUDIO_SEGMENTATION_STRATEGY=silence`: try to cut near natural pauses before the
  max segment length. The worker searches within
  `AUDIO_SILENCE_SEARCH_WINDOW_SECONDS` seconds before the fixed boundary.
- If no usable pause is found, silence mode falls back to fixed.
- The price stays based on the original confirmed calculation. Actual segment count
  may differ slightly after worker-side silence detection.

Preview an audio segment plan locally without GPU:

```bash
curl -X POST http://localhost:8000/api/v1/debug/audio/segment-plan \
  -F "audio=@/path/to/audio.mp3"
```

Segment image strategies:

- `SEGMENT_IMAGE_STRATEGY=last_frame`: each next segment starts from the previous
  segment's last generated frame. This gives smoother visual continuity, but long
  videos can drift gradually.
- `SEGMENT_IMAGE_STRATEGY=source_image`: every segment starts from the original source
  image. This can improve identity stability and reduce quality drift, but segment
  boundaries may look like a visual reset.

Only the final mp4 is uploaded to R2 in Stage 6. Audio segments, intermediate segment
mp4 files, and last-frame PNG files stay in worker temp storage. Successful jobs clean
up temp files. Failed jobs keep temp files in `APP_ENV=local` for debugging.

Current limitations:

- No segment overlap or crossfade yet.
- Visual drift between segments is possible because each next segment starts from the
  previous segment's last generated frame when `SEGMENT_IMAGE_STRATEGY=last_frame`.
- A visual reset at segment boundaries is possible when `SEGMENT_IMAGE_STRATEGY=source_image`.
- Local Docker setup is tuned for one worker process at a time.

## Telegram notifications

After a worker job finishes, the worker sends a Telegram notification through the
Bot API. User-facing notifications use a short video display name derived from the
uploaded photo and audio filenames. Completed jobs get `✅ Видео готово` with the
display name, charged amount, and a download button when a result URL is available.
Failed jobs use non-technical copy and mention whether funds were returned.

Notification failures are logged as warnings and never change generation job status
or balance ledger results.

Test Telegram notifications locally:

```bash
curl -X POST http://localhost:8000/api/v1/debug/telegram/test-notification \
  -H "Content-Type: application/json" \
  -d '{"telegram_id": 123456789, "message": "test notification"}'
```

Recommended BotFather copy:

- Bot name: `SynzAI`
- About: `AI-видеоаватары из фото и голоса.`
- Description:

```text
SynzAI создаёт AI-видеоаватары из фото и аудио. Загрузите изображение, добавьте голос — и получите готовое видео с говорящим аватаром. Подходит для контента, бизнеса, обучения и презентаций.
```

- Welcome:

```text
Создавайте AI-видеоаватары из фото и голоса.

Загрузите изображение, добавьте аудио — SynzAI сгенерирует готовое видео с говорящим аватаром.
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

## Production Deployment

Production files:

- `.env.production.example` - complete production template without real secrets.
- `docker-compose.prod.yml` - production compose for backend, bot, worker, PostgreSQL,
  Redis, and optional Caddy.
- `deploy/Caddyfile.example` - HTTPS reverse proxy example.
- `scripts/deploy_prod.sh` - build/start/migrate/healthcheck helper.
- `scripts/backup_postgres.sh` and `scripts/restore_postgres.sh` - simple database
  backup and restore helpers.

Create production env:

```bash
cp .env.production.example .env.production
```

Fill every `change_me` value in `.env.production`. Do not commit `.env.production`.

Start production services:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

Validate compose syntax without starting production services:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production.example config
```

Run migrations:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production exec backend \
  alembic upgrade head
```

Logs:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f backend bot worker
```

Health:

```bash
curl https://YOUR_DOMAIN/api/v1/health
curl https://YOUR_DOMAIN/api/v1/ops/status
```

The production compose does not expose PostgreSQL or Redis ports. Backend is bound to
`127.0.0.1:${BACKEND_PORT:-8000}` by default; put a reverse proxy in front of it or
enable the optional Caddy profile.

Optional Caddy HTTPS:

```bash
DOMAIN=YOUR_DOMAIN ACME_EMAIL=admin@example.com \
docker compose -f docker-compose.prod.yml --env-file .env.production --profile proxy up -d --build
```

Point the domain DNS A/AAAA records to the server before starting Caddy. The MVP bot
uses Telegram polling, so no Telegram webhook migration is required for Stage 10.
Configure the CryptoBot/Crypto Pay webhook URL in the app settings as:

```text
https://YOUR_DOMAIN/api/v1/payments/cryptobot/webhook
```

If `PAYMENT_PROVIDER=cryptomus`, use the legacy Cryptomus callback route:
`https://YOUR_DOMAIN/api/v1/payments/cryptomus/webhook`.

Local-only debug access in production:

- Default production setting is `DEBUG_ENDPOINTS_ENABLED=false`.
- For emergency access, SSH to the server and call local endpoints from the server, or
  temporarily set `DEBUG_ENDPOINTS_ENABLED=true` with `DEBUG_ENDPOINTS_LOCAL_ONLY=true`.
- Disable debug endpoints again immediately after the emergency action.
- Never expose destructive debug endpoints through a public reverse proxy.

Backups:

```bash
scripts/backup_postgres.sh
scripts/restore_postgres.sh backups/postgres_YYYYMMDDTHHMMSSZ.dump --yes
```

`restore_postgres.sh` asks for confirmation unless `--yes` is passed.

First production smoke test:

1. Deploy and run migrations.
2. Check `GET /api/v1/health` and `GET /api/v1/ops/status`.
3. Confirm the Telegram bot responds.
4. Check user balance display.
5. Create a CryptoBot invoice with the smallest package/current payment flow.
6. Confirm the payment callback updates balance.
7. Run one short generation.
8. Confirm final video delivery and the R2 download button.
9. Confirm balance capture happened and frozen balance returned to zero.
10. Confirm the RunPod pod becomes idle and later terminates.
11. Check local anomalies from the server if debug endpoints are temporarily enabled.
12. Check backend, bot, and worker logs for tracebacks.

## Production Launch Checklist

Pre-launch environment:

- Create `.env.production` from `.env.production.example`.
- Set `APP_ENV=production`.
- Set `DEBUG_ENDPOINTS_ENABLED=false`.
- Keep `ADMIN_PANEL_ENABLED=false` until HTTPS and strong admin credentials are ready.
  If enabled, set `ADMIN_BASIC_AUTH_PASSWORD` to a strong secret.
- Set real `TELEGRAM_BOT_TOKEN`, `CRYPTOBOT_PAY_API_TOKEN`, `RUNPOD_API_KEY`,
  `RUNPOD_TEMPLATE_ID`, PostgreSQL, Redis, and `CLOUDFLARE_R2_*` values.
- Keep `DISTRIBUTED_SEGMENT_GENERATION_ENABLED=false` for MVP launch.
- Keep `RUNPOD_MAX_ACTIVE_PODS` low, normally `1`, until cost and pod locking are
  monitored in production.
- Keep `RUNPOD_MIN_WARM_PODS=0` unless warm capacity cost is intentional.
- Keep `RUNPOD_AUTO_TERMINATE=true`, `RUNPOD_KEEPER_ENABLED=true`,
  `RUNPOD_WAITING_GPU_ENABLED=true`, and `RUNPOD_QUEUE_WAIT_ENABLED=true`.
- Confirm the database and Redis volumes exist and are mounted by production compose.
- Configure recurring PostgreSQL backups.
- Confirm DNS points to the server and HTTPS works.
- Configure the CryptoBot webhook URL.
- Verify RunPod API key/template and Cloudflare R2 access.
- Run one test payment and one short test generation.
- Check active RunPod pods after the generation.
- Know the rollback command before launch.
- Keep `CELERY_WORKER_CONCURRENCY=1` for MVP unless multi-worker locking has been
  explicitly load-tested.
- Set `DEBUG_ENDPOINTS_ENABLED=false` in production, or keep
  `DEBUG_ENDPOINTS_LOCAL_ONLY=true` behind private network controls.

Production startup runs a config sanity check. Missing critical production secrets
fail backend startup when `APP_ENV=production`; local and staging environments log
warnings without printing secret values.

Launch commands:

```bash
docker compose up --build -d
docker compose exec backend alembic upgrade head
curl http://localhost:8000/api/v1/health
curl http://localhost:8000/api/v1/ops/status
docker compose logs -f backend bot worker
```

Local-only operational checks:

```bash
curl http://localhost:8000/api/v1/debug/runpod/pods
curl http://localhost:8000/api/v1/debug/generation/jobs?limit=20
curl http://localhost:8000/api/v1/debug/runpod/autoscaling-plan
curl http://localhost:8000/api/v1/debug/ops/anomalies
```

RunPod safety commands:

```bash
curl -X POST http://localhost:8000/api/v1/debug/runpod/keeper-tick
curl -X POST http://localhost:8000/api/v1/debug/generation/retry-waiting
curl -X POST http://localhost:8000/api/v1/debug/generation/jobs/{job_id}/fail-refund
curl -X DELETE http://localhost:8000/api/v1/debug/runpod/pods/{runpod_pod_id}
```

Rollback:

- Revert the last application commit with `git revert <commit>`.
- Rebuild services: `docker compose up --build -d`.
- If a migration must be rolled back, run the matching `alembic downgrade` only after
  checking whether production data depends on the new schema.
- Terminate live RunPod pods if the rollback stops the worker or changes the template
  contract.

Emergency controls:

- Stop new processing by stopping the worker: `docker compose stop worker`.
- Prevent new pods by setting `RUNPOD_MAX_ACTIVE_PODS=0` and restarting the worker.
- Terminate active RunPod pods through the local debug endpoint or RunPod dashboard.
- Use `fail-refund` for stuck queued/generating/waiting jobs after verifying they did
  not already capture balance.
- List jobs: `curl http://localhost:8000/api/v1/debug/generation/jobs?limit=50`.
- Fail/refund a stuck job:
  `curl -X POST http://localhost:8000/api/v1/debug/generation/jobs/{job_id}/fail-refund`.
- Roll back code with `git revert <commit>` and rerun production compose.
- Restore a backup with `scripts/restore_postgres.sh backups/postgres_...dump --yes`.

## Troubleshooting

If Celery logs show `Event loop is closed`, `got Future attached to a different loop`,
or asyncpg loop mismatch, check that the worker task imports `worker.app.database`
and not `shared.app.database`. Worker DB access must stay sync-only. For local
development, `CELERY_WORKER_CONCURRENCY=1` is the default in `.env.example`; this is
a safety setting, not the core fix.

The worker image runs as a non-root `appuser` to avoid Celery's root warning.

Debug endpoints are protected by `DEBUG_ENDPOINTS_ENABLED` and
`DEBUG_ENDPOINTS_LOCAL_ONLY`. Keep them disabled or local-only in production; destructive
endpoints such as pod termination, `fail-refund`, keeper tick, and waiting-job retries
must never be exposed publicly.

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

- RunPod pod stays in `starting`: check the pod template startup logs. The template
  must start ComfyUI and expose port `RUNPOD_COMFYUI_PORT`.
- RunPod readiness timeout: the worker marks the pod failed and terminates it when
  `RUNPOD_AUTO_TERMINATE=true`. Increase `RUNPOD_POD_READY_TIMEOUT_SECONDS` only if
  the template legitimately needs more boot time.
- GPU unavailable: Stage 7 tries GPU types in `RUNPOD_ALLOWED_GPU_TYPES` order and
  puts the job into `waiting_for_gpu` when `RUNPOD_WAITING_GPU_ENABLED=true`. If
  `RUNPOD_FALLBACK_MIN_RAM_GB` is lower than `RUNPOD_MIN_RAM_GB`, fallback RAM is tried
  only after the primary RAM phase is fully exhausted by capacity errors. If waiting is
  disabled, or the max wait is exceeded, the job fails with refund.
- Waiting GPU jobs can be inspected with `GET /api/v1/debug/generation/jobs?limit=20`.
  Manual local retry is available through
  `POST /api/v1/debug/generation/retry-waiting-gpu`; it changes eligible
  `waiting_for_gpu` jobs back to `queued` and enqueues `process_generation_job`.
- Read-only operational anomalies can be inspected with
  `GET /api/v1/debug/ops/anomalies`. It reports stale generating/waiting jobs,
  orphan busy pods, active pods without endpoint URLs, terminal jobs with stale retry
  fields, and busy pods linked to missing or terminal jobs.
- Pool-full jobs use status `waiting_for_pod`. They mean the configured pod pool is
  busy or at `RUNPOD_MAX_ACTIVE_PODS`, not that RunPod capacity is unavailable.
  Retry both waiting states with `POST /api/v1/debug/generation/retry-waiting`.
- Terminal jobs (`completed`, `failed`, `cancelled`) clear `next_retry_at`,
  `waiting_for_gpu_since`, and `waiting_for_pod_since` so stale queue state does not
  appear in debug job lists.
- Debug `POST /api/v1/debug/runpod/create-pod` also tries GPU types in
  `RUNPOD_ALLOWED_GPU_TYPES` order, applies the same create retry and RAM fallback
  policy, and returns `phase`, `min_ram_gb`, and `tried_gpu_types` for capacity
  failures.
- Debug RunPod state with `GET /api/v1/debug/runpod/pods`; terminate a stuck pod with
  `DELETE /api/v1/debug/runpod/pods/{runpod_pod_id}`.
- Run the keeper manually with `POST /api/v1/debug/runpod/keeper-tick`. It can create
  warm pods for queued, `waiting_for_gpu`, or `waiting_for_pod` work, and it terminates
  idle pods older than `RUNPOD_POD_IDLE_SHUTDOWN_MINUTES`.
- The keeper response includes `active_pods`, `busy_pods`, `idle_pods`,
  `pending_jobs`, `desired_active_pods`, `created_warm_pods`, and `autoscaling`.
  Inspect the read-only autoscaling decision with
  `GET /api/v1/debug/runpod/autoscaling-plan`.
- Autoscaling can be disabled with `RUNPOD_AUTOSCALING_ENABLED=false`; the keeper then
  falls back to Stage 8.3 behavior. Unknown strategies also fall back conservatively.
- Distributed segment generation is experimental and disabled by default. If it is
  enabled but the job is short, has one segment, lacks enough warm idle pods, or uses an
  unsupported strategy, the worker logs `Distributed generation skipped reason=...` and
  falls back to the stable single-pod path.
- Distributed mode does not change user pricing yet. Using multiple pods can increase
  infrastructure cost for the same user price.
- Debug per-segment assignment with
  `GET /api/v1/debug/generation/jobs/{job_id}/segments`; it includes attempts,
  `runpod_pod_id`, and `prompt_id` when distributed mode runs.
- In autoscaling/debug responses, `active_capacity_pods` includes creating/starting
  pods. `assignable_pods` means healthy idle/ready pods only.
- `No mp4 output found in ComfyUI history`: check that the workflow's
  `VHS_VideoCombine` node writes an `.mp4` and that node `317` is present.
- `LoadAudio did not receive file`: check `COMFYUI_INPUT_SUBFOLDER`, the node `125`
  input name, and the patch preview endpoint.
- `LoadAudio.validate_inputs() missing 1 required positional argument: 'audio'`:
  API workflow node `125` must contain `inputs.audio`; `audioUI` alone is not enough
  for ComfyUI `/prompt` validation.
- `ComfyUI prompt timed out`: increase `COMFYUI_TIMEOUT_SECONDS` or inspect
  `docker compose logs -f worker` and the ComfyUI UI.
- RunPod proxy `502 Bad Gateway` during `/history` polling: this is treated as
  transient. The worker logs
  `ComfyUI transient error while polling history prompt_id=... status=502 retry_in=...`
  and keeps polling until `COMFYUI_TIMEOUT_SECONDS` expires.
- `ComfyUI generated longer video than audio`: node `317` is patched with
  `trim_to_audio=true`, and the worker also runs a postprocess ffmpeg trim before
  saving the mp4 to storage. Inspect `video_duration_before` and
  `video_duration_after` in `docker compose logs -f worker`.
- Volume jump at segment boundary: multi-segment final output is remuxed with the
  original uploaded full audio. Inspect `Final audio remux started` and
  `Final audio remux completed` in worker logs.
- Visual reset between segments: this is expected with
  `SEGMENT_IMAGE_STRATEGY=source_image`. Switch back to `last_frame` for smoother
  continuity.
- Visual drift over long videos: this is more likely with
  `SEGMENT_IMAGE_STRATEGY=last_frame`. Test `source_image` to trade continuity for
  identity stability.
- No silences found: `AUDIO_SEGMENTATION_STRATEGY=silence` falls back to fixed
  boundaries. Confirm with `POST /api/v1/debug/audio/segment-plan`.
- Cuts happen too early: reduce `AUDIO_SILENCE_SEARCH_WINDOW_SECONDS` or increase
  `AUDIO_SEGMENT_MIN_SECONDS`.
- Cuts still happen mid-word: make detection more sensitive with
  `AUDIO_SILENCE_THRESHOLD_DB=-40`, or require longer pauses with
  `AUDIO_SILENCE_MIN_DURATION_SECONDS=0.4`.
- Too many false pause candidates: make detection less sensitive with
  `AUDIO_SILENCE_THRESHOLD_DB=-30`. Typical minimum duration values are `0.2` to
  `0.5` seconds.
- Segment failed: inspect `docker compose logs -f worker` and
  `GET /api/v1/debug/generation/jobs/{job_id}/segments`. The worker marks the job
  failed, marks unfinished segments failed, and refunds frozen balance.
- Last frame extraction failed: check that the previous segment mp4 is valid and
  that `ffmpeg` is available in the worker image. Failed local jobs keep temp files
  under `storage/worker/{job_id}`.
- `ffmpeg concat failed`: the worker first tries stream-copy concat and falls back to
  H.264/AAC re-encoding. If both fail, inspect segment codecs and temp files.
- Final video duration mismatch: the worker logs final `video_duration_before` and
  `video_duration_after`; long output is trimmed to the original audio duration,
  while short output is logged as a warning.
- Missing custom node/model errors must be fixed inside the manually running ComfyUI
  environment.
- `404` on `/view`: the filename, subfolder, or output type returned by history does
  not match the file on the ComfyUI side.
- RunPod proxy URL must be the base URL without `#...`.

## Next stages

- Waiting-for-GPU queue/retry instead of immediate failed+refund when capacity is unavailable.
- Segment overlap/crossfade and improved continuity.
- Production cleanup scheduling and storage lifecycle policies.
- Optional payment-provider refinements and live webhook hardening.
