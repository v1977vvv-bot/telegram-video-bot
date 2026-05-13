from __future__ import annotations

from fastapi import APIRouter

from backend.app.core.formats import AVAILABLE_GENERATION_FORMATS
from backend.app.schemas.settings import AvailableFormatResponse, PublicSettingsResponse
from shared.app.config import get_settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/public", response_model=PublicSettingsResponse)
async def public_settings() -> PublicSettingsResponse:
    settings = get_settings()
    return PublicSettingsResponse(
        price_per_second_usd=settings.pricing_price_per_second_usd,
        min_job_price_usd=settings.pricing_min_job_price_usd,
        generation_fps=settings.generation_fps,
        max_segment_seconds=settings.generation_max_segment_seconds,
        result_retention_hours=settings.result_retention_hours,
        available_formats=[
            AvailableFormatResponse(label=item.label, width=item.width, height=item.height)
            for item in AVAILABLE_GENERATION_FORMATS
        ],
    )
