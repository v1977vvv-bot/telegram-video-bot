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

    payment_packages_enabled: bool = True
    payment_custom_amount_enabled: bool = False
    payment_packages_usd: str = "10,25,50,100"
    payment_display_currency: str = "USD"
    payment_provider_currency: str = "USDT"
    payment_usd_usdt_rate: Decimal = Field(default=Decimal("1"))
    payment_show_estimated_generations: bool = False

    runpod_api_key: str = "change_me"
    runpod_template_id: str = "change_me"
    runpod_gpu_type: str = "RTX 4090"
    runpod_idle_timeout_seconds: int = 600
    runpod_max_active_pods: int = 1
    runpod_cloud_type: str = "COMMUNITY"
    runpod_allowed_gpu_types: str = "NVIDIA GeForce RTX 5090,NVIDIA GeForce RTX 4090"
    runpod_min_vcpu: int = 8
    runpod_min_ram_gb: int = 48
    runpod_fallback_min_ram_gb: int | None = 48
    runpod_container_disk_gb: int = 50
    runpod_volume_disk_gb: int = 100
    runpod_cuda_version: str = "12.8"
    runpod_comfyui_port: int = 8188
    runpod_pod_idle_shutdown_minutes: int = 20
    runpod_pod_ready_timeout_seconds: int = 900
    runpod_healthcheck_interval_seconds: int = 10
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
    runpod_create_retry_sleep_seconds: int = 20
    runpod_cost_tracking_enabled: bool = True
    runpod_default_hourly_cost_usd: Decimal = Field(default=Decimal("0.80"))
    runpod_gpu_hourly_costs_usd: str = (
        "NVIDIA GeForce RTX 5090:0.80,NVIDIA L40S:0.75,NVIDIA GeForce RTX 4090:0.55"
    )
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
    def debug_admin_ids(self) -> set[int]:
        values: set[int] = set()
        for item in self.debug_admin_telegram_ids.split(","):
            item = item.strip()
            if item:
                values.add(int(item))
        return values

    @property
    def runpod_allowed_gpu_type_list(self) -> list[str]:
        return [item.strip() for item in self.runpod_allowed_gpu_types.split(",") if item.strip()]

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
