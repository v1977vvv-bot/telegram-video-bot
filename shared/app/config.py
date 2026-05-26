from __future__ import annotations

from decimal import Decimal, InvalidOperation
from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings shared by backend, bot, and worker."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "local"
    log_level: str = "INFO"
    debug_endpoints_enabled: bool = True
    debug_endpoints_local_only: bool = True
    admin_panel_enabled: bool = False
    admin_basic_auth_enabled: bool = True
    admin_basic_auth_username: str = "admin"
    admin_basic_auth_password: str = ""
    admin_session_cookie_name: str = "admin_session"
    admin_session_secret: str = ""
    admin_session_ttl_seconds: int = 86400
    admin_actions_enabled: bool = False
    admin_max_manual_topup_usd: Decimal = Field(default=Decimal("500"))
    admin_require_action_reason: bool = True
    admin_bot_token: str = ""
    admin_telegram_ids: str = ""
    admin_internal_api_token: str = ""
    admin_alerts_enabled: bool = True
    admin_alert_chat_id: str = ""
    admin_pod_alert_cooldown_minutes: int = 15
    admin_queue_alert_cooldown_minutes: int = 20
    admin_queue_alert_min_waiting_jobs: int = 2
    admin_queue_alert_target_wait_minutes: int = 10
    admin_queue_alert_repeat_enabled: bool = True

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "avatar_bot"
    postgres_user: str = "avatar_bot"
    postgres_password: str = "avatar_bot"

    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0

    telegram_bot_token: str = "change_me"

    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    backend_public_url: str = "http://localhost:8000"
    backend_internal_url: str = "http://backend:8000"
    cors_allow_origins: str = "*"
    support_telegram_username: str = "your_support_username"

    celery_broker_url: str = "redis://redis:6379/0"
    celery_result_backend: str = "redis://redis:6379/1"
    celery_worker_concurrency: int = 1

    storage_provider: str = "local"

    cloudflare_r2_account_id: str = "change_me"
    cloudflare_r2_access_key_id: str = "change_me"
    cloudflare_r2_secret_access_key: str = "change_me"
    cloudflare_r2_bucket: str = "change_me"
    cloudflare_r2_endpoint_url: str = ""
    cloudflare_r2_presigned_url_ttl_seconds: int = 86400
    cloudflare_r2_public_base_url: str = ""

    cryptomus_merchant_id: str = "change_me"
    cryptomus_api_key: str = "change_me"
    cryptomus_webhook_secret: str = "change_me"
    cryptomus_enabled: bool = False

    payment_provider: str = "cryptobot"
    payment_packages_enabled: bool = True
    payment_custom_amount_enabled: bool = False
    payment_packages_usd: str = "10,25,50,100"
    payment_display_currency: str = "USD"
    payment_provider_currency: str = "USDT"
    payment_usd_usdt_rate: Decimal = Field(default=Decimal("1"))
    payment_show_estimated_generations: bool = False

    cryptobot_pay_enabled: bool = True
    cryptobot_pay_api_token: str = ""
    cryptobot_pay_api_base_url: str = "https://pay.crypt.bot/api"
    cryptobot_pay_asset: str = "USDT"
    cryptobot_pay_webhook_secret: str = ""
    cryptobot_pay_webhook_path: str = "/api/v1/payments/cryptobot/webhook"
    cryptobot_pay_webhook_url: str = ""
    cryptobot_pay_allow_comments: bool = False
    cryptobot_pay_allow_anonymous: bool = True
    cryptobot_pay_expires_in_seconds: int = 3600

    runpod_api_key: str = "change_me"
    runpod_template_id: str = "change_me"
    runpod_gpu_type: str = "RTX 4090"
    runpod_idle_timeout_seconds: int = 600
    runpod_max_active_pods: int = 1
    runpod_cloud_type: str = "COMMUNITY"
    runpod_primary_cloud_type: str = "SECURE"
    runpod_fallback_cloud_type: str = "COMMUNITY"
    runpod_allowed_gpu_types: str = "NVIDIA GeForce RTX 4090"
    runpod_fallback_allowed_gpu_types: str = "NVIDIA GeForce RTX 4090"
    runpod_min_vcpu: int = 32
    runpod_min_ram_gb: int = 50
    runpod_fallback_min_ram_gb: int | None = 50
    runpod_container_disk_gb: int = 100
    runpod_volume_disk_gb: int = 0
    runpod_cuda_version: str = "12.8"
    runpod_allowed_cuda_versions: str = "12.8"
    runpod_fallback_allowed_cuda_versions: str = ""
    runpod_comfyui_port: int = 8188
    runpod_ports: str = "8188/http"
    runpod_fallback_ports: str = ""
    runpod_min_download: int | None = 1000
    runpod_min_upload: int | None = 1000
    runpod_support_public_ip: bool = False
    runpod_start_jupyter: bool = True
    runpod_start_ssh: bool = True
    runpod_global_network: bool = False
    runpod_experimental_low_vram_startup: bool = False
    runpod_fallback_min_download: str = ""
    runpod_fallback_min_upload: str = ""
    runpod_fallback_support_public_ip: str = ""
    runpod_fallback_start_jupyter: str = ""
    runpod_fallback_start_ssh: str = ""
    runpod_fallback_global_network: str = ""
    runpod_pod_idle_shutdown_minutes: int = 20
    runpod_pod_ready_timeout_seconds: int = 7200
    runpod_healthcheck_interval_seconds: int = 15
    runpod_auto_terminate: bool = True
    runpod_keeper_enabled: bool = True
    runpod_keeper_interval_seconds: int = 120
    runpod_warm_pod_enabled: bool = True
    runpod_autoscaling_enabled: bool = True
    runpod_autoscaling_strategy: str = "queue_time"
    runpod_target_queue_wait_minutes: int = 30
    runpod_min_warm_pods: int = 0
    runpod_scale_up_cooldown_seconds: int = 120
    runpod_scale_down_cooldown_seconds: int = 300
    runpod_max_warm_pods_to_create_per_tick: int = 1
    runpod_estimated_generation_speed_factor: Decimal = Field(default=Decimal("20"))
    runpod_max_estimated_gpu_minutes_per_tick: Decimal = Field(default=Decimal("240"))
    runpod_max_estimated_hourly_gpu_cost_usd: Decimal = Field(default=Decimal("3.00"))
    runpod_estimated_pod_hourly_cost_usd: Decimal = Field(default=Decimal("0.80"))
    runpod_default_job_duration_seconds: int = 60
    runpod_estimated_cold_start_seconds: int = 720
    runpod_short_job_cold_start_avoidance_enabled: bool = True
    runpod_short_job_max_duration_seconds: int = 90
    runpod_create_max_attempts: int = 3
    runpod_primary_create_max_attempts: int = 3
    runpod_fallback_create_max_attempts: int = 6
    runpod_create_retry_sleep_seconds: int = 20
    runpod_cost_tracking_enabled: bool = True
    runpod_default_hourly_cost_usd: Decimal = Field(default=Decimal("0.80"))
    runpod_gpu_hourly_costs_usd: str = "NVIDIA L40S:0.75,NVIDIA GeForce RTX 4090:0.55"
    runpod_secure_gpu_price_per_hour_usd: str = ""
    runpod_community_gpu_price_per_hour_usd: str = ""
    runpod_secure_startup_surcharge_usd: str = "0"
    runpod_community_cold_start_surcharge_usd: str = ""
    runpod_secure_storage_price_per_gb_month_usd: str = ""
    runpod_community_storage_price_per_gb_month_usd: str = "0"
    runpod_billing_margin_percent: str = ""
    runpod_cost_include_cold_start: bool = True
    runpod_cost_include_idle_time: bool = False
    runpod_cost_min_billing_seconds: int = 60
    runpod_cost_rounding_mode: str = "up_to_second"
    runpod_waiting_gpu_enabled: bool = True
    runpod_waiting_gpu_retry_seconds: int = 120
    runpod_waiting_gpu_max_wait_minutes: int = 30
    runpod_queue_wait_enabled: bool = True
    runpod_queue_retry_seconds: int = 60
    runpod_queue_max_wait_minutes: int = 60
    runpod_queue_load_planning_enabled: bool = True
    runpod_target_queue_minutes_per_pod_min: Decimal = Field(default=Decimal("5"))
    runpod_target_queue_minutes_per_pod_max: Decimal = Field(default=Decimal("6"))
    runpod_queue_load_alert_min_total_minutes: Decimal = Field(default=Decimal("5"))
    runpod_queue_load_max_recommended_pods: int = 5
    runpod_queue_load_include_generating: bool = True
    runpod_discovery_enabled: bool = True
    runpod_discovery_interval_seconds: int = 60
    runpod_discovery_auto_register: bool = True
    runpod_discovery_require_healthy: bool = True

    distributed_segment_generation_enabled: bool = False
    distributed_min_audio_duration_seconds: int = 60
    distributed_max_parallel_segments_per_job: int = 2
    distributed_segment_target_seconds: int = 30
    distributed_segment_max_retries: int = 2
    distributed_require_warm_pods: bool = True
    distributed_allow_create_extra_pods: bool = False
    distributed_stitch_strategy: str = "concat"
    distributed_experimental_logging: bool = True
    distributed_segment_image_strategy: str = "source_image"

    generation_mode: str = "mock"
    comfyui_port: int = 8188
    comfyui_base_url: str = "http://localhost:8188"
    comfyui_workflow_path: str = "/app/workflows/infinite_talk_api.json"
    comfyui_model_profile: str = "fp8_480p"
    comfyui_timeout_seconds: int = 7200
    comfyui_poll_interval_seconds: int = 5
    comfyui_transient_retry_max_attempts: int = 5
    comfyui_transient_retry_backoff_seconds: int = 5
    comfyui_transient_retry_backoff_max_seconds: int = 30
    comfyui_input_subfolder: str = "ultronlab"
    comfyui_output_subfolder: str = "InfiniteTalk"

    pricing_price_per_second_usd: Decimal = Field(default=Decimal("0.012"))
    pricing_min_job_price_usd: Decimal = Field(default=Decimal("0.30"))
    generation_fps: int = 25
    generation_max_segment_seconds: int = 30
    segment_image_strategy: str = "last_frame"
    audio_segmentation_strategy: str = "fixed"
    audio_silence_threshold_db: float = -35
    audio_silence_min_duration_seconds: Decimal = Field(default=Decimal("0.30"))
    audio_silence_search_window_seconds: Decimal = Field(default=Decimal("7"))
    audio_segment_min_seconds: Decimal = Field(default=Decimal("8"))
    result_retention_hours: int = 48

    local_storage_dir: str = "/app/storage"
    max_image_size_mb: int = 20
    max_audio_size_mb: int = 100
    generation_max_audio_seconds: int = 1800
    debug_admin_telegram_ids: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        return (
            "postgresql+asyncpg://"
            f"{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        return (
            "postgresql+psycopg://"
            f"{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def resolved_cloudflare_r2_endpoint_url(self) -> str:
        endpoint_url = self.cloudflare_r2_endpoint_url.strip()
        if endpoint_url:
            return endpoint_url.rstrip("/")
        return f"https://{self.cloudflare_r2_account_id}.r2.cloudflarestorage.com"

    @property
    def cloudflare_r2_public_base_url_or_none(self) -> str | None:
        base_url = self.cloudflare_r2_public_base_url.strip().rstrip("/")
        if not base_url or base_url == "change_me":
            return None
        return base_url

    @property
    def telegram_token_is_configured(self) -> bool:
        token = self.telegram_bot_token.strip()
        return bool(token and token != "change_me" and ":" in token)

    @property
    def admin_bot_token_is_configured(self) -> bool:
        token = self.admin_bot_token.strip()
        return bool(token and token != "change_me" and ":" in token)

    @property
    def admin_alert_bot_token(self) -> str:
        admin_token = self.admin_bot_token.strip()
        if self.admin_bot_token_is_configured:
            return admin_token
        return self.telegram_bot_token.strip()

    @property
    def admin_alert_bot_token_is_configured(self) -> bool:
        return self.admin_bot_token_is_configured or self.telegram_token_is_configured

    @property
    def debug_admin_ids(self) -> set[int]:
        values: set[int] = set()
        for item in self.debug_admin_telegram_ids.split(","):
            item = item.strip()
            if item:
                values.add(int(item))
        return values

    @property
    def admin_telegram_id_set(self) -> set[int]:
        values: set[int] = set()
        for item in self.admin_telegram_ids.split(","):
            item = item.strip()
            if item:
                values.add(int(item))
        return values

    @property
    def admin_internal_api_token_configured(self) -> bool:
        token = self.admin_internal_api_token.strip()
        return bool(token and token != "change_me")

    @property
    def runpod_allowed_gpu_type_list(self) -> list[str]:
        return [item.strip() for item in self.runpod_allowed_gpu_types.split(",") if item.strip()]

    @property
    def runpod_fallback_allowed_gpu_type_list(self) -> list[str]:
        return [
            item.strip()
            for item in self.runpod_fallback_allowed_gpu_types.split(",")
            if item.strip()
        ]

    @property
    def runpod_auto_manager_enabled(self) -> bool:
        api_key = self.runpod_api_key.strip()
        template_id = self.runpod_template_id.strip()
        return bool(
            api_key and api_key != "change_me" and template_id and template_id != "change_me"
        )

    @property
    def runpod_ram_fallback_enabled(self) -> bool:
        return (
            self.runpod_fallback_min_ram_gb is not None
            and self.runpod_fallback_min_ram_gb > 0
            and self.runpod_fallback_min_ram_gb < self.runpod_min_ram_gb
        )

    @property
    def runpod_gpu_hourly_costs_map(self) -> dict[str, Decimal]:
        costs: dict[str, Decimal] = {}
        raw_map = self.runpod_gpu_hourly_costs_usd.strip()
        if not raw_map:
            return costs

        for raw_entry in raw_map.split(","):
            raw_entry = raw_entry.strip()
            if not raw_entry:
                continue
            if ":" not in raw_entry:
                raise ValueError("GPU hourly cost entries must use 'GPU name:cost' format")
            gpu_type, raw_cost = raw_entry.rsplit(":", maxsplit=1)
            gpu_type = gpu_type.strip()
            if not gpu_type:
                raise ValueError("GPU hourly cost GPU name cannot be empty")
            try:
                cost = Decimal(raw_cost.strip())
            except InvalidOperation as exc:
                raise ValueError(f"Invalid hourly cost for GPU type {gpu_type}") from exc
            if cost <= Decimal("0"):
                raise ValueError(f"Hourly cost for GPU type {gpu_type} must be positive")
            costs[gpu_type] = cost

        return costs

    @property
    def runpod_secure_gpu_price_per_hour(self) -> Decimal | None:
        return _optional_nonnegative_decimal(self.runpod_secure_gpu_price_per_hour_usd)

    @property
    def runpod_community_gpu_price_per_hour(self) -> Decimal | None:
        return _optional_nonnegative_decimal(self.runpod_community_gpu_price_per_hour_usd)

    @property
    def runpod_secure_startup_surcharge(self) -> Decimal:
        return _optional_nonnegative_decimal(self.runpod_secure_startup_surcharge_usd) or Decimal(
            "0"
        )

    @property
    def runpod_community_cold_start_surcharge(self) -> Decimal:
        return _optional_nonnegative_decimal(
            self.runpod_community_cold_start_surcharge_usd
        ) or Decimal("0")

    @property
    def runpod_secure_storage_price_per_gb_month(self) -> Decimal | None:
        return _optional_nonnegative_decimal(self.runpod_secure_storage_price_per_gb_month_usd)

    @property
    def runpod_community_storage_price_per_gb_month(self) -> Decimal | None:
        return _optional_nonnegative_decimal(self.runpod_community_storage_price_per_gb_month_usd)

    @property
    def runpod_billing_margin_percent_value(self) -> Decimal | None:
        return _optional_nonnegative_decimal(self.runpod_billing_margin_percent)

    @property
    def runpod_cloud_specific_pricing_configured(self) -> bool:
        return (
            self.runpod_secure_gpu_price_per_hour is not None
            and self.runpod_community_gpu_price_per_hour is not None
        )

    @property
    def payment_package_amounts_usd(self) -> list[Decimal]:
        amounts: list[Decimal] = []
        for raw_amount in self.payment_packages_usd.split(","):
            raw_amount = raw_amount.strip()
            if not raw_amount:
                continue
            amount = Decimal(raw_amount).quantize(Decimal("0.01"))
            if amount < Decimal("1.00"):
                raise ValueError("Payment packages must be at least 1 USD")
            if amount <= Decimal("0"):
                raise ValueError("Payment packages must be positive")
            amounts.append(amount)

        unique_amounts = sorted(set(amounts))
        if len(unique_amounts) != len(amounts):
            raise ValueError("Payment packages must be unique")
        if unique_amounts != amounts:
            raise ValueError("Payment packages must be sorted ascending")
        if not unique_amounts:
            raise ValueError("At least one payment package must be configured")
        return unique_amounts

    @property
    def payment_provider_normalized(self) -> str:
        return self.payment_provider.strip().lower()

    @property
    def cryptobot_pay_configured(self) -> bool:
        token = self.cryptobot_pay_api_token.strip()
        return bool(token and token != "change_me")

    @property
    def comfyui_allowed_model_profiles(self) -> tuple[str, ...]:
        return ("gguf_q8_480p", "fp8_480p", "fp8_720p")

    @property
    def comfyui_model_profile_normalized(self) -> str:
        profile = self.comfyui_model_profile.strip().lower()
        if profile not in self.comfyui_allowed_model_profiles:
            allowed = ", ".join(self.comfyui_allowed_model_profiles)
            raise ValueError(f"COMFYUI_MODEL_PROFILE must be one of: {allowed}")
        return profile


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def _optional_nonnegative_decimal(raw_value: str | Decimal | int | float | None) -> Decimal | None:
    if raw_value is None:
        return None
    raw = str(raw_value).strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal value: {raw}") from exc
    if value < Decimal("0"):
        raise ValueError(f"Decimal value must be non-negative: {raw}")
    return value
