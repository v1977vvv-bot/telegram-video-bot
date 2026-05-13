from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class AvailableFormatResponse(BaseModel):
    label: str
    width: int
    height: int


class PublicSettingsResponse(BaseModel):
    price_per_second_usd: Decimal
    min_job_price_usd: Decimal
    generation_fps: int
    max_segment_seconds: int
    result_retention_hours: int
    available_formats: list[AvailableFormatResponse]
