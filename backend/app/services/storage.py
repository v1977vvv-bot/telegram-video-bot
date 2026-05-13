from __future__ import annotations

from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.uploaded_file import UploadedFile
from backend.app.services.local_storage import LocalStorageService
from backend.app.services.r2_storage import R2StorageService
from shared.app.config import Settings, get_settings
from shared.app.enums import FileType, StorageProvider
from shared.app.exceptions import AppError


class StorageService(Protocol):
    async def save_bytes(
        self,
        *,
        user_id: UUID | None,
        file_type: FileType,
        original_filename: str | None,
        content: bytes,
        mime_type: str | None,
    ) -> UploadedFile: ...

    async def save_file(
        self,
        *,
        user_id: UUID | None,
        file_type: FileType,
        original_filename: str | None,
        local_path: Path,
        mime_type: str | None,
    ) -> UploadedFile: ...

    def get_download_url(
        self,
        uploaded_file: UploadedFile,
        expires_in: int | None = None,
    ) -> str | None: ...

    async def delete(self, uploaded_file: UploadedFile) -> None: ...

    async def exists(self, uploaded_file: UploadedFile) -> bool: ...

    def get_local_path(self, uploaded_file: UploadedFile) -> Path | None: ...


class StorageServiceFactory:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def create(self) -> StorageService:
        return self.create_for_provider(self._settings.storage_provider)

    def create_for_uploaded_file(self, uploaded_file: UploadedFile) -> StorageService:
        return self.create_for_provider(uploaded_file.storage_provider)

    def create_for_provider(self, provider: str) -> StorageService:
        provider = provider.strip().lower()
        if provider == StorageProvider.LOCAL.value:
            return LocalStorageService(self._session, self._settings)
        if provider == StorageProvider.CLOUDFLARE_R2.value:
            return R2StorageService(self._session, self._settings)
        raise AppError("Unsupported storage provider", code="unsupported_storage_provider")
