from __future__ import annotations

from pathlib import Path
from shutil import copyfile
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.uploaded_file import UploadedFile
from shared.app.config import Settings, get_settings
from shared.app.enums import FileType, StorageProvider
from shared.app.storage import build_storage_key, safe_local_path, validated_extension


class LocalStorageService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._root = Path(self._settings.local_storage_dir)

    def ensure_storage_dirs(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    async def save_bytes(
        self,
        *,
        user_id: UUID | None,
        file_type: FileType,
        original_filename: str | None,
        content: bytes,
        mime_type: str | None,
    ) -> UploadedFile:
        extension = self._validated_extension(
            file_type=file_type,
            original_filename=original_filename,
            mime_type=mime_type,
        )
        relative_key = self._build_storage_key(user_id, file_type, extension)
        absolute_path = self.get_path(relative_key)
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_bytes(content)

        uploaded_file = UploadedFile(
            user_id=user_id,
            file_type=file_type.value,
            original_filename=original_filename,
            storage_provider=StorageProvider.LOCAL.value,
            storage_key=relative_key,
            mime_type=mime_type,
            size_bytes=len(content),
        )
        self._session.add(uploaded_file)
        await self._session.flush()
        return uploaded_file

    async def save_file(
        self,
        *,
        user_id: UUID | None,
        file_type: FileType,
        original_filename: str | None,
        local_path: Path,
        mime_type: str | None,
    ) -> UploadedFile:
        extension = self._validated_extension(
            file_type=file_type,
            original_filename=original_filename,
            mime_type=mime_type,
        )
        relative_key = self._build_storage_key(user_id, file_type, extension)
        absolute_path = self.get_path(relative_key)
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        copyfile(local_path, absolute_path)

        uploaded_file = UploadedFile(
            user_id=user_id,
            file_type=file_type.value,
            original_filename=original_filename,
            storage_provider=StorageProvider.LOCAL.value,
            storage_key=relative_key,
            mime_type=mime_type,
            size_bytes=absolute_path.stat().st_size,
        )
        self._session.add(uploaded_file)
        await self._session.flush()
        return uploaded_file

    def get_download_url(
        self,
        uploaded_file: UploadedFile,
        expires_in: int | None = None,
    ) -> str | None:
        return None

    def get_path(self, storage_key: str) -> Path:
        return safe_local_path(self._root, storage_key)

    def get_local_path(self, uploaded_file: UploadedFile) -> Path | None:
        return self.get_path(uploaded_file.storage_key)

    async def delete(self, uploaded_file: UploadedFile) -> None:
        path = self.get_path(uploaded_file.storage_key)
        if path.exists():
            path.unlink()

    async def exists(self, uploaded_file: UploadedFile) -> bool:
        return self.get_path(uploaded_file.storage_key).exists()

    def _build_storage_key(self, user_id: UUID | None, file_type: FileType, extension: str) -> str:
        return build_storage_key(user_id, file_type, extension)

    def _validated_extension(
        self,
        *,
        file_type: FileType,
        original_filename: str | None,
        mime_type: str | None,
    ) -> str:
        return validated_extension(
            file_type=file_type,
            original_filename=original_filename,
            mime_type=mime_type,
        )
