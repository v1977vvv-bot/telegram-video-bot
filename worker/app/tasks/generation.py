from __future__ import annotations

import logging
import math
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.models.generation_job import GenerationJob
from backend.app.models.generation_segment import GenerationSegment
from shared.app.config import Settings, get_settings
from shared.app.enums import FileType, GenerationMode, JobStatus, SegmentStatus
from worker.app.celery_app import celery_app
from worker.app.database import get_worker_session
from worker.app.services.balance import SyncBalanceService
from worker.app.services.comfyui_client import ComfyUIClient
from worker.app.services.storage import WorkerStorageService
from worker.app.services.telegram_notify import GenerationNotification, TelegramNotifyService
from worker.app.services.video_probe import VideoProbeService
from worker.app.services.workflow_patcher import patch_infinite_talk_workflow

logger = logging.getLogger(__name__)
ONE_SEGMENT_ERROR = "ComfyUI mode currently supports only one segment up to 30 seconds"


class JobNotFoundError(RuntimeError):
    pass


class JobSkippedError(RuntimeError):
    def __init__(self, status: str) -> None:
        super().__init__(f"Job skipped with status={status}")
        self.status = status


@dataclass(frozen=True, slots=True)
class ComfyUIJobContext:
    job_id: UUID
    user_id: UUID
    source_image_file_id: UUID
    source_audio_file_id: UUID
    width: int
    height: int
    fps: int
    audio_duration_seconds: Decimal
    frame_count: int
    segment_ids: list[UUID]


@celery_app.task(name="process_generation_job")
def process_generation_job(job_id: str) -> dict[str, str]:
    logger.info("process_generation_job started job_id=%s", job_id)
    return _process_generation_job(UUID(job_id))


def _process_generation_job(job_id: UUID) -> dict[str, str]:
    settings = get_settings()
    mode = settings.generation_mode.strip().lower()
    try:
        if mode == GenerationMode.MOCK.value:
            return _run_mock_generation(job_id)
        if mode == GenerationMode.COMFYUI.value:
            return _run_comfyui_generation(job_id, settings)
        raise RuntimeError(f"Unsupported generation mode: {mode}")
    except JobNotFoundError:
        logger.warning("Generation job not found job_id=%s", job_id)
        return {"status": "not_found", "job_id": str(job_id)}
    except JobSkippedError as exc:
        logger.info("Skipping job_id=%s with status=%s", job_id, exc.status)
        return {"status": "skipped", "job_id": str(job_id)}
    except Exception as exc:
        error_message = _safe_error_message(exc)
        logger.exception("process_generation_job failed job_id=%s mode=%s", job_id, mode)
        notification = _mark_job_failed(job_id, error_message)
        _notify_generation_failed(notification)
        return {"status": "failed", "job_id": str(job_id), "error": error_message}


def _run_mock_generation(job_id: UUID) -> dict[str, str]:
    segment_ids: list[UUID] = []
    with get_worker_session() as session:
        with session.begin():
            job = _get_job(session, job_id, with_segments=True, for_update=True)
            if job is None:
                logger.warning("Generation job not found job_id=%s", job_id)
                return {"status": "not_found", "job_id": str(job_id)}
            if job.status != JobStatus.QUEUED.value:
                logger.info("Skipping job_id=%s with status=%s", job_id, job.status)
                return {"status": "skipped", "job_id": str(job_id)}

            now = datetime.now(UTC)
            job.status = JobStatus.GENERATING.value
            job.started_at = now
            segment_ids = [segment.id for segment in job.segments]

        for segment_id in segment_ids:
            with session.begin():
                segment = session.get(GenerationSegment, segment_id)
                if segment is None:
                    continue
                segment.status = SegmentStatus.GENERATING.value
                segment.started_at = datetime.now(UTC)

            time.sleep(1)

            with session.begin():
                segment = session.get(GenerationSegment, segment_id)
                if segment is None:
                    continue
                segment.status = SegmentStatus.COMPLETED.value
                segment.completed_at = datetime.now(UTC)

        notification: GenerationNotification | None = None
        with session.begin():
            job = _get_job(session, job_id, with_segments=True, for_update=True)
            if job is None:
                return {"status": "not_found", "job_id": str(job_id)}
            if job.price_usd is None:
                raise RuntimeError("Job price is missing")

            result_file = WorkerStorageService(session).save_bytes(
                user_id=job.user_id,
                file_type=FileType.VIDEO,
                original_filename=f"mock-result-{job.id}.txt",
                content=f"Mock generation completed successfully for job {job.id}".encode(),
                mime_type="text/plain",
            )
            SyncBalanceService(session).capture_frozen_balance(
                user_id=job.user_id,
                amount_usd=job.price_usd,
                related_job_id=job.id,
                reason="Mock generation completed",
            )
            telegram_id = job.user.telegram_id
            result_url = WorkerStorageService(session).get_download_url(
                result_file,
                telegram_id=telegram_id,
            )
            job.output_file_id = result_file.id
            job.status = JobStatus.COMPLETED.value
            job.completed_at = datetime.now(UTC)
            job.mock_result_message = "Mock generation completed successfully"
            job.error_message = None
            notification = GenerationNotification(
                telegram_id=telegram_id,
                job_id=job.id,
                audio_duration_seconds=job.audio_duration_seconds,
                price_usd=job.price_usd,
                result_url=result_url,
            )

        _notify_generation_completed(notification)
        logger.info("generation job completed and balance captured job_id=%s", job_id)
        logger.info("process_generation_job mock completed job_id=%s", job_id)
        return {"status": "completed", "job_id": str(job_id)}


def _run_comfyui_generation(job_id: UUID, settings: Settings) -> dict[str, str]:
    context = _start_comfyui_job(job_id, settings)
    temp_root = Path(settings.local_storage_dir) / "worker" / str(job_id)
    input_dir = temp_root / "input"
    output_dir = temp_root / "output"
    success = False
    client = ComfyUIClient(settings)
    try:
        with get_worker_session() as session:
            storage = WorkerStorageService(session, settings)
            image_path = storage.download_to_temp(context.source_image_file_id, input_dir)
            audio_path = storage.download_to_temp(context.source_audio_file_id, input_dir)

        client.healthcheck()
        image_upload = client.upload_image(
            image_path,
            image_path.name,
            settings.comfyui_input_subfolder,
        )
        audio_upload = client.upload_audio(
            audio_path,
            audio_path.name,
            settings.comfyui_input_subfolder,
        )
        filename_prefix = f"{settings.comfyui_output_subfolder}/job_{job_id}"
        prompt = patch_infinite_talk_workflow(
            workflow_path=Path(settings.comfyui_workflow_path),
            image_upload=image_upload,
            audio_upload=audio_upload,
            width=context.width,
            height=context.height,
            fps=context.fps,
            frame_count=context.frame_count,
            filename_prefix=filename_prefix,
        )
        prompt_id = client.queue_prompt(prompt)
        logger.info("ComfyUI prompt queued job_id=%s prompt_id=%s", job_id, prompt_id)
        history = client.wait_for_completion(
            prompt_id,
            settings.comfyui_timeout_seconds,
            settings.comfyui_poll_interval_seconds,
        )
        output_file = client.find_video_output(history)
        logger.info(
            "ComfyUI mp4 output found job_id=%s prompt_id=%s filename=%s subfolder=%s",
            job_id,
            prompt_id,
            output_file.filename,
            output_file.subfolder,
        )
        output_path = client.download_output(
            filename=output_file.filename,
            subfolder=output_file.subfolder,
            type_=output_file.type,
            destination=output_dir / output_file.filename,
        )
        output_path = _trim_comfyui_output_if_needed(job_id, context, output_path)
        notification = _complete_comfyui_job(job_id, context, output_path)
        _notify_generation_completed(notification)
        success = True
        logger.info(
            "process_generation_job comfyui completed job_id=%s prompt_id=%s",
            job_id,
            prompt_id,
        )
        return {"status": "completed", "job_id": str(job_id), "prompt_id": prompt_id}
    finally:
        client.close()
        if success or settings.app_env != "local":
            shutil.rmtree(temp_root, ignore_errors=True)


def _start_comfyui_job(job_id: UUID, settings: Settings) -> ComfyUIJobContext:
    with get_worker_session() as session:
        with session.begin():
            job = _get_job(session, job_id, with_segments=True, for_update=True)
            if job is None:
                raise JobNotFoundError(f"Generation job not found job_id={job_id}")
            if job.status != JobStatus.QUEUED.value:
                raise JobSkippedError(job.status)
            if job.source_image_file_id is None or job.source_audio_file_id is None:
                raise RuntimeError("Job source image/audio file is missing")
            if job.audio_duration_seconds is None:
                raise RuntimeError("Job audio duration is missing")
            if job.price_usd is None:
                raise RuntimeError("Job price is missing")

            segment_ids = [segment.id for segment in job.segments]
            if len(segment_ids) != 1 or job.audio_duration_seconds > Decimal(
                settings.generation_max_segment_seconds
            ):
                raise RuntimeError(ONE_SEGMENT_ERROR)

            now = datetime.now(UTC)
            job.status = JobStatus.GENERATING.value
            job.started_at = now
            for segment in job.segments:
                segment.status = SegmentStatus.GENERATING.value
                segment.started_at = now

            frame_count = math.ceil(float(job.audio_duration_seconds * job.fps))
            logger.info(
                "ComfyUI frame plan job_id=%s audio_duration_seconds=%s fps=%s frame_count=%s",
                job.id,
                job.audio_duration_seconds,
                job.fps,
                frame_count,
            )

            return ComfyUIJobContext(
                job_id=job.id,
                user_id=job.user_id,
                source_image_file_id=job.source_image_file_id,
                source_audio_file_id=job.source_audio_file_id,
                width=job.width,
                height=job.height,
                fps=job.fps,
                audio_duration_seconds=job.audio_duration_seconds,
                frame_count=frame_count,
                segment_ids=segment_ids,
            )


def _trim_comfyui_output_if_needed(
    job_id: UUID,
    context: ComfyUIJobContext,
    output_path: Path,
) -> Path:
    video_probe = VideoProbeService()
    duration_before = video_probe.get_video_duration_seconds(output_path)
    target_duration = context.audio_duration_seconds

    if duration_before < target_duration - Decimal("0.300"):
        logger.warning(
            "ComfyUI video shorter than audio job_id=%s audio_duration_seconds=%s "
            "video_duration_before=%s",
            job_id,
            target_duration,
            duration_before,
        )
        return output_path

    if duration_before <= target_duration + Decimal("0.200"):
        logger.info(
            "ComfyUI video duration accepted job_id=%s audio_duration_seconds=%s "
            "video_duration_before=%s video_duration_after=%s",
            job_id,
            target_duration,
            duration_before,
            duration_before,
        )
        return output_path

    trimmed_path = output_path.with_name(f"{output_path.stem}.trimmed{output_path.suffix}")
    video_probe.trim_video_to_duration(
        input_path=output_path,
        output_path=trimmed_path,
        duration_seconds=target_duration,
    )
    duration_after = video_probe.get_video_duration_seconds(trimmed_path)
    logger.info(
        "ComfyUI video duration trimmed job_id=%s audio_duration_seconds=%s "
        "video_duration_before=%s video_duration_after=%s",
        job_id,
        target_duration,
        duration_before,
        duration_after,
    )
    return trimmed_path


def _complete_comfyui_job(
    job_id: UUID,
    context: ComfyUIJobContext,
    output_path: Path,
) -> GenerationNotification:
    with get_worker_session() as session:
        with session.begin():
            job = _get_job(session, job_id, with_segments=True, for_update=True)
            if job is None:
                raise RuntimeError(f"Generation job not found job_id={job_id}")
            if job.status != JobStatus.GENERATING.value:
                raise RuntimeError(f"Job is not generating: {job.status}")
            if job.price_usd is None:
                raise RuntimeError("Job price is missing")

            result_file = WorkerStorageService(session).save_file(
                user_id=context.user_id,
                file_type=FileType.VIDEO,
                original_filename=f"comfyui-result-{job.id}.mp4",
                local_path=output_path,
                mime_type="video/mp4",
            )
            SyncBalanceService(session).capture_frozen_balance(
                user_id=job.user_id,
                amount_usd=job.price_usd,
                related_job_id=job.id,
                reason="ComfyUI generation completed",
            )
            telegram_id = job.user.telegram_id
            result_url = WorkerStorageService(session).get_download_url(
                result_file,
                telegram_id=telegram_id,
            )
            now = datetime.now(UTC)
            job.output_file_id = result_file.id
            job.status = JobStatus.COMPLETED.value
            job.completed_at = now
            job.mock_result_message = "ComfyUI generation completed successfully"
            job.error_message = None
            for segment in job.segments:
                segment.status = SegmentStatus.COMPLETED.value
                segment.completed_at = now
            logger.info("generation job completed and balance captured job_id=%s", job_id)
            return GenerationNotification(
                telegram_id=telegram_id,
                job_id=job.id,
                audio_duration_seconds=job.audio_duration_seconds,
                price_usd=job.price_usd,
                result_url=result_url,
            )


def _mark_job_failed(job_id: UUID, error_message: str) -> GenerationNotification | None:
    with get_worker_session() as session:
        with session.begin():
            job = _get_job(session, job_id, with_segments=False, for_update=True)
            if job is None:
                return None
            if job.status in {
                JobStatus.COMPLETED.value,
                JobStatus.CANCELLED.value,
                JobStatus.FAILED.value,
            }:
                return None

            refund_error: str | None = None
            funds_returned = False
            if job.price_usd is not None and job.status in {
                JobStatus.QUEUED.value,
                JobStatus.GENERATING.value,
            }:
                try:
                    SyncBalanceService(session).refund_frozen_balance(
                        user_id=job.user_id,
                        amount_usd=job.price_usd,
                        related_job_id=job.id,
                        reason="Generation failed",
                    )
                    funds_returned = True
                except Exception as exc:
                    refund_error = str(exc)
                    logger.exception("Failed to refund frozen balance job_id=%s", job_id)

            job.status = JobStatus.FAILED.value
            if refund_error is not None:
                job.error_message = f"{error_message}; refund failed: {refund_error}"
            else:
                job.error_message = error_message
            job.completed_at = datetime.now(UTC)

            result = session.execute(
                select(GenerationSegment).where(GenerationSegment.job_id == job.id)
            )
            for segment in result.scalars():
                if segment.status != SegmentStatus.COMPLETED.value:
                    segment.status = SegmentStatus.FAILED.value
                    segment.error_message = error_message
                    segment.completed_at = datetime.now(UTC)

            logger.info(
                "generation job failed and frozen balance refunded job_id=%s refunded=%s",
                job_id,
                funds_returned,
            )
            return GenerationNotification(
                telegram_id=job.user.telegram_id,
                job_id=job.id,
                audio_duration_seconds=job.audio_duration_seconds,
                price_usd=job.price_usd,
                error_message=job.error_message,
                funds_returned=funds_returned,
            )


def _notify_generation_completed(notification: GenerationNotification | None) -> None:
    if notification is None:
        return
    try:
        TelegramNotifyService().send_generation_completed(notification)
    except Exception:
        logger.warning("Generation completion notification failed job_id=%s", notification.job_id)


def _notify_generation_failed(notification: GenerationNotification | None) -> None:
    if notification is None:
        return
    try:
        TelegramNotifyService().send_generation_failed(notification)
    except Exception:
        logger.warning("Generation failure notification failed job_id=%s", notification.job_id)


def _safe_error_message(exc: Exception) -> str:
    message = str(exc) or exc.__class__.__name__
    return message[:1000]


def _get_job(
    session: Session,
    job_id: UUID,
    *,
    with_segments: bool,
    for_update: bool,
) -> GenerationJob | None:
    statement = select(GenerationJob).where(GenerationJob.id == job_id)
    if with_segments:
        statement = statement.options(selectinload(GenerationJob.segments))
    if for_update:
        statement = statement.with_for_update()
    return session.execute(statement).scalar_one_or_none()
