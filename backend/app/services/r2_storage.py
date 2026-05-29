from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import anyio
import boto3
from botocore.exceptions import ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.uploaded_file import UploadedFile
from shared.app.config import Settings, get_settings
from shared.app.enums import FileType, StorageProvider
from shared.app.exceptions import AppError
from shared.app.storage import build_storage_key, validated_extension


class R2StorageService:
    """Cloudflare R2 implementation using the S3-compatible boto3 client."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._client = boto3.client(
            "s3",
            endpoint_url=self._settings.resolved_cloudflare_r2_endpoint_url,
            aws_access_key_id=self._settings.cloudflare_r2_access_key_id,
            aws_secret_access_key=self._settings.cloudflare_r2_secret_access_key,
            region_name="auto",
        )

    async def save_bytes(
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
        await anyio.to_thread.run_sync(
            lambda: self._client.put_object(
                Bucket=self._settings.cloudflare_r2_bucket,
                Key=storage_key,
                Body=content,
                **_object_extra_args(mime_type, original_filename),
            )
        )
        return await self._create_uploaded_file(
            user_id=user_id,
            file_type=file_type,
            original_filename=original_filename,
            storage_key=storage_key,
            mime_type=mime_type,
            size_bytes=len(content),
        )

    async def save_file(
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
        extra_args = _object_extra_args(mime_type, original_filename)
        await anyio.to_thread.run_sync(
            lambda: self._client.upload_file(
                str(local_path),
                self._settings.cloudflare_r2_bucket,
                storage_key,
                ExtraArgs=extra_args,
            )
        )
        return await self._create_uploaded_file(
            user_id=user_id,
            file_type=file_type,
            original_filename=original_filename,
            storage_key=storage_key,
            mime_type=mime_type,
            size_bytes=local_path.stat().st_size,
        )

    def get_download_url(
        self,
        uploaded_file: UploadedFile,
        expires_in: int | None = None,
    ) -> str | None:
        public_base_url = self._settings.cloudflare_r2_public_base_url_or_none
        if public_base_url is not None:
            return f"{public_base_url}/{uploaded_file.storage_key}"

        ttl = expires_in or self._settings.cloudflare_r2_presigned_url_ttl_seconds
        params = {
            "Bucket": self._settings.cloudflare_r2_bucket,
            "Key": uploaded_file.storage_key,
        }
        if uploaded_file.original_filename:
            params["ResponseContentDisposition"] = _content_disposition(
                uploaded_file.original_filename
            )
        return str(
            self._client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=ttl,
            )
        )

    async def delete(self, uploaded_file: UploadedFile) -> None:
        await anyio.to_thread.run_sync(
            lambda: self._client.delete_object(
                Bucket=self._settings.cloudflare_r2_bucket,
                Key=uploaded_file.storage_key,
            )
        )

    async def exists(self, uploaded_file: UploadedFile) -> bool:
        try:
            await anyio.to_thread.run_sync(
                lambda: self._client.head_object(
                    Bucket=self._settings.cloudflare_r2_bucket,
                    Key=uploaded_file.storage_key,
                )
            )
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404:
                return False
            raise AppError("Storage object lookup failed", code="storage_lookup_failed") from exc
        return True

    def get_local_path(self, uploaded_file: UploadedFile) -> Path | None:
        return None

    async def _create_uploaded_file(
        self,
        *,
        user_id: UUID | None,
        file_type: FileType,
        original_filename: str | None,
        storage_key: str,
        mime_type: str | None,
        size_bytes: int | None,
    ) -> UploadedFile:
        uploaded_file = UploadedFile(
            user_id=user_id,
            file_type=file_type.value,
            original_filename=original_filename,
            storage_provider=StorageProvider.CLOUDFLARE_R2.value,
            storage_key=storage_key,
            mime_type=mime_type,
            size_bytes=size_bytes,
        )
        self._session.add(uploaded_file)
        await self._session.flush()
        return uploaded_file


def _content_disposition(filename: str) -> str:
    ascii_filename = filename.encode("ascii", "ignore").decode() or "video.mp4"
    ascii_filename = ascii_filename.replace("\\", "").replace('"', "")
    return f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _object_extra_args(mime_type: str | None, original_filename: str | None) -> dict[str, str]:
    extra_args = {"ContentType": mime_type or "application/octet-stream"}
    if original_filename:
        extra_args["ContentDisposition"] = _content_disposition(original_filename)
    return extra_args
