from __future__ import annotations

import hmac
import json
import unittest
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
from urllib.parse import urlencode
from uuid import uuid4

from backend.app.api.v1.generation import _read_upload_with_limit
from backend.app.models.batch_upload_session import BatchUploadSession
from backend.app.services.batch_upload_sessions import (
    BatchUploadSessionService,
    hash_upload_token,
    validate_telegram_webapp_init_data,
)
from shared.app.exceptions import AppError


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    async def scalar(self, _: object) -> object | None:
        return None

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = uuid4()


class _FakeUpload:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def read(self, _: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class BatchUploadSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_session_tokens_are_unique_and_stored_hashed(self) -> None:
        session = _FakeSession()
        service = BatchUploadSessionService(
            session,  # type: ignore[arg-type]
            settings=SimpleNamespace(backend_public_url="https://example.test"),
        )
        user_id = uuid4()

        first = await service.create_session(user_id=user_id, telegram_id=778282290)
        second = await service.create_session(user_id=user_id, telegram_id=778282290)

        self.assertNotEqual(first.plain_token, second.plain_token)
        self.assertIn(f"/batch-upload/?token={first.plain_token}", first.web_app_url)
        stored = session.added[0]
        self.assertEqual(stored.token_hash, hash_upload_token(first.plain_token))
        self.assertNotEqual(stored.token_hash, first.plain_token)

    async def test_read_upload_with_limit_rejects_oversized_upload(self) -> None:
        upload = _FakeUpload([b"ab", b"cd"])

        with self.assertRaises(AppError) as raised:
            await _read_upload_with_limit(upload, max_bytes=3)  # type: ignore[arg-type]

        self.assertEqual(raised.exception.code, "batch_archive_too_large")
        self.assertEqual(raised.exception.status_code, 413)


class BatchUploadSessionValidationTests(unittest.TestCase):
    def test_expired_token_rejected(self) -> None:
        service = BatchUploadSessionService(
            _FakeSession(),  # type: ignore[arg-type]
            settings=SimpleNamespace(backend_public_url="https://example.test"),
        )
        record = BatchUploadSession(
            user_id=uuid4(),
            telegram_id=778282290,
            token_hash="hash",
            status="active",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )

        with self.assertRaises(AppError) as raised:
            service.validate_record(record)

        self.assertEqual(raised.exception.code, "upload_session_expired")

    def test_used_token_rejected(self) -> None:
        service = BatchUploadSessionService(
            _FakeSession(),  # type: ignore[arg-type]
            settings=SimpleNamespace(backend_public_url="https://example.test"),
        )
        record = BatchUploadSession(
            user_id=uuid4(),
            telegram_id=778282290,
            token_hash="hash",
            status="used",
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
            used_at=datetime.now(UTC),
        )

        with self.assertRaises(AppError) as raised:
            service.validate_record(record)

        self.assertEqual(raised.exception.code, "upload_session_used")

    def test_mark_used_links_batch_without_plain_token(self) -> None:
        service = BatchUploadSessionService(
            _FakeSession(),  # type: ignore[arg-type]
            settings=SimpleNamespace(backend_public_url="https://example.test"),
        )
        batch_id = uuid4()
        record = BatchUploadSession(
            user_id=uuid4(),
            telegram_id=778282290,
            token_hash="hash",
            status="active",
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )

        service.mark_used(record, batch_id)

        self.assertEqual(record.batch_id, batch_id)
        self.assertEqual(record.status, "used")
        self.assertIsNotNone(record.used_at)
        self.assertEqual(record.token_hash, "hash")

    def test_telegram_webapp_init_data_validates_user_id(self) -> None:
        bot_token = "123456:ABCDEF"
        init_data = _build_init_data(bot_token=bot_token, telegram_id=778282290)

        telegram_id = validate_telegram_webapp_init_data(init_data, bot_token=bot_token)

        self.assertEqual(telegram_id, 778282290)

    def test_telegram_webapp_init_data_rejects_invalid_hash(self) -> None:
        init_data = _build_init_data(bot_token="123456:ABCDEF", telegram_id=778282290)

        with self.assertRaises(AppError) as raised:
            validate_telegram_webapp_init_data(init_data, bot_token="wrong:token")

        self.assertEqual(raised.exception.code, "telegram_init_data_invalid")


def _build_init_data(*, bot_token: str, telegram_id: int) -> str:
    params = {
        "auth_date": "1710000000",
        "query_id": "AAEAAAE",
        "user": json.dumps({"id": telegram_id}, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), sha256).digest()
    params["hash"] = hmac.new(secret_key, data_check_string.encode(), sha256).hexdigest()
    return urlencode(params)


if __name__ == "__main__":
    unittest.main()
