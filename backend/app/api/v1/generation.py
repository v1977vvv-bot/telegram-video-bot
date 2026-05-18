from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.schemas.generation import (
    GenerationConfirmResponse,
    GenerationDraftResponse,
    GenerationFormatRequest,
    GenerationFormatResponse,
    GenerationJobDetailResponse,
    GenerationSegmentDetailResponse,
    TelegramUserJobRequest,
)
from backend.app.schemas.settings import AvailableFormatResponse
from backend.app.services.generation import FilePayload, GenerationService
from backend.app.workers.celery_client import enqueue_generation_job
from shared.app.database import get_session

router = APIRouter(prefix="/generation", tags=["generation"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
TelegramIdForm = Annotated[int, Form(gt=0)]
UploadFileDep = Annotated[UploadFile, File()]
TelegramIdQuery = Annotated[int, Query(gt=0)]


@router.post("/drafts", response_model=GenerationDraftResponse)
async def create_generation_draft(
    session: SessionDep,
    telegram_id: TelegramIdForm,
    image: UploadFileDep,
    audio: UploadFileDep,
) -> GenerationDraftResponse:
    summary = await GenerationService(session).create_draft(
        telegram_id=telegram_id,
        image=FilePayload(
            original_filename=image.filename or "image",
            content=await image.read(),
            mime_type=image.content_type or "application/octet-stream",
        ),
        audio=FilePayload(
            original_filename=audio.filename or "audio",
            content=await audio.read(),
            mime_type=audio.content_type or "application/octet-stream",
        ),
    )
    return GenerationDraftResponse(
        job_id=summary.job_id,
        status=summary.status,
        audio_duration_seconds=summary.audio_duration_seconds,
        segments_count=summary.segments_count,
        fps=summary.fps,
        price_usd=summary.price_usd,
        available_formats=[
            AvailableFormatResponse(label=item.label, width=item.width, height=item.height)
            for item in summary.available_formats
        ],
    )


@router.patch("/drafts/{job_id}/format", response_model=GenerationFormatResponse)
async def update_generation_format(
    job_id: UUID,
    payload: GenerationFormatRequest,
    session: SessionDep,
) -> GenerationFormatResponse:
    summary = await GenerationService(session).update_draft_format(
        job_id=job_id,
        telegram_id=payload.telegram_id,
        width=payload.width,
        height=payload.height,
    )
    return GenerationFormatResponse(
        job_id=summary.job_id,
        status=summary.status,
        width=summary.width,
        height=summary.height,
        fps=summary.fps,
        audio_duration_seconds=summary.audio_duration_seconds,
        segments_count=summary.segments_count,
        price_usd=summary.price_usd,
    )


@router.post("/drafts/{job_id}/confirm", response_model=GenerationConfirmResponse)
async def confirm_generation_draft(
    job_id: UUID,
    payload: TelegramUserJobRequest,
    session: SessionDep,
) -> GenerationConfirmResponse:
    summary = await GenerationService(session).confirm_draft(
        job_id=job_id,
        telegram_id=payload.telegram_id,
    )
    enqueue_generation_job(str(summary.job_id))
    return GenerationConfirmResponse(
        job_id=summary.job_id,
        status=summary.status,
        price_usd=summary.price_usd,
        message=summary.message,
        billing_account_type=summary.billing_account_type,
        business_account_id=summary.business_account_id,
        business_account_name=summary.business_account_name,
    )


@router.post("/drafts/{job_id}/cancel", response_model=GenerationConfirmResponse)
async def cancel_generation_draft(
    job_id: UUID,
    payload: TelegramUserJobRequest,
    session: SessionDep,
) -> GenerationConfirmResponse:
    summary = await GenerationService(session).cancel_job(
        job_id=job_id,
        telegram_id=payload.telegram_id,
    )
    return GenerationConfirmResponse(
        job_id=summary.job_id,
        status=summary.status,
        price_usd=summary.price_usd,
        message=summary.message,
        billing_account_type=summary.billing_account_type,
        business_account_id=summary.business_account_id,
        business_account_name=summary.business_account_name,
    )


@router.get("/jobs/{job_id}", response_model=GenerationJobDetailResponse)
async def get_generation_job(
    job_id: UUID,
    telegram_id: TelegramIdQuery,
    session: SessionDep,
) -> GenerationJobDetailResponse:
    job = await GenerationService(session).get_job_detail(job_id=job_id, telegram_id=telegram_id)
    return GenerationJobDetailResponse(
        job_id=job.id,
        status=job.status,
        width=job.width,
        height=job.height,
        fps=job.fps,
        audio_duration_seconds=job.audio_duration_seconds,
        segments_count=job.segments_count,
        price_usd=job.price_usd,
        mock_result_message=job.mock_result_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
        segments=[
            GenerationSegmentDetailResponse(
                segment_index=segment.segment_index,
                status=segment.status,
                duration_seconds=segment.duration_seconds,
                frame_count=segment.frame_count,
            )
            for segment in job.segments
        ],
    )
