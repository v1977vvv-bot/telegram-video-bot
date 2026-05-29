from __future__ import annotations

from io import BytesIO
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
    BatchUploadSessionRequest,
    BatchUploadSessionResponse,
    BatchWebConfirmRequest,
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
from backend.app.services.batch_upload_sessions import (
    BatchUploadSessionService,
    validate_telegram_webapp_init_data,
)
from backend.app.services.generation import FilePayload, GenerationService
from backend.app.workers.celery_client import enqueue_generation_job
from shared.app.config import get_settings
from shared.app.database import get_session
from shared.app.exceptions import AppError

router = APIRouter(prefix="/generation", tags=["generation"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
TelegramIdForm = Annotated[int, Form(gt=0)]
QualityProfileForm = Annotated[str | None, Form()]
UploadFileDep = Annotated[UploadFile, File()]
TelegramIdQuery = Annotated[int, Query(gt=0)]
TokenForm = Annotated[str, Form()]
InitDataForm = Annotated[str | None, Form()]


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
    async with session.begin():
        user = await _get_active_user(session, telegram_id)
    summary = await BatchGenerationService(session).create_batch_draft(
        user_id=user.id,
        filename=archive.filename or "batch.zip",
        content=await archive.read(),
        quality_profile=quality_profile or "480p",
    )
    return _batch_response(summary, BatchDraftResponse)


@router.post("/batches/upload-session", response_model=BatchUploadSessionResponse)
async def create_generation_batch_upload_session(
    payload: BatchUploadSessionRequest,
    session: SessionDep,
) -> BatchUploadSessionResponse:
    async with session.begin():
        user = await _get_active_user(session, payload.telegram_id)
        created = await BatchUploadSessionService(session).create_session(
            user_id=user.id,
            telegram_id=user.telegram_id,
        )
    return BatchUploadSessionResponse(web_app_url=created.web_app_url)


@router.post("/batches/web-draft", response_model=BatchDraftResponse)
async def create_generation_batch_web_draft(
    session: SessionDep,
    token: TokenForm,
    archive: UploadFileDep,
    quality_profile: QualityProfileForm = None,
    telegram_init_data: InitDataForm = None,
) -> BatchDraftResponse:
    upload_session_service = BatchUploadSessionService(session)
    async with session.begin():
        upload_session = await upload_session_service.validate_session_token(token)
        upload_user_id = upload_session.user_id
        upload_telegram_id = upload_session.telegram_id
    _validate_optional_init_data(telegram_init_data, upload_telegram_id)
    _validate_zip_filename(archive.filename or "batch.zip")
    content = await _read_upload_with_limit(
        archive,
        max_bytes=get_settings().batch_web_upload_max_mb * 1024 * 1024,
    )
    summary = await BatchGenerationService(session).create_batch_draft(
        user_id=upload_user_id,
        filename=archive.filename or "batch.zip",
        content=content,
        quality_profile=quality_profile or "480p",
    )
    if summary.batch_id is not None:
        async with session.begin():
            upload_session = await upload_session_service.validate_session_token(token)
            if upload_session.telegram_id != upload_telegram_id:
                raise AppError("Upload session mismatch", code="batch_session_mismatch")
            upload_session_service.validate_record(upload_session)
            upload_session_service.link_batch(
                upload_session,
                summary.batch_id,
                summary.quality_profile,
            )
    return _batch_response(summary, BatchDraftResponse)


@router.post("/batches/web-confirm", response_model=BatchConfirmResponse)
async def confirm_generation_batch_web(
    payload: BatchWebConfirmRequest,
    session: SessionDep,
) -> BatchConfirmResponse:
    upload_session_service = BatchUploadSessionService(session)
    async with session.begin():
        upload_session = await upload_session_service.validate_session_token(payload.token)
        if upload_session.batch_id != payload.batch_id:
            raise AppError("Upload session does not own this batch", code="batch_session_mismatch")
        upload_user_id = upload_session.user_id
    summary = await BatchGenerationService(session).confirm_batch(
        user_id=upload_user_id,
        batch_id=payload.batch_id,
    )
    if summary.batch_id is not None:
        async with session.begin():
            upload_session = await upload_session_service.validate_session_token(payload.token)
            if upload_session.batch_id != summary.batch_id:
                raise AppError(
                    "Upload session does not own this batch",
                    code="batch_session_mismatch",
                )
            upload_session_service.validate_record(upload_session)
            upload_session_service.mark_used(upload_session, summary.batch_id)
    return _batch_response(summary, BatchConfirmResponse)


@router.post("/batches/{batch_id}/confirm", response_model=BatchConfirmResponse)
async def confirm_generation_batch(
    batch_id: UUID,
    payload: TelegramUserJobRequest,
    session: SessionDep,
) -> BatchConfirmResponse:
    async with session.begin():
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


async def _read_upload_with_limit(upload: UploadFile, *, max_bytes: int) -> bytes:
    buffer = BytesIO()
    total = 0
    while chunk := await upload.read(1024 * 1024):
        total += len(chunk)
        if total > max_bytes:
            max_mb = max_bytes // (1024 * 1024)
            raise AppError(
                f"Archive is too large. Maximum size: {max_mb} MB.",
                code="batch_archive_too_large",
                status_code=413,
            )
        buffer.write(chunk)
    return buffer.getvalue()


def _validate_zip_filename(filename: str) -> None:
    if not filename.casefold().endswith(".zip"):
        raise AppError(
            "Only .zip archives are supported",
            code="unsupported_archive_type",
            status_code=400,
        )


def _validate_optional_init_data(init_data: str | None, telegram_id: int) -> None:
    if not init_data:
        return
    validated_telegram_id = validate_telegram_webapp_init_data(
        init_data,
        bot_token=get_settings().telegram_bot_token,
    )
    if validated_telegram_id != telegram_id:
        raise AppError(
            "Telegram initData user does not match upload session",
            code="telegram_init_data_user_mismatch",
            status_code=403,
        )
