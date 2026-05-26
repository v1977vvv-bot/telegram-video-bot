from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from shared.app.config import Settings
from shared.app.enums import PaymentProvider, StorageProvider
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
        _require_production_secret(result, "RUNPOD_API_KEY", settings.runpod_api_key)
        _require_production_secret(result, "RUNPOD_TEMPLATE_ID", settings.runpod_template_id)
        _require_database_config(result, settings)
        _require_redis_config(result, settings)
        _require_storage_config(result, settings)
        _require_payment_package_config(result, settings)
        _require_payment_provider_config(result, settings)
        _require_runpod_cost_config(result, settings)
        _require_comfyui_config(result, settings)
        _require_admin_config(result, settings)
        _require_production_safety_defaults(result, settings)
    else:
        _warn_if_placeholder(result, "TELEGRAM_BOT_TOKEN", settings.telegram_bot_token)
        _warn_if_placeholder(result, "RUNPOD_API_KEY", settings.runpod_api_key)
        _warn_if_placeholder(result, "RUNPOD_TEMPLATE_ID", settings.runpod_template_id)
        _warn_if_storage_incomplete(result, settings)
        _warn_if_payment_package_config_invalid(result, settings)
        _warn_if_payment_provider_config_invalid(result, settings)
        _warn_if_runpod_cost_config_invalid(result, settings)
        _warn_if_comfyui_config_invalid(result, settings)
        _warn_if_admin_config_invalid(result, settings)

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
    if settings.debug_endpoints_enabled:
        result.errors.append("DEBUG_ENDPOINTS_ENABLED must be false in production")
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
    if _contains_rtx_5090(settings.runpod_allowed_gpu_type_list):
        result.errors.append("RUNPOD_ALLOWED_GPU_TYPES must not include RTX 5090")
    if _contains_rtx_5090(settings.runpod_fallback_allowed_gpu_type_list):
        result.errors.append("RUNPOD_FALLBACK_ALLOWED_GPU_TYPES must not include RTX 5090")


def _require_payment_package_config(result: ConfigSanityResult, settings: Settings) -> None:
    try:
        _ = settings.payment_package_amounts_usd
    except (ArithmeticError, ValueError) as exc:
        result.errors.append(f"PAYMENT_PACKAGES_USD is invalid: {exc}")
    if not settings.payment_packages_enabled:
        result.errors.append("PAYMENT_PACKAGES_ENABLED must be true for MVP launch")
    if settings.payment_custom_amount_enabled:
        result.errors.append("PAYMENT_CUSTOM_AMOUNT_ENABLED must be false for MVP launch")
    if settings.payment_display_currency.upper() != "USD":
        result.errors.append("PAYMENT_DISPLAY_CURRENCY must be USD")
    if settings.payment_provider_currency.upper() != "USDT":
        result.errors.append("PAYMENT_PROVIDER_CURRENCY must be USDT")
    if settings.payment_usd_usdt_rate != 1:
        result.errors.append("PAYMENT_USD_USDT_RATE must be 1 for MVP launch")


def _require_payment_provider_config(result: ConfigSanityResult, settings: Settings) -> None:
    provider = settings.payment_provider_normalized
    if provider not in {item.value for item in PaymentProvider}:
        result.errors.append("PAYMENT_PROVIDER must be one of cryptobot, cryptomus, manual")
        return
    if provider == PaymentProvider.CRYPTOBOT.value:
        if not settings.cryptobot_pay_enabled:
            result.errors.append(
                "CRYPTOBOT_PAY_ENABLED must be true when PAYMENT_PROVIDER=cryptobot"
            )
        if not settings.cryptobot_pay_configured:
            result.errors.append("CRYPTOBOT_PAY_API_TOKEN must be configured for production")
        if settings.cryptobot_pay_asset.upper() != "USDT":
            result.errors.append("CRYPTOBOT_PAY_ASSET must be USDT for MVP launch")
        if settings.payment_provider_currency.upper() != "USDT":
            result.errors.append("PAYMENT_PROVIDER_CURRENCY must be USDT for CryptoBot")
        if not settings.cryptobot_pay_webhook_url.strip():
            result.warnings.append("CRYPTOBOT_PAY_WEBHOOK_URL is empty in production")
    elif provider == PaymentProvider.CRYPTOMUS.value:
        if not settings.cryptomus_enabled:
            result.errors.append("CRYPTOMUS_ENABLED must be true when PAYMENT_PROVIDER=cryptomus")
        _require_production_secret(result, "CRYPTOMUS_MERCHANT_ID", settings.cryptomus_merchant_id)
        _require_production_secret(result, "CRYPTOMUS_API_KEY", settings.cryptomus_api_key)
        _require_production_secret(
            result,
            "CRYPTOMUS_WEBHOOK_SECRET",
            settings.cryptomus_webhook_secret,
        )


def _warn_if_payment_provider_config_invalid(
    result: ConfigSanityResult,
    settings: Settings,
) -> None:
    provider = settings.payment_provider_normalized
    if provider not in {item.value for item in PaymentProvider}:
        result.warnings.append("PAYMENT_PROVIDER must be one of cryptobot, cryptomus, manual")
        return
    if provider == PaymentProvider.CRYPTOBOT.value:
        if not settings.cryptobot_pay_enabled:
            result.warnings.append("CRYPTOBOT_PAY_ENABLED=false while PAYMENT_PROVIDER=cryptobot")
        if not settings.cryptobot_pay_configured:
            result.warnings.append("CRYPTOBOT_PAY_API_TOKEN is not configured")
        if settings.cryptobot_pay_asset.upper() != "USDT":
            result.warnings.append("CRYPTOBOT_PAY_ASSET should be USDT for MVP")
    elif provider == PaymentProvider.CRYPTOMUS.value:
        if not settings.cryptomus_enabled:
            result.warnings.append("CRYPTOMUS_ENABLED=false while PAYMENT_PROVIDER=cryptomus")


def _require_runpod_cost_config(result: ConfigSanityResult, settings: Settings) -> None:
    errors = _runpod_cost_config_errors(settings)
    for error in errors:
        result.errors.append(error)


def _require_comfyui_config(result: ConfigSanityResult, settings: Settings) -> None:
    try:
        _ = settings.comfyui_model_profile_normalized
    except ValueError as exc:
        result.errors.append(str(exc))


def _require_admin_config(result: ConfigSanityResult, settings: Settings) -> None:
    if settings.admin_bot_token_is_configured:
        if not settings.admin_telegram_id_set:
            result.errors.append(
                "ADMIN_TELEGRAM_IDS must be configured when ADMIN_BOT_TOKEN is set"
            )
        if not settings.admin_internal_api_token_configured:
            result.errors.append(
                "ADMIN_INTERNAL_API_TOKEN must be configured when ADMIN_BOT_TOKEN is set"
            )
    if settings.admin_internal_api_token.strip() and len(settings.admin_internal_api_token) < 24:
        result.errors.append("ADMIN_INTERNAL_API_TOKEN must be at least 24 characters when set")
    if settings.admin_actions_enabled:
        if settings.admin_max_manual_topup_usd <= Decimal("0"):
            result.errors.append("ADMIN_MAX_MANUAL_TOPUP_USD must be positive")
        if settings.admin_max_manual_topup_usd > Decimal("10000"):
            result.errors.append("ADMIN_MAX_MANUAL_TOPUP_USD is above the MVP safety cap")

    if not settings.admin_panel_enabled:
        return
    if not settings.admin_basic_auth_enabled:
        result.errors.append("ADMIN_BASIC_AUTH_ENABLED must be true when admin panel is enabled")
    if _is_placeholder(settings.admin_basic_auth_username):
        result.errors.append(
            "ADMIN_BASIC_AUTH_USERNAME must be configured when admin panel is enabled"
        )
    password = settings.admin_basic_auth_password
    if _is_placeholder(password) or len(password) < 12:
        result.errors.append(
            "ADMIN_BASIC_AUTH_PASSWORD must be configured and at least 12 characters"
        )


def _warn_if_payment_package_config_invalid(
    result: ConfigSanityResult,
    settings: Settings,
) -> None:
    try:
        _ = settings.payment_package_amounts_usd
    except (ArithmeticError, ValueError) as exc:
        result.warnings.append(f"PAYMENT_PACKAGES_USD is invalid: {exc}")


def _warn_if_runpod_cost_config_invalid(
    result: ConfigSanityResult,
    settings: Settings,
) -> None:
    for error in _runpod_cost_config_errors(settings):
        result.warnings.append(error)


def _warn_if_comfyui_config_invalid(
    result: ConfigSanityResult,
    settings: Settings,
) -> None:
    try:
        _ = settings.comfyui_model_profile_normalized
    except ValueError as exc:
        result.warnings.append(str(exc))


def _warn_if_admin_config_invalid(result: ConfigSanityResult, settings: Settings) -> None:
    if settings.admin_bot_token_is_configured:
        if not settings.admin_telegram_id_set:
            result.warnings.append(
                "ADMIN_TELEGRAM_IDS is empty while ADMIN_BOT_TOKEN is configured"
            )
        if not settings.admin_internal_api_token_configured:
            result.warnings.append(
                "ADMIN_INTERNAL_API_TOKEN is not configured while ADMIN_BOT_TOKEN is configured"
            )
        if not settings.admin_actions_enabled:
            result.warnings.append(
                "ADMIN_ACTIONS_ENABLED=false; Telegram admin write buttons will be disabled"
            )
    if settings.admin_internal_api_token.strip() and len(settings.admin_internal_api_token) < 24:
        result.warnings.append("ADMIN_INTERNAL_API_TOKEN is shorter than 24 characters")
    if settings.admin_actions_enabled:
        if settings.admin_max_manual_topup_usd <= Decimal("0"):
            result.warnings.append("ADMIN_MAX_MANUAL_TOPUP_USD must be positive")
        if settings.admin_max_manual_topup_usd > Decimal("10000"):
            result.warnings.append("ADMIN_MAX_MANUAL_TOPUP_USD is above the MVP safety cap")

    if not settings.admin_panel_enabled:
        return
    if not settings.admin_basic_auth_enabled:
        result.warnings.append("ADMIN_BASIC_AUTH_ENABLED=false while admin panel is enabled")
    if _is_placeholder(settings.admin_basic_auth_username):
        result.warnings.append("ADMIN_BASIC_AUTH_USERNAME is not configured")
    if _is_placeholder(settings.admin_basic_auth_password):
        result.warnings.append("ADMIN_BASIC_AUTH_PASSWORD is not configured")
    elif len(settings.admin_basic_auth_password) < 12:
        result.warnings.append("ADMIN_BASIC_AUTH_PASSWORD is shorter than 12 characters")


def _runpod_cost_config_errors(settings: Settings) -> list[str]:
    if not settings.runpod_cost_tracking_enabled:
        return []

    errors: list[str] = []
    if settings.runpod_default_hourly_cost_usd <= Decimal("0"):
        errors.append("RUNPOD_DEFAULT_HOURLY_COST_USD must be positive")
    try:
        _ = settings.runpod_gpu_hourly_costs_map
    except (ArithmeticError, ValueError) as exc:
        errors.append(f"RUNPOD_GPU_HOURLY_COSTS_USD is invalid: {exc}")
    for name, getter in (
        ("RUNPOD_SECURE_GPU_PRICE_PER_HOUR_USD", lambda: settings.runpod_secure_gpu_price_per_hour),
        (
            "RUNPOD_COMMUNITY_GPU_PRICE_PER_HOUR_USD",
            lambda: settings.runpod_community_gpu_price_per_hour,
        ),
        ("RUNPOD_SECURE_STARTUP_SURCHARGE_USD", lambda: settings.runpod_secure_startup_surcharge),
        (
            "RUNPOD_COMMUNITY_COLD_START_SURCHARGE_USD",
            lambda: settings.runpod_community_cold_start_surcharge,
        ),
        (
            "RUNPOD_SECURE_STORAGE_PRICE_PER_GB_MONTH_USD",
            lambda: settings.runpod_secure_storage_price_per_gb_month,
        ),
        (
            "RUNPOD_COMMUNITY_STORAGE_PRICE_PER_GB_MONTH_USD",
            lambda: settings.runpod_community_storage_price_per_gb_month,
        ),
        ("RUNPOD_BILLING_MARGIN_PERCENT", lambda: settings.runpod_billing_margin_percent_value),
    ):
        try:
            getter()
        except (ArithmeticError, ValueError) as exc:
            errors.append(f"{name} is invalid: {exc}")
    if settings.runpod_cost_min_billing_seconds < 0:
        errors.append("RUNPOD_COST_MIN_BILLING_SECONDS must be >= 0")
    return errors


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
    if settings.runpod_cost_rounding_mode.strip().lower() != "up_to_second":
        result.warnings.append(
            "RUNPOD_COST_ROUNDING_MODE is unknown; cost tracking falls back to up_to_second"
        )
    if (
        settings.runpod_cost_tracking_enabled
        and not settings.runpod_cloud_specific_pricing_configured
    ):
        result.warnings.append(
            "RunPod cloud-specific GPU prices are not fully configured; "
            "cost tracking uses fallback GPU pricing"
        )
    if settings.admin_actions_enabled:
        result.warnings.append("ADMIN_ACTIONS_ENABLED=true; restrict operator access carefully")
    if settings.admin_alerts_enabled and not settings.admin_bot_token_is_configured:
        result.warnings.append(
            "ADMIN_BOT_TOKEN is not configured; admin alerts fall back to TELEGRAM_BOT_TOKEN"
        )
    if _contains_rtx_5090(settings.runpod_allowed_gpu_type_list):
        result.warnings.append("RUNPOD_ALLOWED_GPU_TYPES includes RTX 5090; it is disabled for now")
    if _contains_rtx_5090(settings.runpod_fallback_allowed_gpu_type_list):
        result.warnings.append(
            "RUNPOD_FALLBACK_ALLOWED_GPU_TYPES includes RTX 5090; it is disabled for now"
        )


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


def _contains_rtx_5090(gpu_types: list[str]) -> bool:
    return any("5090" in gpu_type for gpu_type in gpu_types)
