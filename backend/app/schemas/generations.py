from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class GenerationHistoryItemResponse(BaseModel):
    id: UUID
    status: str
    width: int
    height: int
    fps: int
    audio_duration_seconds: Decimal | None
    segments_count: int
    price_usd: Decimal | None
    error_message: str | None
    mock_result_message: str | None
    result_file_id: UUID | None
    result_url: str | None
    result_url_expires_in_seconds: int | None
    created_at: datetime


class GenerationHistoryResponse(BaseModel):
    items: list[GenerationHistoryItemResponse]
