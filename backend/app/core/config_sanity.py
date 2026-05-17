from __future__ import annotations

from dataclasses import dataclass, field

from shared.app.config import Settings
from shared.app.enums import StorageProvider
from shared.app.logging import get_logger

logger = get_logger(__name__)
ALLOWED_APP_ENVS = {"local", "staging", "production"}
PLACEHOLDER_VALUES = {"", "change_me", "your_support_username"}


@dataclass(slots=True)
class ConfigSanityResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_startup_config(settings: Settings) -> ConfigSanityResult:
    """Validate launch-critical config without exposing secret values."""

    result = build_config_sanity_result(settings)
    for warning in result.warnings:
        logger.warning("Config sanity warning: %s", warning)
    for error in result.errors:
        logger.error("Config sanity error: %s", error)

    app_env = settings.app_env.strip().lower()
    if result.errors and (app_env == "production" or app_env not in ALLOWED_APP_ENVS):
        raise RuntimeError(
            "Production configuration sanity check failed. "
            "See previous log lines for missing or unsafe settings."
        )

    logger.info(
        "Config sanity check completed env=%s errors=%s warnings=%s",
        settings.app_env,
        len(result.errors),
        len(result.warnings),
    )
    return result


def build_config_sanity_result(settings: Settings) -> ConfigSanityResult:
    result = ConfigSanityResult()
    app_env = settings.app_env.strip().lower()

    if app_env not in ALLOWED_APP_ENVS:
        result.errors.append(
            "APP_ENV must be one of local, staging, production; current value is invalid"
        )
        return result

    if app_env == "production":
        _require_production_secret(
            result,
            "TELEGRAM_BOT_TOKEN",
            settings.telegram_bot_token,
            token_must_look_like_telegram=True,
        )
        _require_production_secret(result, "CRYPTOMUS_MERCHANT_ID", settings.cryptomus_merchant_id)
        _require_production_secret(result, "CRYPTOMUS_API_KEY", settings.cryptomus_api_key)
        _require_production_secret(
            result,
            "CRYPTOMUS_WEBHOOK_SECRET",
            settings.cryptomus_webhook_secret,
        )
        _require_production_secret(result, "RUNPOD_API_KEY", settings.runpod_api_key)
        _require_production_secret(result, "RUNPOD_TEMPLATE_ID", settings.runpod_template_id)
        _require_database_config(result, settings)
        _require_redis_config(result, settings)
        _require_storage_config(result, settings)
        _require_production_safety_defaults(result, settings)
    else:
        _warn_if_placeholder(result, "TELEGRAM_BOT_TOKEN", settings.telegram_bot_token)
        _warn_if_placeholder(result, "RUNPOD_API_KEY", settings.runpod_api_key)
        _warn_if_placeholder(result, "RUNPOD_TEMPLATE_ID", settings.runpod_template_id)
        _warn_if_storage_incomplete(result, settings)

    _warn_for_launch_risky_values(result, settings)
    return result


def _require_production_secret(
    result: ConfigSanityResult,
    name: str,
    value: str,
    *,
    token_must_look_like_telegram: bool = False,
) -> None:
    if _is_placeholder(value) or (token_must_look_like_telegram and ":" not in value.strip()):
        result.errors.append(f"{name} must be configured for production")


def _require_database_config(result: ConfigSanityResult, settings: Settings) -> None:
    for name, value in {
        "POSTGRES_HOST": settings.postgres_host,
        "POSTGRES_DB": settings.postgres_db,
        "POSTGRES_USER": settings.postgres_user,
        "POSTGRES_PASSWORD": settings.postgres_password,
    }.items():
        if _is_placeholder(value):
            result.errors.append(f"{name} must be configured for production")


def _require_redis_config(result: ConfigSanityResult, settings: Settings) -> None:
    if _is_placeholder(settings.redis_host) or settings.redis_port <= 0:
        result.errors.append("Redis connection settings must be configured for production")
    if _is_placeholder(settings.celery_broker_url) or _is_placeholder(
        settings.celery_result_backend
    ):
        result.errors.append("Celery Redis URLs must be configured for production")


def _require_storage_config(result: ConfigSanityResult, settings: Settings) -> None:
    provider = settings.storage_provider.strip().lower()
    if provider != StorageProvider.CLOUDFLARE_R2.value:
        result.errors.append("STORAGE_PROVIDER=cloudflare_r2 is required for production launch")
        return

    for name, value in {
        "CLOUDFLARE_R2_ACCOUNT_ID": settings.cloudflare_r2_account_id,
        "CLOUDFLARE_R2_ACCESS_KEY_ID": settings.cloudflare_r2_access_key_id,
        "CLOUDFLARE_R2_SECRET_ACCESS_KEY": settings.cloudflare_r2_secret_access_key,
        "CLOUDFLARE_R2_BUCKET": settings.cloudflare_r2_bucket,
    }.items():
        if _is_placeholder(value):
            result.errors.append(f"{name} must be configured for production")


def _require_production_safety_defaults(
    result: ConfigSanityResult,
    settings: Settings,
) -> None:
    if settings.debug_endpoints_enabled and not settings.debug_endpoints_local_only:
        result.errors.append("Debug endpoints must be disabled or local-only in production")
    if settings.distributed_segment_generation_enabled:
        result.errors.append(
            "DISTRIBUTED_SEGMENT_GENERATION_ENABLED must stay false for MVP launch"
        )
    if not settings.runpod_auto_terminate:
        result.errors.append("RUNPOD_AUTO_TERMINATE must be true for production launch")
    if not settings.runpod_keeper_enabled:
        result.errors.append("RUNPOD_KEEPER_ENABLED must be true for production launch")
    if not settings.runpod_waiting_gpu_enabled:
        result.errors.append("RUNPOD_WAITING_GPU_ENABLED must be true for production launch")
    if not settings.runpod_queue_wait_enabled:
        result.errors.append("RUNPOD_QUEUE_WAIT_ENABLED must be true for production launch")


def _warn_for_launch_risky_values(result: ConfigSanityResult, settings: Settings) -> None:
    if settings.debug_endpoints_enabled:
        result.warnings.append(
            "DEBUG_ENDPOINTS_ENABLED=true; keep it local-only and disabled publicly"
        )
    if settings.runpod_max_active_pods > 1:
        result.warnings.append("RUNPOD_MAX_ACTIVE_PODS is above MVP default 1")
    if settings.runpod_min_warm_pods > 0:
        result.warnings.append("RUNPOD_MIN_WARM_PODS is above MVP default 0 and may increase cost")
    if settings.celery_worker_concurrency != 1:
        result.warnings.append(
            "CELERY_WORKER_CONCURRENCY is not 1; verify RunPod/pod locking first"
        )
    if settings.distributed_segment_generation_enabled:
        result.warnings.append("Experimental distributed segment generation is enabled")


def _warn_if_storage_incomplete(result: ConfigSanityResult, settings: Settings) -> None:
    if settings.storage_provider.strip().lower() != StorageProvider.CLOUDFLARE_R2.value:
        return
    for name, value in {
        "CLOUDFLARE_R2_ACCOUNT_ID": settings.cloudflare_r2_account_id,
        "CLOUDFLARE_R2_ACCESS_KEY_ID": settings.cloudflare_r2_access_key_id,
        "CLOUDFLARE_R2_SECRET_ACCESS_KEY": settings.cloudflare_r2_secret_access_key,
        "CLOUDFLARE_R2_BUCKET": settings.cloudflare_r2_bucket,
    }.items():
        _warn_if_placeholder(result, name, value)


def _warn_if_placeholder(result: ConfigSanityResult, name: str, value: str) -> None:
    if _is_placeholder(value):
        result.warnings.append(f"{name} is not configured")


def _is_placeholder(value: str) -> bool:
    return value.strip() in PLACEHOLDER_VALUES
