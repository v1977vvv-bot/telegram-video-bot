from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.uploaded_file import UploadedFile
from backend.app.repositories.generation_jobs import GenerationJobRepository
from backend.app.repositories.users import UserRepository
from backend.app.services.storage import StorageServiceFactory
from shared.app.config import Settings, get_settings
from shared.app.enums import StorageProvider
from shared.app.exceptions import AppError


@dataclass(frozen=True, slots=True)
class GenerationHistoryItem:
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


class GenerationHistoryService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        user_repository: UserRepository | None = None,
        generation_repository: GenerationJobRepository | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._user_repository = user_repository or UserRepository()
        self._generation_repository = generation_repository or GenerationJobRepository()

    async def get_latest_by_telegram_id(
        self,
        *,
        telegram_id: int,
        limit: int,
    ) -> list[GenerationHistoryItem]:
        user = await self._user_repository.get_by_telegram_id(self._session, telegram_id)
        if user is None:
            raise AppError("User not found", code="user_not_found", status_code=404)

        safe_limit = min(max(limit, 1), 50)
        jobs = await self._generation_repository.get_latest_for_user(
            self._session,
            user_id=user.id,
            limit=safe_limit,
        )
        output_files = await self._get_output_files(
            [job.output_file_id for job in jobs if job.output_file_id is not None]
        )
        return [
            GenerationHistoryItem(
                id=job.id,
                status=job.status,
                width=job.width,
                height=job.height,
                fps=job.fps,
                audio_duration_seconds=job.audio_duration_seconds,
                segments_count=job.segments_count,
                price_usd=job.price_usd,
                error_message=job.error_message,
                mock_result_message=job.mock_result_message,
                result_file_id=job.output_file_id,
                result_url=self._get_result_url(
                    output_files.get(job.output_file_id),
                    telegram_id=telegram_id,
                ),
                result_url_expires_in_seconds=self._get_result_url_expires_in_seconds(
                    output_files.get(job.output_file_id)
                ),
                created_at=job.created_at,
            )
            for job in jobs
        ]

    async def _get_output_files(self, file_ids: list[UUID]) -> dict[UUID, UploadedFile]:
        if not file_ids:
            return {}

        result = await self._session.execute(
            select(UploadedFile).where(UploadedFile.id.in_(file_ids))
        )
        return {uploaded_file.id: uploaded_file for uploaded_file in result.scalars()}

    def _get_result_url(
        self,
        uploaded_file: UploadedFile | None,
        *,
        telegram_id: int,
    ) -> str | None:
        if uploaded_file is None:
            return None
        if uploaded_file.storage_provider == StorageProvider.LOCAL.value:
            base_url = self._settings.backend_public_url.rstrip("/")
            return f"{base_url}/api/v1/files/{uploaded_file.id}/download?telegram_id={telegram_id}"
        storage = StorageServiceFactory(self._session, self._settings).create_for_uploaded_file(
            uploaded_file
        )
        return storage.get_download_url(uploaded_file)

    def _get_result_url_expires_in_seconds(self, uploaded_file: UploadedFile | None) -> int | None:
        if uploaded_file is None:
            return None
        if uploaded_file.storage_provider != StorageProvider.CLOUDFLARE_R2.value:
            return None
        if self._settings.cloudflare_r2_public_base_url_or_none is not None:
            return None
        return self._settings.cloudflare_r2_presigned_url_ttl_seconds
