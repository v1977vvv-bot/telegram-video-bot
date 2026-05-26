from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import select

from backend.app.models.generation_segment import GenerationSegment
from backend.app.models.runpod_pod import RunpodPod
from shared.app.config import Settings, get_settings
from shared.app.enums import PodStatus, SegmentStatus
from worker.app.database import get_worker_session
from worker.app.services.audio import AudioSegmentFile
from worker.app.services.comfyui_client import ComfyUIClient
from worker.app.services.runpod import RunPodError
from worker.app.services.runpod_manager import ManagedComfyUIEndpoint, RunPodManager
from worker.app.services.video_probe import VideoProbeService
from worker.app.services.workflow_patcher import patch_infinite_talk_workflow

logger = logging.getLogger(__name__)


class DistributedJobContext(Protocol):
    job_id: UUID
    width: int
    height: int
    fps: int


class DistributedSegmentContext(Protocol):
    id: UUID
    segment_index: int
    duration_seconds: Decimal
    frame_count: int


@dataclass(frozen=True, slots=True)
class DistributedGenerationResult:
    segment_paths: list[Path]
    parallelism: int


class DistributedSegmentGenerationService:
    """Experimental per-segment ComfyUI runner across multiple warm RunPod pods."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        runpod_manager: RunPodManager | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._manager = runpod_manager or RunPodManager(self._settings)

    def available_healthy_idle_pod_count(self, *, limit: int | None = None) -> int:
        count = 0
        for pod in self._idle_candidates(limit=limit):
            if pod.base_url and self._manager._healthcheck(pod.base_url):  # noqa: SLF001
                count += 1
                if limit is not None and count >= limit:
                    return count
        return count

    def run(
        self,
        *,
        context: DistributedJobContext,
        segments: list[DistributedSegmentContext],
        audio_segments: list[AudioSegmentFile],
        source_image_path: Path,
        output_dir: Path,
        parallelism: int,
    ) -> DistributedGenerationResult:
        if parallelism <= 0:
            raise RuntimeError("Distributed generation parallelism must be positive")
        if not segments:
            raise RuntimeError("Distributed generation has no segments")

        output_dir.mkdir(parents=True, exist_ok=True)
        audio_by_index = {item.segment_index: item for item in audio_segments}
        pending: list[DistributedSegmentContext] = list(segments)
        segment_paths: dict[int, Path] = {}
        futures: dict[Future[Path], tuple[DistributedSegmentContext, int]] = {}
        max_retries = max(self._settings.distributed_segment_max_retries, 0)
        fatal_error: RuntimeError | None = None

        logger.info(
            "Distributed generation selected job_id=%s segments_count=%s parallelism=%s",
            context.job_id,
            len(segments),
            parallelism,
        )
        logger.info(
            "Distributed mode uses source_image for each segment; visual continuity may differ"
        )

        with ThreadPoolExecutor(max_workers=parallelism) as executor:
            while pending or futures:
                scheduled_any = False
                while fatal_error is None and pending and len(futures) < parallelism:
                    segment = pending.pop(0)
                    audio_segment = audio_by_index.get(segment.segment_index)
                    if audio_segment is None:
                        raise RuntimeError(
                            f"Missing audio segment file index={segment.segment_index}"
                        )

                    endpoint = self._reserve_endpoint(context.job_id)
                    if endpoint is None:
                        pending.insert(0, segment)
                        break

                    attempt = self._next_attempt(segment.id)
                    logger.info(
                        "Segment assigned job_id=%s segment_index=%s pod_id=%s attempt=%s",
                        context.job_id,
                        segment.segment_index,
                        endpoint.runpod_pod_id,
                        attempt,
                    )
                    future = executor.submit(
                        self._run_segment_attempt,
                        context,
                        segment,
                        audio_segment,
                        source_image_path,
                        output_dir,
                        endpoint,
                        attempt,
                    )
                    futures[future] = (segment, attempt)
                    scheduled_any = True

                if not futures:
                    if fatal_error is not None:
                        break
                    if scheduled_any:
                        continue
                    self._wait_for_available_pod(context.job_id)
                    continue

                done, _ = wait(futures.keys(), return_when=FIRST_COMPLETED)
                for future in done:
                    segment, attempt = futures.pop(future)
                    try:
                        path = future.result()
                    except Exception as exc:
                        if fatal_error is None and attempt <= max_retries:
                            logger.warning(
                                "Segment retry job_id=%s segment_index=%s attempt=%s error=%s",
                                context.job_id,
                                segment.segment_index,
                                attempt,
                                _safe_error_message(exc),
                            )
                            self._mark_segment_pending_retry(segment.id, exc)
                            pending.append(segment)
                            continue
                        self._mark_segment_failed(segment.id, exc)
                        fatal_error = RuntimeError(
                            f"Distributed segment failed after retries "
                            f"segment_index={segment.segment_index}: {_safe_error_message(exc)}"
                        )
                        continue

                    segment_paths[segment.segment_index] = path
                    logger.info(
                        "Segment completed job_id=%s segment_index=%s path=%s",
                        context.job_id,
                        segment.segment_index,
                        path,
                    )

        if fatal_error is not None:
            for segment in pending:
                self._mark_segment_failed_message(segment.id, str(fatal_error))
            raise fatal_error

        ordered_paths = [segment_paths[item.segment_index] for item in segments]
        return DistributedGenerationResult(segment_paths=ordered_paths, parallelism=parallelism)

    def _run_segment_attempt(
        self,
        context: DistributedJobContext,
        segment: DistributedSegmentContext,
        audio_segment: AudioSegmentFile,
        source_image_path: Path,
        output_dir: Path,
        endpoint: ManagedComfyUIEndpoint,
        attempt: int,
    ) -> Path:
        client = ComfyUIClient(self._settings, base_url=endpoint.base_url)
        try:
            self._mark_segment_assigned(segment.id, endpoint, attempt)
            client.healthcheck()
            path = self._generate_segment(
                client=client,
                context=context,
                segment=segment,
                input_image_path=source_image_path,
                input_audio_segment_path=audio_segment.path,
                output_dir=output_dir,
            )
            self._mark_segment_completed(segment.id)
            self._mark_endpoint_idle(endpoint)
            return path
        except Exception:
            self._release_endpoint_after_failure(endpoint)
            raise
        finally:
            client.close()

    def _generate_segment(
        self,
        *,
        client: ComfyUIClient,
        context: DistributedJobContext,
        segment: DistributedSegmentContext,
        input_image_path: Path,
        input_audio_segment_path: Path,
        output_dir: Path,
    ) -> Path:
        logger.info(
            "Distributed segment generation started job_id=%s segment_index=%s "
            "duration=%s frame_count=%s model_profile=%s",
            context.job_id,
            segment.segment_index,
            segment.duration_seconds,
            segment.frame_count,
            self._settings.comfyui_model_profile_normalized,
        )
        image_upload = client.upload_image(
            input_image_path,
            input_image_path.name,
            self._settings.comfyui_input_subfolder,
        )
        audio_upload = client.upload_audio(
            input_audio_segment_path,
            input_audio_segment_path.name,
            self._settings.comfyui_input_subfolder,
        )
        filename_prefix = (
            f"{self._settings.comfyui_output_subfolder}/"
            f"job_{context.job_id}_segment_{segment.segment_index:03d}"
        )
        prompt = patch_infinite_talk_workflow(
            workflow_path=Path(self._settings.comfyui_workflow_path),
            image_upload=image_upload,
            audio_upload=audio_upload,
            width=context.width,
            height=context.height,
            fps=context.fps,
            frame_count=segment.frame_count,
            filename_prefix=filename_prefix,
            model_profile=self._settings.comfyui_model_profile_normalized,
        )
        prompt_id = client.queue_prompt(prompt)
        self._mark_segment_generating(segment.id, prompt_id)
        logger.info(
            "Distributed segment prompt queued job_id=%s segment_index=%s prompt_id=%s",
            context.job_id,
            segment.segment_index,
            prompt_id,
        )
        history = client.wait_for_completion(
            prompt_id,
            self._settings.comfyui_timeout_seconds,
            self._settings.comfyui_poll_interval_seconds,
        )
        output_file = client.find_video_output(history)
        logger.info(
            "Distributed segment mp4 found job_id=%s segment_index=%s prompt_id=%s "
            "filename=%s subfolder=%s",
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
        return self._trim_video_if_needed(
            job_id=context.job_id,
            label=f"distributed_segment_{segment.segment_index:03d}",
            output_path=downloaded_path,
            target_duration=segment.duration_seconds,
        )

    def _reserve_endpoint(self, job_id: UUID) -> ManagedComfyUIEndpoint | None:
        for pod in self._idle_candidates():
            if pod.base_url is None:
                continue
            if not self._manager._healthcheck(pod.base_url):  # noqa: SLF001
                continue
            with get_worker_session() as session:
                if self._manager._try_mark_pod_busy(session, pod, job_id):  # noqa: SLF001
                    return ManagedComfyUIEndpoint(
                        base_url=pod.base_url,
                        managed=True,
                        runpod_pod_id=pod.runpod_pod_id,
                        db_pod_id=pod.id,
                    )

        if not self._settings.distributed_allow_create_extra_pods:
            return None

        try:
            with get_worker_session() as session:
                endpoint = self._manager.ensure_comfyui_endpoint(session, job_id=job_id)
        except RunPodError as exc:
            logger.warning(
                "Distributed extra pod acquisition failed job_id=%s error=%s", job_id, exc
            )
            return None
        if not endpoint.managed:
            return None
        return endpoint

    def _idle_candidates(self, *, limit: int | None = None) -> list[RunpodPod]:
        with get_worker_session() as session:
            statement = (
                select(RunpodPod)
                .where(
                    RunpodPod.status.in_([PodStatus.IDLE.value, PodStatus.READY.value]),
                    RunpodPod.base_url.is_not(None),
                    RunpodPod.runpod_pod_id.is_not(None),
                    RunpodPod.terminated_at.is_(None),
                    RunpodPod.active_job_id.is_(None),
                    RunpodPod.current_job_id.is_(None),
                )
                .order_by(RunpodPod.updated_at.asc())
            )
            if limit is not None:
                statement = statement.limit(limit)
            pods = list(session.execute(statement).scalars())
            session.commit()
            return pods

    def _next_attempt(self, segment_id: UUID) -> int:
        with get_worker_session() as session:
            segment = session.get(GenerationSegment, segment_id)
            if segment is None:
                raise RuntimeError(f"Generation segment not found segment_id={segment_id}")
            attempt = int(segment.retry_count or 0) + 1
            session.commit()
            return attempt

    def _mark_segment_assigned(
        self,
        segment_id: UUID,
        endpoint: ManagedComfyUIEndpoint,
        attempt: int,
    ) -> None:
        with get_worker_session() as session:
            with session.begin():
                segment = session.get(GenerationSegment, segment_id, with_for_update=True)
                if segment is None:
                    raise RuntimeError(f"Generation segment not found segment_id={segment_id}")
                segment.status = SegmentStatus.ASSIGNED.value
                segment.retry_count = attempt
                segment.runpod_pod_id = endpoint.runpod_pod_id
                segment.prompt_id = None
                segment.error_message = None
                segment.started_at = None
                segment.completed_at = None

    def _mark_segment_generating(self, segment_id: UUID, prompt_id: str) -> None:
        with get_worker_session() as session:
            with session.begin():
                segment = session.get(GenerationSegment, segment_id, with_for_update=True)
                if segment is None:
                    raise RuntimeError(f"Generation segment not found segment_id={segment_id}")
                segment.status = SegmentStatus.GENERATING.value
                segment.prompt_id = prompt_id
                segment.started_at = segment.started_at or _now()
                segment.error_message = None

    def _mark_segment_completed(self, segment_id: UUID) -> None:
        with get_worker_session() as session:
            with session.begin():
                segment = session.get(GenerationSegment, segment_id, with_for_update=True)
                if segment is None:
                    raise RuntimeError(f"Generation segment not found segment_id={segment_id}")
                segment.status = SegmentStatus.COMPLETED.value
                segment.completed_at = _now()
                segment.error_message = None

    def _mark_segment_pending_retry(self, segment_id: UUID, exc: Exception) -> None:
        with get_worker_session() as session:
            with session.begin():
                segment = session.get(GenerationSegment, segment_id, with_for_update=True)
                if segment is None:
                    return
                segment.status = SegmentStatus.PENDING.value
                segment.runpod_pod_id = None
                segment.error_message = _safe_error_message(exc)

    def _mark_segment_failed(self, segment_id: UUID, exc: Exception) -> None:
        self._mark_segment_failed_message(segment_id, _safe_error_message(exc))

    def _mark_segment_failed_message(self, segment_id: UUID, error_message: str) -> None:
        with get_worker_session() as session:
            with session.begin():
                segment = session.get(GenerationSegment, segment_id, with_for_update=True)
                if segment is None:
                    return
                segment.status = SegmentStatus.FAILED.value
                segment.error_message = error_message[:1000]
                segment.completed_at = _now()

    def _mark_endpoint_idle(self, endpoint: ManagedComfyUIEndpoint) -> None:
        try:
            with get_worker_session() as session:
                self._manager.mark_pod_idle(session, endpoint)
        except Exception:
            logger.warning("Distributed pod idle mark failed pod_id=%s", endpoint.runpod_pod_id)

    def _release_endpoint_after_failure(self, endpoint: ManagedComfyUIEndpoint) -> None:
        try:
            with get_worker_session() as session:
                self._manager.release_after_failure(session, endpoint)
        except Exception:
            logger.warning(
                "Distributed pod failure release failed pod_id=%s", endpoint.runpod_pod_id
            )

    def _wait_for_available_pod(self, job_id: UUID) -> None:
        deadline = time.monotonic() + max(self._settings.runpod_queue_retry_seconds, 1)
        while time.monotonic() < deadline:
            if self.available_healthy_idle_pod_count(limit=1) > 0:
                return
            time.sleep(min(5, max(deadline - time.monotonic(), 0)))
        raise RuntimeError(f"No idle RunPod pod became available for distributed job {job_id}")

    def _trim_video_if_needed(
        self,
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


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_error_message(exc: Exception) -> str:
    return (str(exc) or exc.__class__.__name__)[:1000]
