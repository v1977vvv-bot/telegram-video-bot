from __future__ import annotations

from decimal import Decimal
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

    runpod_api_key: str = "change_me"
    runpod_template_id: str = "change_me"
    runpod_gpu_type: str = "RTX 4090"
    runpod_idle_timeout_seconds: int = 600
    runpod_max_active_pods: int = 1
    runpod_cloud_type: str = "COMMUNITY"
    runpod_allowed_gpu_types: str = "NVIDIA GeForce RTX 5090,NVIDIA GeForce RTX 4090"
    runpod_min_vcpu: int = 8
    runpod_min_ram_gb: int = 48
    runpod_container_disk_gb: int = 50
    runpod_volume_disk_gb: int = 100
    runpod_cuda_version: str = "12.8"
    runpod_comfyui_port: int = 8188
    runpod_pod_idle_shutdown_minutes: int = 10
    runpod_pod_ready_timeout_seconds: int = 900
    runpod_healthcheck_interval_seconds: int = 10
    runpod_auto_terminate: bool = True

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
