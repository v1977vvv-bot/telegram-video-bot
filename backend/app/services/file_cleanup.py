from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.generation_job import GenerationJob
from backend.app.models.uploaded_file import UploadedFile
from backend.app.services.storage import StorageServiceFactory
from shared.app.config import Settings, get_settings
from shared.app.enums import JobStatus

ACTIVE_JOB_STATUSES = {
    JobStatus.DRAFT.value,
    JobStatus.QUEUED.value,
    JobStatus.POD_STARTING.value,
    JobStatus.UPLOADING_INPUTS.value,
    JobStatus.GENERATING.value,
    JobStatus.STITCHING.value,
    JobStatus.UPLOADING_RESULT.value,
}


class FileCleanupService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    async def cleanup_expired_files(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(hours=self._settings.result_retention_hours)
        active_file_ids = await self._get_active_file_ids()
        statement = select(UploadedFile).where(UploadedFile.created_at < cutoff)
        if active_file_ids:
            statement = statement.where(UploadedFile.id.not_in(active_file_ids))

        result = await self._session.execute(statement)
        deleted_count = 0
        for uploaded_file in result.scalars():
            storage = StorageServiceFactory(
                self._session,
                self._settings,
            ).create_for_uploaded_file(uploaded_file)
            await storage.delete(uploaded_file)
            await self._session.delete(uploaded_file)
            deleted_count += 1
        return deleted_count

    async def _get_active_file_ids(self) -> set[UUID]:
        result = await self._session.execute(
            select(
                GenerationJob.source_image_file_id,
                GenerationJob.source_audio_file_id,
                GenerationJob.output_file_id,
            ).where(GenerationJob.status.in_(ACTIVE_JOB_STATUSES))
        )
        file_ids: set[UUID] = set()
        for source_image_file_id, source_audio_file_id, output_file_id in result.all():
            for file_id in (source_image_file_id, source_audio_file_id, output_file_id):
                if file_id is not None:
                    file_ids.add(file_id)
        return file_ids
