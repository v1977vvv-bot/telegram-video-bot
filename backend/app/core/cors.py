from __future__ import annotations

from shared.app.config import Settings


def parse_cors_origins(settings: Settings) -> list[str]:
    raw_value = settings.cors_allow_origins.strip()
    if raw_value == "*":
        return ["*"]
    return [origin.strip() for origin in raw_value.split(",") if origin.strip()]
