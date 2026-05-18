from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.schemas.settings import AvailableFormatResponse


class GenerationDraftResponse(BaseModel):
    job_id: UUID
    status: str
    audio_duration_seconds: Decimal
    segments_count: int
    fps: int
    price_usd: Decimal
    available_formats: list[AvailableFormatResponse]


class GenerationFormatRequest(BaseModel):
    telegram_id: int = Field(gt=0)
    width: int
    height: int


class GenerationFormatResponse(BaseModel):
    job_id: UUID
    status: str
    width: int
    height: int
    fps: int
    audio_duration_seconds: Decimal
    segments_count: int
    price_usd: Decimal


class TelegramUserJobRequest(BaseModel):
    telegram_id: int = Field(gt=0)


class GenerationConfirmResponse(BaseModel):
    job_id: UUID
    status: str
    price_usd: Decimal
    message: str
    billing_account_type: str
    business_account_id: UUID | None = None
    business_account_name: str | None = None


class GenerationSegmentDetailResponse(BaseModel):
    segment_index: int
    status: str
    duration_seconds: Decimal
    frame_count: int


class GenerationJobDetailResponse(BaseModel):
    job_id: UUID
    status: str
    width: int
    height: int
    fps: int
    audio_duration_seconds: Decimal | None
    segments_count: int
    price_usd: Decimal | None
    mock_result_message: str | None
    created_at: datetime
    updated_at: datetime
    segments: list[GenerationSegmentDetailResponse]
