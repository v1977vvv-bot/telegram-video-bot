from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.models.generation_job import GenerationJob
from backend.app.models.generation_segment import GenerationSegment
from shared.app.config import Settings, get_settings
from shared.app.enums import (
    AudioSegmentationStrategy,
    FileType,
    GenerationMode,
    JobStatus,
    SegmentImageStrategy,
    SegmentStatus,
)
from worker.app.celery_app import celery_app
from worker.app.database import get_worker_session
from worker.app.services.audio import AudioSegmentFile, AudioSegmentPlan, AudioService
from worker.app.services.balance import SyncBalanceService
from worker.app.services.comfyui_client import ComfyUIClient
from worker.app.services.storage import WorkerStorageService
from worker.app.services.telegram_notify import GenerationNotification, TelegramNotifyService
from worker.app.services.video_probe import VideoProbeService
from worker.app.services.video_stitch import VideoStitchService
from worker.app.services.workflow_patcher import patch_infinite_talk_workflow

logger = logging.getLogger(__name__)


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
    segments: list[ComfyUISegmentContext]


@dataclass(frozen=True, slots=True)
class ComfyUISegmentContext:
    id: UUID
    segment_index: int
    start_seconds: Decimal
    end_seconds: Decimal
    duration_seconds: Decimal
    frame_count: int


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
                segments_count=job.segments_count,
                result_url=result_url,
            )

        _notify_generation_completed(notification)
        logger.info("generation job completed and balance captured job_id=%s", job_id)
        logger.info("process_generation_job mock completed job_id=%s", job_id)
        return {"status": "completed", "job_id": str(job_id)}


def _run_comfyui_generation(job_id: UUID, settings: Settings) -> dict[str, str]:
    context = _start_comfyui_job(job_id, settings)
    segment_image_strategy = _resolve_segment_image_strategy(settings)
    temp_root = Path(settings.local_storage_dir) / "worker" / str(job_id)
    input_dir = temp_root / "input"
    audio_segments_dir = temp_root / "audio_segments"
    segments_dir = temp_root / "segments"
    frames_dir = temp_root / "frames"
    final_dir = temp_root / "final"
    success = False
    client = ComfyUIClient(settings)
    try:
        with get_worker_session() as session:
            storage = WorkerStorageService(session, settings)
            image_path = storage.download_to_temp(context.source_image_file_id, input_dir)
            audio_path = storage.download_to_temp(context.source_audio_file_id, input_dir)

        client.healthcheck()
        audio_service = AudioService()
        audio_plan = audio_service.build_segment_plan(
            input_audio_path=audio_path,
            max_segment_seconds=settings.generation_max_segment_seconds,
            total_duration_seconds=context.audio_duration_seconds,
            strategy=settings.audio_segmentation_strategy,
            silence_threshold_db=settings.audio_silence_threshold_db,
            silence_min_duration_seconds=settings.audio_silence_min_duration_seconds,
            silence_search_window_seconds=settings.audio_silence_search_window_seconds,
            segment_min_seconds=settings.audio_segment_min_seconds,
        )
        _log_audio_segment_plan(job_id, audio_plan)
        context = _sync_segments_with_audio_plan(context, audio_plan)
        audio_segments = audio_service.split_audio_by_boundaries(
            input_audio_path=audio_path,
            output_dir=audio_segments_dir,
            boundaries=audio_plan.boundaries,
        )
        _validate_audio_segments(job_id, context, audio_segments)

        current_image_path = image_path
        segment_paths: list[Path] = []
        logger.info(
            "ComfyUI generation strategy job_id=%s segments_count=%s "
            "segment_image_strategy=%s final_audio_strategy=%s",
            job_id,
            len(context.segments),
            segment_image_strategy.value,
            "original_audio_for_multisegment" if len(context.segments) > 1 else "segment_audio",
        )
        for position, segment in enumerate(context.segments):
            audio_segment = audio_segments[position]
            segment_image_path = (
                image_path
                if segment_image_strategy == SegmentImageStrategy.SOURCE_IMAGE
                else current_image_path
            )
            if segment_image_strategy == SegmentImageStrategy.SOURCE_IMAGE:
                logger.info(
                    "Segment image strategy source_image: using original image "
                    "job_id=%s segment_index=%s",
                    job_id,
                    segment.segment_index,
                )
            _mark_segment_generating(segment.id)
            segment_path = _generate_comfyui_segment(
                client=client,
                settings=settings,
                context=context,
                segment=segment,
                input_image_path=segment_image_path,
                input_audio_segment_path=audio_segment.path,
                output_dir=segments_dir,
            )
            segment_paths.append(segment_path)
            _mark_segment_completed(segment.id)

            if (
                position < len(context.segments) - 1
                and segment_image_strategy == SegmentImageStrategy.LAST_FRAME
            ):
                next_frame_path = frames_dir / f"last_frame_{segment.segment_index:03d}.png"
                VideoProbeService().extract_last_frame(segment_path, next_frame_path)
                logger.info(
                    "Last frame extracted job_id=%s segment_index=%s path=%s",
                    job_id,
                    segment.segment_index,
                    next_frame_path,
                )
                current_image_path = next_frame_path

        final_path = _build_final_video(
            job_id=job_id,
            context=context,
            segment_paths=segment_paths,
            final_dir=final_dir,
            original_audio_path=audio_path,
        )

        notification = _complete_comfyui_job(job_id, context, final_path)
        _notify_generation_completed(notification)
        success = True
        logger.info(
            "process_generation_job comfyui completed job_id=%s segments_count=%s",
            job_id,
            len(context.segments),
        )
        return {"status": "completed", "job_id": str(job_id)}
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

            if job.audio_duration_seconds > Decimal(settings.generation_max_audio_seconds):
                raise RuntimeError(
                    f"Audio is too long. Max {settings.generation_max_audio_seconds} seconds"
                )
            if not job.segments:
                raise RuntimeError("Generation job has no segments")

            now = datetime.now(UTC)
            job.status = JobStatus.GENERATING.value
            job.started_at = now

            logger.info(
                "ComfyUI segmented plan job_id=%s audio_duration_seconds=%s fps=%s "
                "segments_count=%s",
                job.id,
                job.audio_duration_seconds,
                job.fps,
                len(job.segments),
            )

            return _context_from_job(job)


def _context_from_job(job: GenerationJob) -> ComfyUIJobContext:
    if job.source_image_file_id is None or job.source_audio_file_id is None:
        raise RuntimeError("Job source image/audio file is missing")
    if job.audio_duration_seconds is None:
        raise RuntimeError("Job audio duration is missing")

    return ComfyUIJobContext(
        job_id=job.id,
        user_id=job.user_id,
        source_image_file_id=job.source_image_file_id,
        source_audio_file_id=job.source_audio_file_id,
        width=job.width,
        height=job.height,
        fps=job.fps,
        audio_duration_seconds=job.audio_duration_seconds,
        segments=[
            ComfyUISegmentContext(
                id=segment.id,
                segment_index=segment.segment_index,
                start_seconds=segment.audio_start_seconds,
                end_seconds=segment.audio_end_seconds,
                duration_seconds=segment.duration_seconds,
                frame_count=segment.frame_count,
            )
            for segment in job.segments
        ],
    )


def _validate_audio_segments(
    job_id: UUID,
    context: ComfyUIJobContext,
    audio_segments: list[AudioSegmentFile],
) -> None:
    if len(audio_segments) != len(context.segments):
        raise RuntimeError(
            f"Audio split produced {len(audio_segments)} segments, expected {len(context.segments)}"
        )

    for expected, actual in zip(context.segments, audio_segments, strict=True):
        if expected.segment_index != actual.segment_index:
            raise RuntimeError(
                f"Audio segment index mismatch: expected {expected.segment_index}, "
                f"got {actual.segment_index}"
            )
        logger.info(
            "Audio segment prepared job_id=%s segments_count=%s segment_index=%s "
            "start=%s end=%s duration=%s",
            job_id,
            len(context.segments),
            actual.segment_index,
            actual.start_seconds,
            actual.end_seconds,
            actual.duration_seconds,
        )


def _log_audio_segment_plan(job_id: UUID, plan: AudioSegmentPlan) -> None:
    logger.info(
        "Audio segmentation strategy job_id=%s strategy=%s silences_found=%s segments_count=%s",
        job_id,
        plan.strategy,
        len(plan.silences),
        len(plan.boundaries),
    )
    for boundary in plan.boundaries:
        logger.info(
            "Audio segment prepared job_id=%s segment_index=%s start=%s end=%s "
            "duration=%s reason=%s",
            job_id,
            boundary.segment_index,
            boundary.start_seconds,
            boundary.end_seconds,
            boundary.duration_seconds,
            boundary.reason,
        )
        if boundary.reason == "silence" and boundary.silence is not None:
            logger.info(
                "Silence-based cut selected job_id=%s target_end=%s cut=%s "
                "silence_start=%s silence_end=%s",
                job_id,
                boundary.target_end_seconds,
                boundary.end_seconds,
                boundary.silence.start_seconds,
                boundary.silence.end_seconds,
            )
        elif (
            plan.strategy == AudioSegmentationStrategy.SILENCE.value and boundary.reason == "fixed"
        ):
            logger.info(
                "Silence cut not found, using fixed boundary job_id=%s target_end=%s",
                job_id,
                boundary.target_end_seconds,
            )


def _sync_segments_with_audio_plan(
    context: ComfyUIJobContext,
    plan: AudioSegmentPlan,
) -> ComfyUIJobContext:
    if _context_matches_audio_plan(context, plan):
        return context

    with get_worker_session() as session:
        with session.begin():
            job = _get_job(session, context.job_id, with_segments=True, for_update=True)
            if job is None:
                raise RuntimeError(f"Generation job not found job_id={context.job_id}")
            if job.status != JobStatus.GENERATING.value:
                raise RuntimeError(f"Job is not generating: {job.status}")
            if any(
                segment.status not in {SegmentStatus.QUEUED.value, SegmentStatus.PENDING.value}
                for segment in job.segments
            ):
                raise RuntimeError("Cannot recalculate segments after segment processing started")

            old_segments_count = len(job.segments)
            for segment in list(job.segments):
                session.delete(segment)
            session.flush()

            for boundary in plan.boundaries:
                session.add(
                    GenerationSegment(
                        job_id=job.id,
                        segment_index=boundary.segment_index,
                        status=SegmentStatus.QUEUED.value,
                        audio_start_seconds=boundary.start_seconds,
                        audio_end_seconds=boundary.end_seconds,
                        duration_seconds=boundary.duration_seconds,
                        frame_count=_frame_count(boundary.duration_seconds, job.fps),
                        input_audio_file_id=job.source_audio_file_id,
                        input_image_file_id=job.source_image_file_id,
                    )
                )
            job.segments_count = len(plan.boundaries)
            session.flush()
            session.expire(job, ["segments"])
            refreshed_job = _get_job(session, context.job_id, with_segments=True, for_update=True)
            if refreshed_job is None:
                raise RuntimeError(f"Generation job not found job_id={context.job_id}")

            logger.info(
                "Segment boundaries recalculated by %s strategy old_segments_count=%s "
                "new_segments_count=%s price_unchanged=true",
                plan.strategy,
                old_segments_count,
                len(plan.boundaries),
            )
            return _context_from_job(refreshed_job)


def _context_matches_audio_plan(context: ComfyUIJobContext, plan: AudioSegmentPlan) -> bool:
    if len(context.segments) != len(plan.boundaries):
        return False
    for segment, boundary in zip(context.segments, plan.boundaries, strict=True):
        if (
            segment.segment_index != boundary.segment_index
            or segment.start_seconds != boundary.start_seconds
            or segment.end_seconds != boundary.end_seconds
            or segment.duration_seconds != boundary.duration_seconds
            or segment.frame_count != _frame_count(boundary.duration_seconds, context.fps)
        ):
            return False
    return True


def _frame_count(duration_seconds: Decimal, fps: int) -> int:
    return int((duration_seconds * Decimal(fps)).to_integral_value(rounding=ROUND_CEILING))


def _resolve_segment_image_strategy(settings: Settings) -> SegmentImageStrategy:
    raw_value = settings.segment_image_strategy.strip().lower()
    try:
        return SegmentImageStrategy(raw_value)
    except ValueError:
        logger.warning(
            "Unknown SEGMENT_IMAGE_STRATEGY=%s; falling back to %s",
            raw_value,
            SegmentImageStrategy.LAST_FRAME.value,
        )
        return SegmentImageStrategy.LAST_FRAME


def _generate_comfyui_segment(
    *,
    client: ComfyUIClient,
    settings: Settings,
    context: ComfyUIJobContext,
    segment: ComfyUISegmentContext,
    input_image_path: Path,
    input_audio_segment_path: Path,
    output_dir: Path,
) -> Path:
    logger.info(
        "Segment generation started job_id=%s segment_index=%s duration=%s frame_count=%s",
        context.job_id,
        segment.segment_index,
        segment.duration_seconds,
        segment.frame_count,
    )
    image_upload = client.upload_image(
        input_image_path,
        input_image_path.name,
        settings.comfyui_input_subfolder,
    )
    audio_upload = client.upload_audio(
        input_audio_segment_path,
        input_audio_segment_path.name,
        settings.comfyui_input_subfolder,
    )
    filename_prefix = (
        f"{settings.comfyui_output_subfolder}/"
        f"job_{context.job_id}_segment_{segment.segment_index:03d}"
    )
    prompt = patch_infinite_talk_workflow(
        workflow_path=Path(settings.comfyui_workflow_path),
        image_upload=image_upload,
        audio_upload=audio_upload,
        width=context.width,
        height=context.height,
        fps=context.fps,
        frame_count=segment.frame_count,
        filename_prefix=filename_prefix,
    )
    prompt_id = client.queue_prompt(prompt)
    logger.info(
        "Segment prompt queued job_id=%s segment_index=%s prompt_id=%s",
        context.job_id,
        segment.segment_index,
        prompt_id,
    )
    history = client.wait_for_completion(
        prompt_id,
        settings.comfyui_timeout_seconds,
        settings.comfyui_poll_interval_seconds,
    )
    output_file = client.find_video_output(history)
    logger.info(
        "Segment mp4 found job_id=%s segment_index=%s prompt_id=%s filename=%s subfolder=%s",
        context.job_id,
        segment.segment_index,
        prompt_id,
        output_file.filename,
        output_file.subfolder,
    )
    downloaded_path = client.download_output(
        filename=output_file.filename,
        subfolder=output_file.subfolder,
        type_=output_file.type,
        destination=output_dir / f"segment_{segment.segment_index:03d}.mp4",
    )
    return _trim_video_if_needed(
        job_id=context.job_id,
        label=f"segment_{segment.segment_index:03d}",
        output_path=downloaded_path,
        target_duration=segment.duration_seconds,
    )


def _build_final_video(
    *,
    job_id: UUID,
    context: ComfyUIJobContext,
    segment_paths: list[Path],
    final_dir: Path,
    original_audio_path: Path,
) -> Path:
    if not segment_paths:
        raise RuntimeError("No generated segment videos")
    if len(segment_paths) == 1:
        return _trim_video_if_needed(
            job_id=job_id,
            label="final",
            output_path=segment_paths[0],
            target_duration=context.audio_duration_seconds,
            shorter_warning_delta=Decimal("0.500"),
        )

    final_path = final_dir / "final.mp4"
    logger.info("Stitch started job_id=%s segments_count=%s", job_id, len(segment_paths))
    stitched_path = VideoStitchService().stitch_mp4_segments(segment_paths, final_path)
    logger.info("Stitch completed job_id=%s path=%s", job_id, stitched_path)

    video_probe = VideoProbeService()
    duration_before_remux = video_probe.get_video_duration_seconds(stitched_path)
    remuxed_path = final_dir / "final_with_original_audio.mp4"
    logger.info(
        "Final audio remux started job_id=%s final_duration_before_remux=%s target_duration=%s",
        job_id,
        duration_before_remux,
        context.audio_duration_seconds,
    )
    remuxed_path = VideoStitchService().replace_audio_with_original(
        video_path=stitched_path,
        original_audio_path=original_audio_path,
        output_path=remuxed_path,
        target_duration_seconds=context.audio_duration_seconds,
    )
    duration_after_remux = video_probe.get_video_duration_seconds(remuxed_path)
    trimmed_path = _trim_video_if_needed(
        job_id=job_id,
        label="final",
        output_path=remuxed_path,
        target_duration=context.audio_duration_seconds,
        shorter_warning_delta=Decimal("0.500"),
    )
    duration_after_trim = video_probe.get_video_duration_seconds(trimmed_path)
    logger.info(
        "Final audio remux completed job_id=%s final_duration_before_remux=%s "
        "final_duration_after_remux=%s final_duration_after_trim=%s",
        job_id,
        duration_before_remux,
        duration_after_remux,
        duration_after_trim,
    )
    return trimmed_path


def _mark_segment_generating(segment_id: UUID) -> None:
    with get_worker_session() as session:
        with session.begin():
            segment = session.get(GenerationSegment, segment_id, with_for_update=True)
            if segment is None:
                raise RuntimeError(f"Generation segment not found segment_id={segment_id}")
            segment.status = SegmentStatus.GENERATING.value
            segment.started_at = datetime.now(UTC)
            segment.error_message = None


def _mark_segment_completed(segment_id: UUID) -> None:
    with get_worker_session() as session:
        with session.begin():
            segment = session.get(GenerationSegment, segment_id, with_for_update=True)
            if segment is None:
                raise RuntimeError(f"Generation segment not found segment_id={segment_id}")
            segment.status = SegmentStatus.COMPLETED.value
            segment.completed_at = datetime.now(UTC)
            segment.error_message = None


def _trim_video_if_needed(
    *,
    job_id: UUID,
    label: str,
    output_path: Path,
    target_duration: Decimal,
    longer_trim_delta: Decimal = Decimal("0.200"),
    shorter_warning_delta: Decimal = Decimal("0.300"),
) -> Path:
    video_probe = VideoProbeService()
    duration_before = video_probe.get_video_duration_seconds(output_path)

    if duration_before < target_duration - shorter_warning_delta:
        logger.warning(
            "Video shorter than target job_id=%s label=%s target_duration=%s "
            "video_duration_before=%s",
            job_id,
            label,
            target_duration,
            duration_before,
        )
        return output_path

    if duration_before <= target_duration + longer_trim_delta:
        logger.info(
            "Video duration accepted job_id=%s label=%s target_duration=%s "
            "video_duration_before=%s video_duration_after=%s",
            job_id,
            label,
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
        "Video duration trimmed job_id=%s label=%s target_duration=%s "
        "video_duration_before=%s video_duration_after=%s",
        job_id,
        label,
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
            logger.info(
                "Final uploaded to storage job_id=%s output_file_id=%s",
                job_id,
                result_file.id,
            )
            logger.info("generation job completed and balance captured job_id=%s", job_id)
            return GenerationNotification(
                telegram_id=telegram_id,
                job_id=job.id,
                audio_duration_seconds=job.audio_duration_seconds,
                price_usd=job.price_usd,
                segments_count=job.segments_count,
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
                segments_count=job.segments_count,
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
