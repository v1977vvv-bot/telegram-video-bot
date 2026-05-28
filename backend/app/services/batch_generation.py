from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Protocol
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.core.formats import default_format_for_quality
from backend.app.models.generation_batch import GenerationBatch
from backend.app.models.generation_batch_item import GenerationBatchItem
from backend.app.models.generation_job import GenerationJob
from backend.app.models.generation_segment import GenerationSegment
from backend.app.models.uploaded_file import UploadedFile
from backend.app.services.balances import BalanceService
from backend.app.services.audio import AudioService, SegmentPlan
from backend.app.services.batch_archive import (
    BatchArchiveError,
    BatchArchiveResult,
    parse_generation_batch_archive,
)
from backend.app.services.business_balance import BusinessBalanceService
from backend.app.services.pricing import PricingService
from shared.app.config import Settings, get_settings
from shared.app.enums import (
    BillingAccountType,
    FileType,
    GenerationBatchStatus,
    JobStatus,
    SegmentStatus,
)
from shared.app.exceptions import AppError


class AudioDurationService(Protocol):
    async def get_duration_seconds(self, path: Path) -> Decimal: ...

    def build_segments(
        self,
        duration_seconds: Decimal,
        max_segment_seconds: int,
        fps: int,
    ) -> list[SegmentPlan]: ...


class BatchStorageService(Protocol):
    async def save_bytes(
        self,
        *,
        user_id: UUID | None,
        file_type: FileType,
        original_filename: str | None,
        content: bytes,
        mime_type: str | None,
    ) -> UploadedFile: ...


class BatchBillingReserver(Protocol):
    async def reserve_job(
        self,
        *,
        user_id: UUID,
        job: GenerationJob,
        amount_usd: Decimal,
    ) -> None: ...


@dataclass(frozen=True)
class BatchDraftItemSummary:
    item_id: UUID | None
    index: int
    basename: str
    image_filename: str
    audio_filename: str
    source_image_file_id: UUID | None
    source_audio_file_id: UUID | None
    audio_duration_seconds: Decimal
    price_usd: Decimal
    status: str
    generation_job_id: UUID | None = None


@dataclass(frozen=True)
class BatchDraftSummary:
    batch_id: UUID | None
    status: str | None
    quality_profile: str
    total_jobs: int
    total_duration_seconds: Decimal | None
    total_price_usd: Decimal | None
    items: list[BatchDraftItemSummary]
    errors: list[BatchArchiveError]
    job_ids: list[UUID] | None = None


@dataclass(frozen=True)
class _PreparedBatchItem:
    summary: BatchDraftItemSummary
    image_content: bytes
    audio_content: bytes


class BatchGenerationService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        audio_service: AudioDurationService | None = None,
        pricing_service: PricingService | None = None,
        storage_service: BatchStorageService | None = None,
        billing_reserver: BatchBillingReserver | None = None,
        enqueue_generation_job: Callable[[str], object] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._audio_service = audio_service or AudioService(self._settings)
        self._pricing_service = pricing_service or PricingService(self._settings)
        self._storage_service = storage_service
        self._billing_reserver = billing_reserver
        self._enqueue_generation_job = enqueue_generation_job

    async def create_batch_draft(
        self,
        *,
        user_id: UUID,
        filename: str,
        content: bytes,
        quality_profile: str = "480p",
    ) -> BatchDraftSummary:
        quality = self._pricing_service.normalize_quality_profile(quality_profile)
        archive_result = parse_generation_batch_archive(filename, content)
        if archive_result.errors:
            return BatchDraftSummary(
                batch_id=None,
                status=None,
                quality_profile=quality,
                total_jobs=0,
                total_duration_seconds=None,
                total_price_usd=None,
                items=[],
                errors=archive_result.errors,
                job_ids=[],
            )

        prepared_items, errors = await self._prepare_items(content, archive_result, quality)
        if errors:
            return BatchDraftSummary(
                batch_id=None,
                status=None,
                quality_profile=quality,
                total_jobs=0,
                total_duration_seconds=None,
                total_price_usd=None,
                items=[],
                errors=errors,
                job_ids=[],
            )

        items = [item.summary for item in prepared_items]
        total_duration = sum((item.audio_duration_seconds for item in items), Decimal("0"))
        total_price = sum((item.price_usd for item in items), Decimal("0"))
        batch = GenerationBatch(
            user_id=user_id,
            status=GenerationBatchStatus.DRAFT.value,
            quality_profile=quality,
            total_jobs=len(items),
            completed_jobs=0,
            failed_jobs=0,
            total_duration_seconds=total_duration,
            total_price_usd=total_price,
        )
        async with self._session.begin():
            self._session.add(batch)
            await self._session.flush()
            storage = self._get_storage_service()
            for prepared in prepared_items:
                image_file = await storage.save_bytes(
                    user_id=user_id,
                    file_type=FileType.IMAGE,
                    original_filename=prepared.summary.image_filename,
                    content=prepared.image_content,
                    mime_type=_mime_type_for_filename(prepared.summary.image_filename),
                )
                audio_file = await storage.save_bytes(
                    user_id=user_id,
                    file_type=FileType.AUDIO,
                    original_filename=prepared.summary.audio_filename,
                    content=prepared.audio_content,
                    mime_type=_mime_type_for_filename(prepared.summary.audio_filename),
                )
                item = GenerationBatchItem(
                    batch_id=batch.id,
                    batch_index=prepared.summary.index,
                    basename=prepared.summary.basename,
                    image_filename=prepared.summary.image_filename,
                    audio_filename=prepared.summary.audio_filename,
                    source_image_file_id=image_file.id,
                    source_audio_file_id=audio_file.id,
                    duration_seconds=prepared.summary.audio_duration_seconds,
                    price_usd=prepared.summary.price_usd,
                    status=JobStatus.DRAFT.value,
                )
                self._session.add(item)
                await self._session.flush()
                items[item.batch_index - 1] = BatchDraftItemSummary(
                    item_id=item.id,
                    index=item.batch_index,
                    basename=item.basename,
                    image_filename=item.image_filename,
                    audio_filename=item.audio_filename,
                    source_image_file_id=item.source_image_file_id,
                    source_audio_file_id=item.source_audio_file_id,
                    audio_duration_seconds=item.duration_seconds,
                    price_usd=item.price_usd,
                    status=item.status,
                    generation_job_id=item.generation_job_id,
                )

        return BatchDraftSummary(
            batch_id=batch.id,
            status=batch.status,
            quality_profile=batch.quality_profile,
            total_jobs=batch.total_jobs,
            total_duration_seconds=batch.total_duration_seconds,
            total_price_usd=batch.total_price_usd,
            items=items,
            errors=[],
            job_ids=[],
        )

    async def confirm_batch(self, *, user_id: UUID, batch_id: UUID) -> BatchDraftSummary:
        job_ids: list[UUID] = []
        async with self._session.begin():
            batch = await self._get_batch(batch_id)
            if batch.user_id != user_id:
                raise AppError(
                    "Generation batch not found",
                    code="batch_not_found",
                    status_code=404,
                )
            if batch.status != GenerationBatchStatus.DRAFT.value:
                raise AppError(
                    "Only draft batches can be confirmed",
                    code="batch_not_confirmable",
                    status_code=400,
                )

            now = datetime.now(UTC)
            business_selection = None
            if self._billing_reserver is None:
                business_selection = await BusinessBalanceService(
                    self._session
                ).get_active_business_account_for_user(user_id)

            default_format = default_format_for_quality(batch.quality_profile)
            for item in sorted(batch.items, key=lambda value: value.batch_index):
                segment_plans = self._audio_service.build_segments(
                    item.duration_seconds,
                    self._settings.generation_max_segment_seconds,
                    self._settings.generation_fps,
                )
                job = GenerationJob(
                    user_id=user_id,
                    status=JobStatus.QUEUED.value,
                    source_image_file_id=item.source_image_file_id,
                    source_audio_file_id=item.source_audio_file_id,
                    fps=self._settings.generation_fps,
                    width=default_format.width,
                    height=default_format.height,
                    quality_profile=batch.quality_profile,
                    audio_duration_seconds=item.duration_seconds,
                    segments_count=len(segment_plans),
                    price_usd=item.price_usd,
                    batch_id=batch.id,
                    batch_index=item.batch_index,
                    confirmed_at=now,
                    queued_at=now,
                )
                self._session.add(job)
                await self._session.flush()
                await self._reserve_job_balance(
                    user_id=user_id,
                    job=job,
                    amount_usd=item.price_usd,
                    business_selection=business_selection,
                )
                for plan in segment_plans:
                    self._session.add(
                        GenerationSegment(
                            job_id=job.id,
                            segment_index=plan.segment_index,
                            status=SegmentStatus.QUEUED.value,
                            audio_start_seconds=plan.start_seconds,
                            audio_end_seconds=plan.end_seconds,
                            duration_seconds=plan.duration_seconds,
                            frame_count=plan.frame_count,
                            price_usd=self._pricing_service.calculate_segment_price(
                                plan.duration_seconds,
                                quality_profile=batch.quality_profile,
                            ),
                            input_audio_file_id=item.source_audio_file_id,
                            input_image_file_id=item.source_image_file_id,
                        )
                    )
                item.generation_job_id = job.id
                item.status = JobStatus.QUEUED.value
                item.error_message = None
                job_ids.append(job.id)

            batch.status = GenerationBatchStatus.CONFIRMED.value
            batch.confirmed_at = now

        for job_id in job_ids:
            self._enqueue_job(job_id)

        batch = await self._get_batch(batch_id)
        return self._summary_from_batch(batch, errors=[], job_ids=job_ids)

    async def get_batch(self, *, user_id: UUID, batch_id: UUID) -> BatchDraftSummary:
        batch = await self._get_batch(batch_id)
        if batch.user_id != user_id:
            raise AppError("Generation batch not found", code="batch_not_found", status_code=404)
        return self._summary_from_batch(batch, errors=[], job_ids=None)

    async def _prepare_items(
        self,
        content: bytes,
        archive_result: BatchArchiveResult,
        quality_profile: str,
    ) -> tuple[list[_PreparedBatchItem], list[BatchArchiveError]]:
        try:
            with ZipFile(BytesIO(content)) as archive:
                items: list[_PreparedBatchItem] = []
                for pair in archive_result.pairs:
                    image_content = archive.read(pair.image_filename)
                    audio_content = archive.read(pair.audio_filename)
                    try:
                        duration = await self._probe_audio_duration(
                            filename=pair.audio_filename,
                            content=audio_content,
                        )
                    except Exception as exc:
                        return [], [
                            BatchArchiveError(
                                code="audio_probe_failed",
                                message=f"Audio duration could not be detected: {exc}",
                                filename=pair.audio_filename,
                            )
                        ]
                    price = self._pricing_service.calculate_job_price(
                        duration,
                        quality_profile=quality_profile,
                    )
                    items.append(
                        _PreparedBatchItem(
                            summary=BatchDraftItemSummary(
                                item_id=None,
                                index=pair.index,
                                basename=pair.basename,
                                image_filename=pair.image_filename,
                                audio_filename=pair.audio_filename,
                                source_image_file_id=None,
                                source_audio_file_id=None,
                                audio_duration_seconds=duration,
                                price_usd=price,
                                status=JobStatus.DRAFT.value,
                                generation_job_id=None,
                            ),
                            image_content=image_content,
                            audio_content=audio_content,
                        )
                    )
        except (BadZipFile, KeyError) as exc:
            return [], [
                BatchArchiveError(
                    code="invalid_zip",
                    message=f"ZIP archive could not be read: {exc}",
                    filename=None,
                )
            ]

        return items, []

    async def _probe_audio_duration(self, *, filename: str, content: bytes) -> Decimal:
        temp_path = self._write_probe_temp_file(filename=filename, content=content)
        try:
            return await self._audio_service.get_duration_seconds(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def _write_probe_temp_file(self, *, filename: str, content: bytes) -> Path:
        extension = Path(filename).suffix.lower()
        temp_dir = Path(self._settings.local_storage_dir) / "temp" / "batch_probe"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = temp_dir / f"{uuid.uuid4().hex}{extension}"
        temp_path.write_bytes(content)
        return temp_path

    def _get_storage_service(self) -> BatchStorageService:
        if self._storage_service is not None:
            return self._storage_service

        from backend.app.services.storage import StorageServiceFactory

        return StorageServiceFactory(self._session, self._settings).create()

    async def _get_batch(self, batch_id: UUID) -> GenerationBatch:
        batch = await self._session.get(
            GenerationBatch,
            batch_id,
            options=[selectinload(GenerationBatch.items)],
        )
        if batch is None:
            raise AppError("Generation batch not found", code="batch_not_found", status_code=404)
        return batch

    async def _reserve_job_balance(
        self,
        *,
        user_id: UUID,
        job: GenerationJob,
        amount_usd: Decimal,
        business_selection: object | None,
    ) -> None:
        if self._billing_reserver is not None:
            await self._billing_reserver.reserve_job(
                user_id=user_id,
                job=job,
                amount_usd=amount_usd,
            )
            return

        if business_selection is not None:
            business_account = business_selection.account
            mutation = await BusinessBalanceService(
                self._session
            ).reserve_business_balance_in_transaction(
                business_account_id=business_account.id,
                user_id=user_id,
                job_id=job.id,
                amount_usd=amount_usd,
            )
            job.billing_account_type = BillingAccountType.BUSINESS.value
            job.business_account_id = business_account.id
            if mutation.transaction is not None:
                job.business_hold_transaction_id = mutation.transaction.id
            return

        await BalanceService(self._session).freeze_balance_in_transaction(
            user_id=user_id,
            amount_usd=amount_usd,
            related_job_id=job.id,
        )
        job.billing_account_type = BillingAccountType.PERSONAL.value

    def _summary_from_batch(
        self,
        batch: GenerationBatch,
        *,
        errors: list[BatchArchiveError],
        job_ids: list[UUID] | None,
    ) -> BatchDraftSummary:
        return BatchDraftSummary(
            batch_id=batch.id,
            status=batch.status,
            quality_profile=batch.quality_profile,
            total_jobs=batch.total_jobs,
            total_duration_seconds=batch.total_duration_seconds,
            total_price_usd=batch.total_price_usd,
            items=[
                BatchDraftItemSummary(
                    item_id=item.id,
                    index=item.batch_index,
                    basename=item.basename,
                    image_filename=item.image_filename,
                    audio_filename=item.audio_filename,
                    source_image_file_id=item.source_image_file_id,
                    source_audio_file_id=item.source_audio_file_id,
                    audio_duration_seconds=item.duration_seconds,
                    price_usd=item.price_usd,
                    status=item.status,
                    generation_job_id=item.generation_job_id,
                )
                for item in sorted(batch.items, key=lambda value: value.batch_index)
            ],
            errors=errors,
            job_ids=job_ids,
        )

    def _enqueue_job(self, job_id: UUID) -> None:
        if self._enqueue_generation_job is not None:
            self._enqueue_generation_job(str(job_id))
            return

        from backend.app.workers.celery_client import enqueue_generation_job

        enqueue_generation_job(str(job_id))


def _mime_type_for_filename(filename: str) -> str | None:
    extension = Path(filename).suffix.casefold()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
    }.get(extension)
