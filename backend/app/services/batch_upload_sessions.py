from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, unquote, urlencode
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.batch_upload_session import BatchUploadSession
from shared.app.config import Settings, get_settings
from shared.app.exceptions import AppError


@dataclass(frozen=True, slots=True)
class BatchUploadSessionCreated:
    plain_token: str
    web_app_url: str
    session: BatchUploadSession


class BatchUploadSessionService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    async def create_session(
        self,
        *,
        user_id: UUID,
        telegram_id: int,
        ttl_minutes: int = 60,
    ) -> BatchUploadSessionCreated:
        ttl = max(ttl_minutes, 1)
        for _ in range(5):
            token = secrets.token_urlsafe(32)
            token_hash = hash_upload_token(token)
            exists = await self._session.scalar(
                select(BatchUploadSession.id).where(BatchUploadSession.token_hash == token_hash)
            )
            if exists is not None:
                continue

            record = BatchUploadSession(
                user_id=user_id,
                telegram_id=telegram_id,
                token_hash=token_hash,
                status="active",
                expires_at=datetime.now(UTC) + timedelta(minutes=ttl),
            )
            self._session.add(record)
            await self._session.flush()
            return BatchUploadSessionCreated(
                plain_token=token,
                web_app_url=self._build_web_app_url(token),
                session=record,
            )
        raise AppError("Could not create upload session", code="upload_session_token_collision")

    async def validate_session_token(self, token: str) -> BatchUploadSession:
        token_hash = hash_upload_token(token)
        record = await self._session.scalar(
            select(BatchUploadSession).where(BatchUploadSession.token_hash == token_hash)
        )
        if record is None:
            raise AppError(
                "Upload session not found",
                code="upload_session_not_found",
                status_code=404,
            )
        self._validate_record(record)
        return record

    def validate_record(self, record: BatchUploadSession) -> None:
        self._validate_record(record)

    def mark_used(self, record: BatchUploadSession, batch_id: UUID) -> None:
        now = datetime.now(UTC)
        record.batch_id = batch_id
        record.status = "used"
        record.used_at = now

    def link_batch(self, record: BatchUploadSession, batch_id: UUID, quality_profile: str) -> None:
        record.batch_id = batch_id
        record.quality_profile = quality_profile

    def _build_web_app_url(self, token: str) -> str:
        base_url = self._settings.backend_public_url.rstrip("/")
        return f"{base_url}/batch-upload/?{urlencode({'token': token})}"

    def _validate_record(self, record: BatchUploadSession) -> None:
        now = datetime.now(UTC)
        expires_at = record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if record.status != "active" or record.used_at is not None:
            raise AppError(
                "Upload session already used",
                code="upload_session_used",
                status_code=400,
            )
        if expires_at <= now:
            raise AppError(
                "Upload session expired",
                code="upload_session_expired",
                status_code=400,
            )


def hash_upload_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_telegram_webapp_init_data(
    init_data: str,
    *,
    bot_token: str,
) -> int:
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise AppError("Telegram initData hash is missing", code="telegram_init_data_invalid")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(parsed.items()))
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise AppError("Telegram initData hash is invalid", code="telegram_init_data_invalid")

    user_raw = parsed.get("user")
    if not user_raw:
        raise AppError("Telegram initData user is missing", code="telegram_init_data_invalid")
    try:
        user = json.loads(unquote(user_raw))
        telegram_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AppError(
            "Telegram initData user is invalid", code="telegram_init_data_invalid"
        ) from exc
    return telegram_id
