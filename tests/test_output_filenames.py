from __future__ import annotations

import unittest
from types import SimpleNamespace
from uuid import UUID, uuid4

from backend.app.models.generation_job import GenerationJob
from backend.app.models.uploaded_file import UploadedFile
from worker.app.services.output_filenames import build_generation_output_filename


class _FakeSession:
    def __init__(self, *, batch_basename: str | None = None, audio_filename: str | None = None):
        self.batch_basename = batch_basename
        self.audio_filename = audio_filename

    def scalar(self, _: object) -> str | None:
        return self.batch_basename

    def get(self, model: type[object], _: UUID) -> object | None:
        if model is not UploadedFile or self.audio_filename is None:
            return None
        return SimpleNamespace(original_filename=self.audio_filename)


def _job(
    *,
    job_id: UUID | None = None,
    batch_id: UUID | None = None,
    source_audio_file_id: UUID | None = None,
) -> GenerationJob:
    return GenerationJob(
        id=job_id or uuid4(),
        user_id=uuid4(),
        status="completed",
        source_audio_file_id=source_audio_file_id,
        batch_id=batch_id,
        fps=25,
        width=480,
        height=480,
        segments_count=1,
    )


class OutputFilenameTests(unittest.TestCase):
    def test_batch_job_uses_generation_batch_item_basename(self) -> None:
        filename = build_generation_output_filename(
            _FakeSession(batch_basename="docktor"),  # type: ignore[arg-type]
            _job(batch_id=uuid4(), source_audio_file_id=uuid4()),
        )

        self.assertEqual(filename, "docktor.mp4")

    def test_single_job_uses_source_audio_filename_stem(self) -> None:
        filename = build_generation_output_filename(
            _FakeSession(audio_filename="ed_carabao.mp3"),  # type: ignore[arg-type]
            _job(source_audio_file_id=uuid4()),
        )

        self.assertEqual(filename, "ed_carabao.mp4")

    def test_cyrillic_filename_preserved(self) -> None:
        filename = build_generation_output_filename(
            _FakeSession(audio_filename="голос_ведущий.wav"),  # type: ignore[arg-type]
            _job(source_audio_file_id=uuid4()),
        )

        self.assertEqual(filename, "голос_ведущий.mp4")

    def test_unsafe_characters_removed(self) -> None:
        filename = build_generation_output_filename(
            _FakeSession(batch_basename="bad/name: <x>  test?"),  # type: ignore[arg-type]
            _job(batch_id=uuid4(), source_audio_file_id=uuid4()),
        )

        self.assertEqual(filename, "badname x test.mp4")

    def test_fallback_uses_video_job_id(self) -> None:
        job_id = uuid4()
        filename = build_generation_output_filename(
            _FakeSession(),  # type: ignore[arg-type]
            _job(job_id=job_id),
        )

        self.assertEqual(filename, f"video-{job_id}.mp4")


if __name__ == "__main__":
    unittest.main()
