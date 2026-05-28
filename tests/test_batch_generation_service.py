from __future__ import annotations

import unittest
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from backend.app.models.generation_batch import GenerationBatch
from backend.app.models.generation_batch_item import GenerationBatchItem
from backend.app.models.generation_job import GenerationJob
from backend.app.models.generation_segment import GenerationSegment
from backend.app.services.audio import SegmentPlan
from backend.app.services.batch_generation import BatchGenerationService
from backend.app.services.pricing import PricingService
from shared.app.enums import GenerationBatchStatus, JobStatus
from shared.app.exceptions import AppError


def _zip_bytes(files: dict[str, bytes | str]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for filename, content in files.items():
            payload = content.encode() if isinstance(content, str) else content
            archive.writestr(filename, payload)
    return buffer.getvalue()


class _FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeSession:
    def __init__(self, objects: list[object] | None = None) -> None:
        self.added: list[object] = []
        self.objects: dict[tuple[type[object], UUID], object] = {}
        for item in objects or []:
            self._remember(item)

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction()

    def add(self, item: object) -> None:
        self.added.append(item)

    async def flush(self) -> None:
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = uuid4()
            self._remember(item)

    async def get(self, model: type[object], item_id: UUID, **_: object):
        return self.objects.get((model, item_id))

    def _remember(self, item: object) -> None:
        item_id = getattr(item, "id", None)
        if item_id is not None:
            self.objects[(type(item), item_id)] = item


class _FakeAudioService:
    async def get_duration_seconds(self, path: Path) -> Decimal:
        return Decimal(path.read_bytes().decode())

    def build_segments(
        self,
        duration_seconds: Decimal,
        max_segment_seconds: int,
        fps: int,
    ) -> list[SegmentPlan]:
        return [
            SegmentPlan(
                segment_index=1,
                start_seconds=Decimal("0.000"),
                end_seconds=duration_seconds,
                duration_seconds=duration_seconds,
                frame_count=int(duration_seconds * Decimal(fps)),
            )
        ]


class _FakeStorageService:
    def __init__(self) -> None:
        self.saved: list[SimpleNamespace] = []

    async def save_bytes(
        self,
        *,
        user_id,
        file_type,
        original_filename,
        content,
        mime_type,
    ):
        uploaded = SimpleNamespace(
            id=uuid4(),
            user_id=user_id,
            file_type=file_type,
            original_filename=original_filename,
            content=content,
            mime_type=mime_type,
        )
        self.saved.append(uploaded)
        return uploaded


class _FakeBillingReserver:
    def __init__(self) -> None:
        self.reservations: list[tuple[UUID, UUID, Decimal]] = []

    async def reserve_job(
        self,
        *,
        user_id: UUID,
        job: GenerationJob,
        amount_usd: Decimal,
    ) -> None:
        self.reservations.append((user_id, job.id, amount_usd))


class BatchGenerationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_draft_creates_batch_items(self) -> None:
        session = _FakeSession()
        storage = _FakeStorageService()

        with TemporaryDirectory() as temp_dir:
            result = await _service(session, temp_dir, storage=storage).create_batch_draft(
                user_id=uuid4(),
                filename="batch.zip",
                content=_zip_bytes({"001.jpg": b"image", "001.mp3": b"6"}),
                quality_profile="480p",
            )

        self.assertEqual(result.errors, [])
        self.assertEqual(result.status, GenerationBatchStatus.DRAFT.value)
        self.assertEqual(result.total_jobs, 1)
        self.assertEqual(_count_added(session, GenerationBatch), 1)
        self.assertEqual(_count_added(session, GenerationBatchItem), 1)
        self.assertEqual(_count_added(session, GenerationJob), 0)
        self.assertEqual(len(storage.saved), 2)

    async def test_total_price_equals_sum_of_item_prices(self) -> None:
        session = _FakeSession()

        with TemporaryDirectory() as temp_dir:
            result = await _service(session, temp_dir).create_batch_draft(
                user_id=uuid4(),
                filename="batch.zip",
                content=_zip_bytes(
                    {
                        "001.jpg": b"image",
                        "001.mp3": b"6",
                        "002.png": b"image",
                        "002.wav": b"7",
                    }
                ),
                quality_profile="480p",
            )

        item_total = sum((item.price_usd for item in result.items), Decimal("0"))
        self.assertEqual(result.total_price_usd, item_total)
        self.assertEqual(result.total_price_usd, Decimal("0.3450"))

    async def test_480p_and_720p_pricing_work(self) -> None:
        with TemporaryDirectory() as temp_dir:
            result_480 = await _service(_FakeSession(), temp_dir).create_batch_draft(
                user_id=uuid4(),
                filename="batch.zip",
                content=_zip_bytes({"001.jpg": b"image", "001.mp3": b"7"}),
                quality_profile="480p",
            )
            result_720 = await _service(_FakeSession(), temp_dir).create_batch_draft(
                user_id=uuid4(),
                filename="batch.zip",
                content=_zip_bytes({"001.jpg": b"image", "001.mp3": b"7"}),
                quality_profile="720p",
            )

        self.assertEqual(result_480.total_price_usd, Decimal("0.1800"))
        self.assertEqual(result_720.total_price_usd, Decimal("0.3600"))

    async def test_parser_errors_create_no_batch_items_or_jobs(self) -> None:
        session = _FakeSession()
        storage = _FakeStorageService()

        with TemporaryDirectory() as temp_dir:
            result = await _service(session, temp_dir, storage=storage).create_batch_draft(
                user_id=uuid4(),
                filename="batch.zip",
                content=_zip_bytes(
                    {
                        "001.jpg": b"image",
                        "001.mp3": b"6",
                        "notes.txt": b"unsupported",
                    }
                ),
                quality_profile="480p",
            )

        self.assertEqual([error.code for error in result.errors], ["unsupported_file"])
        self.assertEqual(session.added, [])
        self.assertEqual(storage.saved, [])
        self.assertIsNone(result.batch_id)

    async def test_quality_profile_is_saved_on_draft_items(self) -> None:
        session = _FakeSession()

        with TemporaryDirectory() as temp_dir:
            result = await _service(session, temp_dir).create_batch_draft(
                user_id=uuid4(),
                filename="batch.zip",
                content=_zip_bytes({"001.jpg": b"image", "001.mp3": b"6"}),
                quality_profile="720p",
            )

        batch = _first_added(session, GenerationBatch)
        item = _first_added(session, GenerationBatchItem)
        self.assertEqual(batch.quality_profile, "720p")
        self.assertEqual(item.status, JobStatus.DRAFT.value)
        self.assertEqual(result.quality_profile, "720p")

    async def test_confirm_creates_generation_jobs(self) -> None:
        user_id = uuid4()
        batch = _batch(user_id=user_id, quality_profile="480p")
        session = _FakeSession([batch])
        billing = _FakeBillingReserver()
        enqueued: list[str] = []

        with TemporaryDirectory() as temp_dir:
            result = await _service(
                session,
                temp_dir,
                billing=billing,
                enqueue=enqueued.append,
            ).confirm_batch(user_id=user_id, batch_id=batch.id)

        self.assertEqual(result.status, GenerationBatchStatus.CONFIRMED.value)
        self.assertEqual(_count_added(session, GenerationJob), 2)
        self.assertEqual(_count_added(session, GenerationSegment), 2)
        self.assertEqual(len(billing.reservations), 2)
        self.assertEqual(len(enqueued), 2)
        self.assertEqual([item.status for item in batch.items], [JobStatus.QUEUED.value] * 2)

    async def test_confirm_preserves_source_image_audio_per_basename(self) -> None:
        user_id = uuid4()
        batch = _batch(user_id=user_id, quality_profile="480p")
        session = _FakeSession([batch])

        with TemporaryDirectory() as temp_dir:
            await _service(session, temp_dir).confirm_batch(user_id=user_id, batch_id=batch.id)

        jobs = _added(session, GenerationJob)
        jobs_by_index = {job.batch_index: job for job in jobs}
        for item in batch.items:
            job = jobs_by_index[item.batch_index]
            self.assertEqual(job.source_image_file_id, item.source_image_file_id)
            self.assertEqual(job.source_audio_file_id, item.source_audio_file_id)
            self.assertEqual(item.generation_job_id, job.id)

    async def test_confirm_uses_480p_size_for_480p_batch(self) -> None:
        user_id = uuid4()
        batch = _batch(user_id=user_id, quality_profile="480p")
        session = _FakeSession([batch])

        with TemporaryDirectory() as temp_dir:
            await _service(session, temp_dir).confirm_batch(user_id=user_id, batch_id=batch.id)

        job = _first_added(session, GenerationJob)
        self.assertEqual((job.width, job.height), (480, 480))

    async def test_confirm_uses_720p_size_for_720p_batch(self) -> None:
        user_id = uuid4()
        batch = _batch(user_id=user_id, quality_profile="720p")
        session = _FakeSession([batch])

        with TemporaryDirectory() as temp_dir:
            await _service(session, temp_dir).confirm_batch(user_id=user_id, batch_id=batch.id)

        job = _first_added(session, GenerationJob)
        self.assertEqual((job.width, job.height), (720, 720))

    async def test_confirm_rejects_non_owner(self) -> None:
        batch = _batch(user_id=uuid4(), quality_profile="480p")
        session = _FakeSession([batch])

        with TemporaryDirectory() as temp_dir:
            with self.assertRaises(AppError) as context:
                await _service(session, temp_dir).confirm_batch(
                    user_id=uuid4(),
                    batch_id=batch.id,
                )

        self.assertEqual(context.exception.code, "batch_not_found")
        self.assertEqual(_count_added(session, GenerationJob), 0)

    async def test_confirm_rejects_non_draft_batch(self) -> None:
        user_id = uuid4()
        batch = _batch(
            user_id=user_id,
            quality_profile="480p",
            status=GenerationBatchStatus.CONFIRMED.value,
        )
        session = _FakeSession([batch])

        with TemporaryDirectory() as temp_dir:
            with self.assertRaises(AppError) as context:
                await _service(session, temp_dir).confirm_batch(
                    user_id=user_id,
                    batch_id=batch.id,
                )

        self.assertEqual(context.exception.code, "batch_not_confirmable")
        self.assertEqual(_count_added(session, GenerationJob), 0)


def _service(
    session: _FakeSession,
    temp_dir: str,
    *,
    storage: _FakeStorageService | None = None,
    billing: _FakeBillingReserver | None = None,
    enqueue=None,
) -> BatchGenerationService:
    return BatchGenerationService(
        session,  # type: ignore[arg-type]
        settings=SimpleNamespace(
            local_storage_dir=temp_dir,
            generation_fps=25,
            generation_max_segment_seconds=120,
        ),
        audio_service=_FakeAudioService(),
        pricing_service=PricingService(
            SimpleNamespace(
                video_480_min_duration_seconds=5,
                video_480_min_price_usd=Decimal("0.15"),
                video_480_price_per_extra_second_usd=Decimal("0.015"),
                video_720_min_duration_seconds=5,
                video_720_min_price_usd=Decimal("0.30"),
                video_720_price_per_extra_second_usd=Decimal("0.030"),
            )
        ),
        storage_service=storage or _FakeStorageService(),  # type: ignore[arg-type]
        billing_reserver=billing or _FakeBillingReserver(),
        enqueue_generation_job=enqueue or (lambda job_id: None),
    )


def _batch(
    *,
    user_id: UUID,
    quality_profile: str,
    status: str = GenerationBatchStatus.DRAFT.value,
) -> GenerationBatch:
    batch = GenerationBatch(
        id=uuid4(),
        user_id=user_id,
        status=status,
        quality_profile=quality_profile,
        total_jobs=2,
        completed_jobs=0,
        failed_jobs=0,
        total_duration_seconds=Decimal("13"),
        total_price_usd=Decimal("0.3450"),
    )
    batch.items = [
        GenerationBatchItem(
            id=uuid4(),
            batch_id=batch.id,
            batch_index=1,
            basename="001",
            image_filename="001.jpg",
            audio_filename="001.mp3",
            source_image_file_id=uuid4(),
            source_audio_file_id=uuid4(),
            duration_seconds=Decimal("6"),
            price_usd=Decimal("0.1650"),
            status=JobStatus.DRAFT.value,
        ),
        GenerationBatchItem(
            id=uuid4(),
            batch_id=batch.id,
            batch_index=2,
            basename="002",
            image_filename="002.png",
            audio_filename="002.wav",
            source_image_file_id=uuid4(),
            source_audio_file_id=uuid4(),
            duration_seconds=Decimal("7"),
            price_usd=Decimal("0.1800"),
            status=JobStatus.DRAFT.value,
        ),
    ]
    return batch


def _count_added(session: _FakeSession, cls: type[object]) -> int:
    return len(_added(session, cls))


def _added(session: _FakeSession, cls: type[object]):
    return [item for item in session.added if isinstance(item, cls)]


def _first_added(session: _FakeSession, cls: type[object]):
    return next(item for item in session.added if isinstance(item, cls))


if __name__ == "__main__":
    unittest.main()
