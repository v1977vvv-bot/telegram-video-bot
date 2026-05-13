from __future__ import annotations

from pathlib import Path
from shutil import copyfile
from uuid import UUID

import boto3
from sqlalchemy.orm import Session

from backend.app.models.uploaded_file import UploadedFile
from shared.app.config import Settings, get_settings
from shared.app.enums import FileType, StorageProvider
from shared.app.storage import build_storage_key, safe_local_path, validated_extension


class WorkerStorageService:
    """Sync storage boundary for Celery tasks."""

    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    def save_bytes(
        self,
        *,
        user_id: UUID | None,
        file_type: FileType,
        original_filename: str | None,
        content: bytes,
        mime_type: str | None,
    ) -> UploadedFile:
        extension = validated_extension(
            file_type=file_type,
            original_filename=original_filename,
            mime_type=mime_type,
        )
        storage_key = build_storage_key(user_id, file_type, extension)
        provider = self._settings.storage_provider.strip().lower()

        if provider == StorageProvider.LOCAL.value:
            self._save_local(storage_key, content)
        elif provider == StorageProvider.CLOUDFLARE_R2.value:
            self._save_r2(storage_key, content, mime_type)
        else:
            raise RuntimeError(f"Unsupported storage provider: {provider}")

        uploaded_file = UploadedFile(
            user_id=user_id,
            file_type=file_type.value,
            original_filename=original_filename,
            storage_provider=provider,
            storage_key=storage_key,
            mime_type=mime_type,
            size_bytes=len(content),
        )
        self._session.add(uploaded_file)
        self._session.flush()
        return uploaded_file

    def save_file(
        self,
        *,
        user_id: UUID | None,
        file_type: FileType,
        original_filename: str | None,
        local_path: Path,
        mime_type: str | None,
    ) -> UploadedFile:
        extension = validated_extension(
            file_type=file_type,
            original_filename=original_filename,
            mime_type=mime_type,
        )
        storage_key = build_storage_key(user_id, file_type, extension)
        provider = self._settings.storage_provider.strip().lower()

        if provider == StorageProvider.LOCAL.value:
            destination = self._local_path(storage_key)
            destination.parent.mkdir(parents=True, exist_ok=True)
            copyfile(local_path, destination)
        elif provider == StorageProvider.CLOUDFLARE_R2.value:
            self._r2_client().upload_file(
                str(local_path),
                self._settings.cloudflare_r2_bucket,
                storage_key,
                ExtraArgs={"ContentType": mime_type or "application/octet-stream"},
            )
        else:
            raise RuntimeError(f"Unsupported storage provider: {provider}")

        uploaded_file = UploadedFile(
            user_id=user_id,
            file_type=file_type.value,
            original_filename=original_filename,
            storage_provider=provider,
            storage_key=storage_key,
            mime_type=mime_type,
            size_bytes=local_path.stat().st_size,
        )
        self._session.add(uploaded_file)
        self._session.flush()
        return uploaded_file

    def download_to_temp(self, uploaded_file_id: UUID, destination_dir: Path) -> Path:
        uploaded_file = self._session.get(UploadedFile, uploaded_file_id)
        if uploaded_file is None:
            raise RuntimeError(f"Uploaded file not found: {uploaded_file_id}")

        destination_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(uploaded_file.storage_key).name or str(uploaded_file.id)
        destination = destination_dir / filename

        if uploaded_file.storage_provider == StorageProvider.LOCAL.value:
            source = self._local_path(uploaded_file.storage_key)
            if not source.exists():
                raise RuntimeError(f"Local storage file not found: {uploaded_file.storage_key}")
            copyfile(source, destination)
            return destination

        if uploaded_file.storage_provider == StorageProvider.CLOUDFLARE_R2.value:
            self._r2_client().download_file(
                self._settings.cloudflare_r2_bucket,
                uploaded_file.storage_key,
                str(destination),
            )
            return destination

        raise RuntimeError(f"Unsupported storage provider: {uploaded_file.storage_provider}")

    def get_download_url(
        self,
        uploaded_file: UploadedFile,
        *,
        telegram_id: int | None = None,
        expires_in: int | None = None,
    ) -> str | None:
        if uploaded_file.storage_provider == StorageProvider.LOCAL.value:
            if telegram_id is None:
                return None
            base_url = self._settings.backend_public_url.rstrip("/")
            return f"{base_url}/api/v1/files/{uploaded_file.id}/download?telegram_id={telegram_id}"

        if uploaded_file.storage_provider == StorageProvider.CLOUDFLARE_R2.value:
            public_base_url = self._settings.cloudflare_r2_public_base_url_or_none
            if public_base_url is not None:
                return f"{public_base_url}/{uploaded_file.storage_key}"

            ttl = expires_in or self._settings.cloudflare_r2_presigned_url_ttl_seconds
            return str(
                self._r2_client().generate_presigned_url(
                    "get_object",
                    Params={
                        "Bucket": self._settings.cloudflare_r2_bucket,
                        "Key": uploaded_file.storage_key,
                    },
                    ExpiresIn=ttl,
                )
            )

        raise RuntimeError(f"Unsupported storage provider: {uploaded_file.storage_provider}")

    def _save_local(self, storage_key: str, content: bytes) -> None:
        path = self._local_path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _save_r2(self, storage_key: str, content: bytes, mime_type: str | None) -> None:
        self._r2_client().put_object(
            Bucket=self._settings.cloudflare_r2_bucket,
            Key=storage_key,
            Body=content,
            ContentType=mime_type or "application/octet-stream",
        )

    def _local_path(self, storage_key: str) -> Path:
        return safe_local_path(Path(self._settings.local_storage_dir), storage_key)

    def _r2_client(self):
        return boto3.client(
            "s3",
            endpoint_url=self._settings.resolved_cloudflare_r2_endpoint_url,
            aws_access_key_id=self._settings.cloudflare_r2_access_key_id,
            aws_secret_access_key=self._settings.cloudflare_r2_secret_access_key,
            region_name="auto",
        )
