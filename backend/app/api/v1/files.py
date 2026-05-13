from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.generation_job import GenerationJob
from backend.app.models.uploaded_file import UploadedFile
from backend.app.repositories.users import UserRepository
from backend.app.services.storage import StorageServiceFactory
from shared.app.database import get_session
from shared.app.enums import StorageProvider
from shared.app.exceptions import AppError

router = APIRouter(prefix="/files", tags=["files"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
TelegramIdQuery = Annotated[int, Query(gt=0)]


@router.get("/{file_id}/download", response_model=None)
async def download_file(
    file_id: UUID,
    telegram_id: TelegramIdQuery,
    session: SessionDep,
) -> FileResponse | RedirectResponse:
    user = await UserRepository().get_by_telegram_id(session, telegram_id)
    if user is None:
        raise AppError("User not found", code="user_not_found", status_code=404)

    result = await session.execute(select(UploadedFile).where(UploadedFile.id == file_id))
    uploaded_file = result.scalar_one_or_none()
    if uploaded_file is None:
        raise AppError("File not found", code="file_not_found", status_code=404)

    if uploaded_file.user_id != user.id and not await _is_file_linked_to_user_job(
        session,
        file_id=file_id,
        user_id=user.id,
    ):
        raise AppError("File not found", code="file_not_found", status_code=404)

    storage = StorageServiceFactory(session).create_for_uploaded_file(uploaded_file)
    if uploaded_file.storage_provider == StorageProvider.CLOUDFLARE_R2.value:
        download_url = storage.get_download_url(uploaded_file)
        if download_url is None:
            raise AppError("Download URL is not available", code="download_url_unavailable")
        return RedirectResponse(download_url)

    local_path = storage.get_local_path(uploaded_file)
    if local_path is None or not local_path.exists():
        raise AppError("File not found", code="file_not_found", status_code=404)
    return FileResponse(
        local_path,
        media_type=uploaded_file.mime_type or "application/octet-stream",
        filename=uploaded_file.original_filename,
    )


async def _is_file_linked_to_user_job(
    session: AsyncSession,
    *,
    file_id: UUID,
    user_id: UUID,
) -> bool:
    result = await session.execute(
        select(GenerationJob.id)
        .where(
            GenerationJob.user_id == user_id,
            or_(
                GenerationJob.source_image_file_id == file_id,
                GenerationJob.source_audio_file_id == file_id,
                GenerationJob.output_file_id == file_id,
            ),
        )
        .limit(1)
    )
    return result.first() is not None
