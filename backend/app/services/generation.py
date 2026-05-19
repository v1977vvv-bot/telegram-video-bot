from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.formats import AVAILABLE_GENERATION_FORMATS, is_available_format
from backend.app.models.generation_job import GenerationJob
from backend.app.models.generation_segment import GenerationSegment
from backend.app.models.uploaded_file import UploadedFile
from backend.app.models.user import User
from backend.app.repositories.generation_jobs import GenerationJobRepository
from backend.app.repositories.users import UserRepository
from backend.app.services.audio import AudioService, SegmentPlan
from backend.app.services.balances import BalanceService
from backend.app.services.business_balance import BusinessBalanceService
from backend.app.services.pricing import PricingService
from backend.app.services.storage import StorageServiceFactory
from shared.app.config import Settings, get_settings
from shared.app.enums import BillingAccountType, FileType, JobStatus, SegmentStatus
from shared.app.exceptions import AppError
from shared.app.job_names import build_job_display_name
from shared.app.storage import validated_extension


@dataclass(frozen=True, slots=True)
class FilePayload:
    original_filename: str
    content: bytes
    mime_type: str


@dataclass(frozen=True, slots=True)
class GenerationFormatDto:
    label: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class DraftSummary:
    job_id: UUID
    display_name: str
    status: str
    audio_duration_seconds: Decimal
    segments_count: int
    fps: int
    price_usd: Decimal
    available_formats: list[GenerationFormatDto]


@dataclass(frozen=True, slots=True)
class FormatSummary:
    job_id: UUID
    display_name: str
    status: str
    width: int
    height: int
    fps: int
    audio_duration_seconds: Decimal
    segments_count: int
    price_usd: Decimal


@dataclass(frozen=True, slots=True)
class ConfirmationSummary:
    job_id: UUID
    display_name: str
    status: str
    price_usd: Decimal
    message: str
    billing_account_type: str = BillingAccountType.PERSONAL.value
    business_account_id: UUID | None = None
    business_account_name: str | None = None


class GenerationService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        user_repository: UserRepository | None = None,
        job_repository: GenerationJobRepository | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._user_repository = user_repository or UserRepository()
        self._job_repository = job_repository or GenerationJobRepository()
        self._audio_service = AudioService(self._settings)
        self._pricing_service = PricingService(self._settings)

    async def create_draft(
        self,
        *,
        telegram_id: int,
        image: FilePayload,
        audio: FilePayload,
    ) -> DraftSummary:
        self._validate_size(image.content, self._settings.max_image_size_mb, "image_too_large")
        self._validate_size(audio.content, self._settings.max_audio_size_mb, "audio_too_large")

        async with self._session.begin():
            await self._get_active_user(telegram_id)

        temp_audio_path = self._write_probe_temp_file(audio)
        try:
            duration = await self._audio_service.get_duration_seconds(temp_audio_path)
            segment_plans = self._audio_service.build_segments(
                duration,
                self._settings.generation_max_segment_seconds,
                self._settings.generation_fps,
            )
            price = self._pricing_service.calculate_job_price(duration)

            async with self._session.begin():
                user = await self._get_active_user(telegram_id)
                storage = StorageServiceFactory(self._session, self._settings).create()
                image_file = await storage.save_bytes(
                    user_id=user.id,
                    file_type=FileType.IMAGE,
                    original_filename=image.original_filename,
                    content=image.content,
                    mime_type=image.mime_type,
                )
                audio_file = await storage.save_bytes(
                    user_id=user.id,
                    file_type=FileType.AUDIO,
                    original_filename=audio.original_filename,
                    content=audio.content,
                    mime_type=audio.mime_type,
                )

                job = GenerationJob(
                    user_id=user.id,
                    status=JobStatus.DRAFT.value,
                    source_image_file_id=image_file.id,
                    source_audio_file_id=audio_file.id,
                    fps=self._settings.generation_fps,
                    width=480,
                    height=480,
                    audio_duration_seconds=duration,
                    segments_count=len(segment_plans),
                    price_usd=price,
                )
                self._session.add(job)
                await self._session.flush()

                for plan in segment_plans:
                    self._session.add(
                        self._build_segment(
                            job_id=job.id,
                            plan=plan,
                            image_file_id=image_file.id,
                            audio_file_id=audio_file.id,
                        )
                    )

                return DraftSummary(
                    job_id=job.id,
                    display_name=build_job_display_name(
                        image_filename=image_file.original_filename,
                        audio_filename=audio_file.original_filename,
                        created_at=datetime.now(UTC),
                    ),
                    status=job.status,
                    audio_duration_seconds=duration,
                    segments_count=len(segment_plans),
                    fps=job.fps,
                    price_usd=price,
                    available_formats=[
                        GenerationFormatDto(item.label, item.width, item.height)
                        for item in AVAILABLE_GENERATION_FORMATS
                    ],
                )
        finally:
            temp_audio_path.unlink(missing_ok=True)

    def _write_probe_temp_file(self, audio: FilePayload) -> Path:
        extension = validated_extension(
            file_type=FileType.AUDIO,
            original_filename=audio.original_filename,
            mime_type=audio.mime_type,
        )
        temp_dir = Path(self._settings.local_storage_dir) / "temp" / "probe"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = temp_dir / f"{uuid.uuid4().hex}.{extension}"
        temp_path.write_bytes(audio.content)
        return temp_path

    async def update_draft_format(
        self,
        *,
        job_id: UUID,
        telegram_id: int,
        width: int,
        height: int,
    ) -> FormatSummary:
        if not is_available_format(width, height):
            raise AppError("Unsupported generation format", code="unsupported_generation_format")

        async with self._session.begin():
            user = await self._get_active_user(telegram_id)
            job = await self._get_owned_job(job_id, user.id, for_update=True)
            if job.status != JobStatus.DRAFT.value:
                raise AppError("Only draft jobs can be edited", code="job_not_editable")
            job.width = width
            job.height = height
            return await self._format_summary(job)

    async def confirm_draft(self, *, job_id: UUID, telegram_id: int) -> ConfirmationSummary:
        async with self._session.begin():
            user = await self._get_active_user(telegram_id)
            job = await self._get_owned_job(job_id, user.id, for_update=True)
            if job.status != JobStatus.DRAFT.value:
                raise AppError("Only draft jobs can be confirmed", code="job_not_confirmable")
            if job.price_usd is None:
                raise AppError("Job price is missing", code="job_price_missing")
            self._validate_audio_limit(job)

            business_selection = await BusinessBalanceService(
                self._session
            ).get_active_business_account_for_user(user.id)
            business_account_name: str | None = None
            if business_selection is not None:
                business_account = business_selection.account
                mutation = await BusinessBalanceService(
                    self._session
                ).reserve_business_balance_in_transaction(
                    business_account_id=business_account.id,
                    user_id=user.id,
                    job_id=job.id,
                    amount_usd=job.price_usd,
                )
                job.billing_account_type = BillingAccountType.BUSINESS.value
                job.business_account_id = business_account.id
                if mutation.transaction is not None:
                    job.business_hold_transaction_id = mutation.transaction.id
                business_account_name = business_account.name
            else:
                await BalanceService(self._session).freeze_balance_in_transaction(
                    user_id=user.id,
                    amount_usd=job.price_usd,
                    related_job_id=job.id,
                )
                job.billing_account_type = BillingAccountType.PERSONAL.value
                job.business_account_id = None
                job.business_hold_transaction_id = None
            now = datetime.now(UTC)
            job.status = JobStatus.QUEUED.value
            job.confirmed_at = now
            job.queued_at = now
            for segment in job.segments:
                segment.status = SegmentStatus.QUEUED.value

            return ConfirmationSummary(
                job_id=job.id,
                display_name=await self.get_job_display_name(job),
                status=job.status,
                price_usd=job.price_usd,
                message="Задача поставлена в очередь",
                billing_account_type=job.billing_account_type,
                business_account_id=job.business_account_id,
                business_account_name=business_account_name,
            )

    async def cancel_job(self, *, job_id: UUID, telegram_id: int) -> ConfirmationSummary:
        async with self._session.begin():
            user = await self._get_active_user(telegram_id)
            job = await self._get_owned_job(job_id, user.id, for_update=True)
            if job.status == JobStatus.GENERATING.value:
                raise AppError(
                    "Отмена во время генерации пока недоступна", code="cancel_unavailable"
                )
            if job.status not in {JobStatus.DRAFT.value, JobStatus.QUEUED.value}:
                raise AppError("Job cannot be cancelled", code="job_not_cancellable")

            if job.status == JobStatus.QUEUED.value and job.price_usd is not None:
                if (
                    job.billing_account_type == BillingAccountType.BUSINESS.value
                    and job.business_account_id is not None
                ):
                    await BusinessBalanceService(
                        self._session
                    ).refund_business_frozen_balance_in_transaction(
                        business_account_id=job.business_account_id,
                        job_id=job.id,
                        amount_usd=job.price_usd,
                        user_id=user.id,
                        reason="Queued generation job cancelled",
                    )
                else:
                    await BalanceService(self._session).refund_frozen_balance_in_transaction(
                        user_id=user.id,
                        amount_usd=job.price_usd,
                        related_job_id=job.id,
                        reason="Queued generation job cancelled",
                    )

            now = datetime.now(UTC)
            job.status = JobStatus.CANCELLED.value
            job.cancelled_at = now
            job.next_retry_at = None
            job.waiting_for_gpu_since = None
            job.waiting_for_pod_since = None
            for segment in job.segments:
                segment.status = SegmentStatus.CANCELLED.value

            return ConfirmationSummary(
                job_id=job.id,
                display_name=await self.get_job_display_name(job),
                status=job.status,
                price_usd=job.price_usd or Decimal("0.0000"),
                message="Генерация отменена",
                billing_account_type=job.billing_account_type,
                business_account_id=job.business_account_id,
                business_account_name=None,
            )

    async def get_job_detail(self, *, job_id: UUID, telegram_id: int) -> GenerationJob:
        user = await self._get_active_user(telegram_id)
        return await self._get_owned_job(job_id, user.id, with_segments=True)

    async def get_job_display_name(self, job: GenerationJob) -> str:
        image_file = await self._session.get(UploadedFile, job.source_image_file_id)
        audio_file = await self._session.get(UploadedFile, job.source_audio_file_id)
        return build_job_display_name(
            image_filename=image_file.original_filename if image_file else None,
            audio_filename=audio_file.original_filename if audio_file else None,
            created_at=job.created_at,
        )

    async def _get_active_user(self, telegram_id: int) -> User:
        user = await self._user_repository.get_by_telegram_id(self._session, telegram_id)
        if user is None:
            raise AppError("User not found", code="user_not_found", status_code=404)
        if user.is_banned:
            raise AppError("User access is restricted", code="user_banned", status_code=403)
        return user

    async def _get_owned_job(
        self,
        job_id: UUID,
        user_id: UUID,
        *,
        with_segments: bool = True,
        for_update: bool = False,
    ) -> GenerationJob:
        job = await self._job_repository.get_by_id(
            self._session,
            job_id,
            with_segments=with_segments,
            for_update=for_update,
        )
        if job is None or job.user_id != user_id:
            raise AppError("Generation job not found", code="job_not_found", status_code=404)
        return job

    def _build_segment(
        self,
        *,
        job_id: UUID,
        plan: SegmentPlan,
        image_file_id: UUID,
        audio_file_id: UUID,
    ) -> GenerationSegment:
        return GenerationSegment(
            job_id=job_id,
            segment_index=plan.segment_index,
            status=SegmentStatus.PENDING.value,
            audio_start_seconds=plan.start_seconds,
            audio_end_seconds=plan.end_seconds,
            duration_seconds=plan.duration_seconds,
            frame_count=plan.frame_count,
            price_usd=self._pricing_service.calculate_segment_price(plan.duration_seconds),
            input_audio_file_id=audio_file_id,
            input_image_file_id=image_file_id,
        )

    async def _format_summary(self, job: GenerationJob) -> FormatSummary:
        if job.audio_duration_seconds is None or job.price_usd is None:
            raise AppError("Draft is incomplete", code="incomplete_draft")
        return FormatSummary(
            job_id=job.id,
            display_name=await self.get_job_display_name(job),
            status=job.status,
            width=job.width,
            height=job.height,
            fps=job.fps,
            audio_duration_seconds=job.audio_duration_seconds,
            segments_count=job.segments_count,
            price_usd=job.price_usd,
        )

    def _validate_size(self, content: bytes, max_mb: int, code: str) -> None:
        max_bytes = max_mb * 1024 * 1024
        if len(content) > max_bytes:
            raise AppError(
                f"Файл слишком большой. Максимум {max_mb} MB.",
                code=code,
                status_code=400,
            )

    def _validate_audio_limit(self, job: GenerationJob) -> None:
        if job.audio_duration_seconds is None:
            raise AppError("Job audio duration is missing", code="job_audio_duration_missing")

        max_seconds = Decimal(self._settings.generation_max_audio_seconds)
        if job.audio_duration_seconds <= max_seconds:
            return

        raise AppError(
            f"Аудио слишком длинное. Максимум {self._settings.generation_max_audio_seconds} сек.",
            code="audio_too_long",
            status_code=400,
        )
