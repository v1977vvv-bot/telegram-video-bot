from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class BatchDraftRequest(BaseModel):
    telegram_id: int
    quality_profile: str = "480p"


class BatchConfirmRequest(BaseModel):
    telegram_id: int


class BatchUploadSessionRequest(BaseModel):
    telegram_id: int


class BatchUploadSessionResponse(BaseModel):
    web_app_url: str


class BatchWebConfirmRequest(BaseModel):
    token: str
    batch_id: UUID


class BatchDraftErrorResponse(BaseModel):
    code: str
    message: str
    filename: str | None = None


class BatchDraftItemResponse(BaseModel):
    item_id: UUID | None = None
    index: int
    basename: str
    image_filename: str
    audio_filename: str
    source_image_file_id: UUID | None = None
    source_audio_file_id: UUID | None = None
    audio_duration_seconds: Decimal
    price_usd: Decimal
    status: str
    generation_job_id: UUID | None = None


class BatchDraftResponse(BaseModel):
    batch_id: UUID | None = None
    status: str | None = None
    quality_profile: str
    total_jobs: int
    total_duration_seconds: Decimal | None = None
    total_price_usd: Decimal | None = None
    items: list[BatchDraftItemResponse]
    errors: list[BatchDraftErrorResponse]
    job_ids: list[UUID] | None = None


class BatchConfirmResponse(BatchDraftResponse):
    pass


class BatchDetailResponse(BatchDraftResponse):
    pass
