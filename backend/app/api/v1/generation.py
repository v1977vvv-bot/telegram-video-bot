from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.repositories.users import UserRepository
from backend.app.schemas.batch_generation import (
    BatchConfirmResponse,
    BatchDetailResponse,
    BatchDraftErrorResponse,
    BatchDraftItemResponse,
    BatchDraftResponse,
)
from backend.app.schemas.generation import (
    GenerationConfirmResponse,
    GenerationDraftResponse,
    GenerationFormatRequest,
    GenerationFormatResponse,
    GenerationJobDetailResponse,
    GenerationQualityRequest,
    GenerationSegmentDetailResponse,
    TelegramUserJobRequest,
)
from backend.app.schemas.settings import AvailableFormatResponse
from backend.app.services.batch_generation import BatchDraftSummary, BatchGenerationService
from backend.app.services.generation import FilePayload, GenerationService
from backend.app.workers.celery_client import enqueue_generation_job
from shared.app.exceptions import AppError
from shared.app.database import get_session

router = APIRouter(prefix="/generation", tags=["generation"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
TelegramIdForm = Annotated[int, Form(gt=0)]
QualityProfileForm = Annotated[str | None, Form()]
UploadFileDep = Annotated[UploadFile, File()]
TelegramIdQuery = Annotated[int, Query(gt=0)]


@router.post("/drafts", response_model=GenerationDraftResponse)
async def create_generation_draft(
    session: SessionDep,
    telegram_id: TelegramIdForm,
    image: UploadFileDep,
    audio: UploadFileDep,
    quality_profile: QualityProfileForm = None,
) -> GenerationDraftResponse:
    summary = await GenerationService(session).create_draft(
        telegram_id=telegram_id,
        quality_profile=quality_profile or "480p",
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
        display_name=summary.display_name,
        status=summary.status,
        audio_duration_seconds=summary.audio_duration_seconds,
        segments_count=summary.segments_count,
        fps=summary.fps,
        quality_profile=summary.quality_profile,
        price_usd=summary.price_usd,
        available_formats=[
            AvailableFormatResponse(label=item.label, width=item.width, height=item.height)
            for item in summary.available_formats
        ],
    )


@router.post("/batches/draft", response_model=BatchDraftResponse)
async def create_generation_batch_draft(
    session: SessionDep,
    telegram_id: TelegramIdForm,
    archive: UploadFileDep,
    quality_profile: QualityProfileForm = None,
) -> BatchDraftResponse:
    user = await _get_active_user(session, telegram_id)
    summary = await BatchGenerationService(session).create_batch_draft(
        user_id=user.id,
        filename=archive.filename or "batch.zip",
        content=await archive.read(),
        quality_profile=quality_profile or "480p",
    )
    return _batch_response(summary, BatchDraftResponse)


@router.post("/batches/{batch_id}/confirm", response_model=BatchConfirmResponse)
async def confirm_generation_batch(
    batch_id: UUID,
    payload: TelegramUserJobRequest,
    session: SessionDep,
) -> BatchConfirmResponse:
    user = await _get_active_user(session, payload.telegram_id)
    summary = await BatchGenerationService(session).confirm_batch(
        user_id=user.id,
        batch_id=batch_id,
    )
    return _batch_response(summary, BatchConfirmResponse)


@router.get("/batches/{batch_id}", response_model=BatchDetailResponse)
async def get_generation_batch(
    batch_id: UUID,
    telegram_id: TelegramIdQuery,
    session: SessionDep,
) -> BatchDetailResponse:
    user = await _get_active_user(session, telegram_id)
    summary = await BatchGenerationService(session).get_batch(
        user_id=user.id,
        batch_id=batch_id,
    )
    return _batch_response(summary, BatchDetailResponse)


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
        display_name=summary.display_name,
        status=summary.status,
        width=summary.width,
        height=summary.height,
        fps=summary.fps,
        quality_profile=summary.quality_profile,
        audio_duration_seconds=summary.audio_duration_seconds,
        segments_count=summary.segments_count,
        price_usd=summary.price_usd,
    )


@router.patch("/drafts/{job_id}/quality", response_model=GenerationFormatResponse)
async def update_generation_quality(
    job_id: UUID,
    payload: GenerationQualityRequest,
    session: SessionDep,
) -> GenerationFormatResponse:
    summary = await GenerationService(session).update_draft_quality(
        job_id=job_id,
        telegram_id=payload.telegram_id,
        quality_profile=payload.quality_profile,
    )
    return GenerationFormatResponse(
        job_id=summary.job_id,
        display_name=summary.display_name,
        status=summary.status,
        width=summary.width,
        height=summary.height,
        fps=summary.fps,
        quality_profile=summary.quality_profile,
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
        display_name=summary.display_name,
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
        display_name=summary.display_name,
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
    display_name = await GenerationService(session).get_job_display_name(job)
    return GenerationJobDetailResponse(
        job_id=job.id,
        display_name=display_name,
        status=job.status,
        width=job.width,
        height=job.height,
        fps=job.fps,
        quality_profile=job.quality_profile,
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


async def _get_active_user(session: AsyncSession, telegram_id: int):
    user = await UserRepository().get_by_telegram_id(session, telegram_id)
    if user is None:
        raise AppError("User not found", code="user_not_found", status_code=404)
    if user.is_banned:
        raise AppError("User access is restricted", code="user_banned", status_code=403)
    return user


def _batch_response(
    summary: BatchDraftSummary,
    response_type: type[BatchDraftResponse],
) -> BatchDraftResponse:
    return response_type(
        batch_id=summary.batch_id,
        status=summary.status,
        quality_profile=summary.quality_profile,
        total_jobs=summary.total_jobs,
        total_duration_seconds=summary.total_duration_seconds,
        total_price_usd=summary.total_price_usd,
        items=[
            BatchDraftItemResponse(
                item_id=item.item_id,
                index=item.index,
                basename=item.basename,
                image_filename=item.image_filename,
                audio_filename=item.audio_filename,
                source_image_file_id=item.source_image_file_id,
                source_audio_file_id=item.source_audio_file_id,
                audio_duration_seconds=item.audio_duration_seconds,
                price_usd=item.price_usd,
                status=item.status,
                generation_job_id=item.generation_job_id,
            )
            for item in summary.items
        ],
        errors=[
            BatchDraftErrorResponse(
                code=error.code,
                message=error.message,
                filename=error.filename,
            )
            for error in summary.errors
        ],
        job_ids=summary.job_ids,
    )
