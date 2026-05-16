from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import anyio
import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.models.balance_account import BalanceAccount
from backend.app.models.balance_transaction import BalanceTransaction
from backend.app.models.generation_job import GenerationJob
from backend.app.models.generation_segment import GenerationSegment
from backend.app.models.runpod_pod import RunpodPod
from backend.app.models.uploaded_file import UploadedFile
from backend.app.repositories.users import UserRepository
from backend.app.schemas.debug import (
    DebugAddBalanceRequest,
    DebugAudioSegmentPlanItemResponse,
    DebugAudioSegmentPlanResponse,
    DebugBalanceLedgerResponse,
    DebugBalanceResponse,
    DebugComfyUIHealthResponse,
    DebugComfyUIPatchWorkflowPreviewRequest,
    DebugComfyUIPatchWorkflowPreviewResponse,
    DebugComfyUIValidateWorkflowResponse,
    DebugCreateMockJobsRequest,
    DebugCreateMockJobsResponse,
    DebugFailRefundGenerationJobResponse,
    DebugGenerationJobListItemResponse,
    DebugGenerationJobSegmentsResponse,
    DebugGenerationJobsResponse,
    DebugGenerationSegmentResponse,
    DebugLedgerJobResponse,
    DebugLedgerTransactionResponse,
    DebugRepairFrozenBalancesResponse,
    DebugRetryWaitingGpuResponse,
    DebugRunPodCleanupResponse,
    DebugRunPodCreatePodResponse,
    DebugRunPodDeleteResponse,
    DebugRunPodGpuAttemptResponse,
    DebugRunPodKeeperTickResponse,
    DebugRunPodPodResponse,
    DebugRunPodPodsResponse,
    DebugStorageCleanupResponse,
    DebugStorageDeleteResponse,
    DebugStorageTestUploadRequest,
    DebugStorageTestUploadResponse,
    DebugTaskResponse,
    DebugTelegramTestNotificationRequest,
    DebugTelegramTestNotificationResponse,
)
from backend.app.schemas.users import BalanceResponse
from backend.app.services.balances import BalanceService
from backend.app.services.file_cleanup import FileCleanupService
from backend.app.services.pricing import PricingService
from backend.app.services.storage import StorageServiceFactory
from backend.app.services.telegram_notify import TelegramNotificationService
from backend.app.workers.celery_client import (
    enqueue_debug_ping,
    enqueue_generation_job,
    enqueue_retry_waiting_for_gpu_jobs,
)
from shared.app.config import get_settings
from shared.app.database import get_session
from shared.app.enums import BalanceTransactionType, FileType, JobStatus, SegmentStatus
from shared.app.exceptions import AppError
from worker.app.services.audio import AudioService as WorkerAudioService
from worker.app.services.runpod import RunPodCapacityError, RunPodClient, RunPodError
from worker.app.services.runpod_keeper import RunPodKeeper
from worker.app.services.workflow_patcher import (
    WorkflowPatchError,
    preview_infinite_talk_patch_values,
    validate_infinite_talk_workflow,
)

router = APIRouter(prefix="/debug", tags=["debug"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
MONEY_QUANT = Decimal("0.0001")
logger = logging.getLogger(__name__)
ACTIVE_GENERATION_JOB_STATUSES = {
    JobStatus.QUEUED.value,
    JobStatus.WAITING_FOR_GPU.value,
    JobStatus.POD_STARTING.value,
    JobStatus.UPLOADING_INPUTS.value,
    JobStatus.GENERATING.value,
    JobStatus.STITCHING.value,
    JobStatus.UPLOADING_RESULT.value,
    "processing",
    "running",
}
FAIL_REFUND_ALLOWED_JOB_STATUSES = {
    *ACTIVE_GENERATION_JOB_STATUSES,
    JobStatus.CANCELLED.value,
    JobStatus.FAILED.value,
}


@router.post("/enqueue-ping", response_model=DebugTaskResponse)
def enqueue_ping() -> DebugTaskResponse:
    result = enqueue_debug_ping()
    return DebugTaskResponse(task_id=result.id, status="queued")


@router.post("/users/{telegram_id}/add-balance", response_model=DebugBalanceResponse)
async def add_debug_balance(
    telegram_id: int,
    payload: DebugAddBalanceRequest,
    session: SessionDep,
) -> DebugBalanceResponse:
    settings = get_settings()
    if settings.app_env != "local":
        raise AppError(
            "Debug endpoint is available only in local env", code="not_found", status_code=404
        )

    async with session.begin():
        user = await UserRepository().get_by_telegram_id(session, telegram_id)
        if user is None:
            raise AppError("User not found", code="user_not_found", status_code=404)

        account = await BalanceService(session).add_balance_in_transaction(
            user_id=user.id,
            amount_usd=payload.amount_usd,
            reason=payload.reason,
        )
    return DebugBalanceResponse(
        telegram_id=telegram_id,
        balance=BalanceResponse(
            available_usd=account.available_usd,
            frozen_usd=account.frozen_usd,
        ),
    )


@router.post(
    "/users/{telegram_id}/mock-generation-jobs",
    response_model=DebugCreateMockJobsResponse,
)
async def create_debug_mock_generation_jobs(
    telegram_id: int,
    payload: DebugCreateMockJobsRequest,
    session: SessionDep,
) -> DebugCreateMockJobsResponse:
    settings = get_settings()
    if settings.app_env != "local":
        raise AppError(
            "Debug endpoint is available only in local env", code="not_found", status_code=404
        )

    job_ids: list[UUID] = []
    async with session.begin():
        user = await UserRepository().get_by_telegram_id(session, telegram_id)
        if user is None:
            raise AppError("User not found", code="user_not_found", status_code=404)

        duration = payload.duration_seconds.quantize(Decimal("0.001"))
        price = PricingService(settings).calculate_job_price(duration)
        now = datetime.now(UTC)
        for _ in range(payload.count):
            job = GenerationJob(
                user_id=user.id,
                status=JobStatus.QUEUED.value,
                fps=settings.generation_fps,
                width=payload.width,
                height=payload.height,
                audio_duration_seconds=duration,
                segments_count=1,
                price_usd=price,
                confirmed_at=now,
                queued_at=now,
            )
            session.add(job)
            await session.flush()

            segment = GenerationSegment(
                job_id=job.id,
                segment_index=1,
                status=SegmentStatus.QUEUED.value,
                audio_start_seconds=Decimal("0.000"),
                audio_end_seconds=duration,
                duration_seconds=duration,
                frame_count=int(duration * settings.generation_fps),
                price_usd=price,
            )
            session.add(segment)

            await BalanceService(session).freeze_balance_in_transaction(
                user_id=user.id,
                amount_usd=price,
                related_job_id=job.id,
                reason="Local debug mock generation job",
            )
            job_ids.append(job.id)

    for job_id in job_ids:
        enqueue_generation_job(str(job_id))

    return DebugCreateMockJobsResponse(
        telegram_id=telegram_id,
        job_ids=job_ids,
        status="queued",
    )


@router.post(
    "/users/{telegram_id}/repair-frozen-balances",
    response_model=DebugRepairFrozenBalancesResponse,
)
async def repair_debug_frozen_balances(
    telegram_id: int,
    session: SessionDep,
) -> DebugRepairFrozenBalancesResponse:
    _require_local_env()

    released_usd = Decimal("0.0000")
    captured_usd = Decimal("0.0000")
    repaired_job_ids: list[UUID] = []
    async with session.begin():
        user = await UserRepository().get_by_telegram_id(session, telegram_id)
        if user is None:
            raise AppError("User not found", code="user_not_found", status_code=404)

        account = await _get_or_create_locked_account(session, user.id)
        jobs = await _get_repair_candidate_jobs(session, user.id)
        transaction_map = await _get_transactions_by_job_id(
            session,
            user.id,
            [job.id for job in jobs],
        )

        for job in jobs:
            if job.price_usd is None:
                continue

            transactions = transaction_map.get(job.id, [])
            held = _sum_transaction_type(transactions, BalanceTransactionType.HOLD.value)
            captured = _sum_transaction_type(transactions, BalanceTransactionType.CAPTURE.value)
            returned = _sum_transaction_types(
                transactions,
                {
                    BalanceTransactionType.REFUND.value,
                    BalanceTransactionType.RELEASE.value,
                },
            )
            unsettled = _money(held - captured - returned)
            if unsettled <= Decimal("0"):
                continue

            if job.status == JobStatus.COMPLETED.value:
                amount = min(unsettled, account.frozen_usd)
                if amount <= Decimal("0"):
                    continue
                account.frozen_usd = _money(account.frozen_usd - amount)
                _add_repair_transaction(
                    session,
                    user_id=user.id,
                    job_id=job.id,
                    transaction_type=BalanceTransactionType.CAPTURE.value,
                    amount_usd=amount,
                    account=account,
                    reason="Local repair: capture completed generation job",
                )
                captured_usd = _money(captured_usd + amount)
            elif job.status in {JobStatus.FAILED.value, JobStatus.CANCELLED.value}:
                amount = min(unsettled, account.frozen_usd)
                if amount <= Decimal("0"):
                    continue
                account.frozen_usd = _money(account.frozen_usd - amount)
                account.available_usd = _money(account.available_usd + amount)
                _add_repair_transaction(
                    session,
                    user_id=user.id,
                    job_id=job.id,
                    transaction_type=BalanceTransactionType.RELEASE.value,
                    amount_usd=amount,
                    account=account,
                    reason="Local repair: release stale frozen generation funds",
                )
                released_usd = _money(released_usd + amount)
            else:
                continue

            repaired_job_ids.append(job.id)

    return DebugRepairFrozenBalancesResponse(
        telegram_id=telegram_id,
        released_usd=released_usd,
        captured_usd=captured_usd,
        repaired_job_ids=repaired_job_ids,
        balance=BalanceResponse(
            available_usd=account.available_usd,
            frozen_usd=account.frozen_usd,
        ),
    )


@router.get(
    "/users/{telegram_id}/balance-ledger",
    response_model=DebugBalanceLedgerResponse,
)
async def get_debug_balance_ledger(
    telegram_id: int,
    session: SessionDep,
) -> DebugBalanceLedgerResponse:
    _require_local_env()

    user = await UserRepository().get_by_telegram_id(session, telegram_id)
    if user is None:
        raise AppError("User not found", code="user_not_found", status_code=404)

    account_result = await session.execute(
        select(BalanceAccount).where(BalanceAccount.user_id == user.id)
    )
    account = account_result.scalar_one_or_none()
    transaction_result = await session.execute(
        select(BalanceTransaction)
        .where(BalanceTransaction.user_id == user.id)
        .order_by(BalanceTransaction.created_at.desc())
        .limit(20)
    )
    job_result = await session.execute(
        select(GenerationJob)
        .where(GenerationJob.user_id == user.id)
        .order_by(GenerationJob.created_at.desc())
        .limit(10)
    )

    return DebugBalanceLedgerResponse(
        telegram_id=telegram_id,
        balance=BalanceResponse(
            available_usd=account.available_usd if account else Decimal("0.0000"),
            frozen_usd=account.frozen_usd if account else Decimal("0.0000"),
        ),
        transactions=[
            DebugLedgerTransactionResponse(
                id=transaction.id,
                type=transaction.type,
                amount_usd=transaction.amount_usd,
                balance_available_after=transaction.balance_available_after,
                balance_frozen_after=transaction.balance_frozen_after,
                reason=transaction.reason,
                generation_job_id=transaction.generation_job_id,
                payment_id=transaction.payment_id,
                created_at=transaction.created_at,
            )
            for transaction in transaction_result.scalars()
        ],
        jobs=[
            DebugLedgerJobResponse(
                id=job.id,
                status=job.status,
                price_usd=job.price_usd,
                error_message=job.error_message,
                mock_result_message=job.mock_result_message,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
            for job in job_result.scalars()
        ],
    )


@router.get(
    "/generation/jobs/{job_id}/segments",
    response_model=DebugGenerationJobSegmentsResponse,
)
async def get_debug_generation_job_segments(
    job_id: UUID,
    session: SessionDep,
) -> DebugGenerationJobSegmentsResponse:
    _require_local_env()

    result = await session.execute(
        select(GenerationSegment)
        .where(GenerationSegment.job_id == job_id)
        .order_by(GenerationSegment.segment_index)
    )
    return DebugGenerationJobSegmentsResponse(
        job_id=job_id,
        segments=[
            DebugGenerationSegmentResponse(
                segment_index=segment.segment_index,
                status=segment.status,
                audio_start_seconds=segment.audio_start_seconds,
                audio_end_seconds=segment.audio_end_seconds,
                duration_seconds=segment.duration_seconds,
                frame_count=segment.frame_count,
                error_message=segment.error_message,
            )
            for segment in result.scalars()
        ],
    )


@router.post("/audio/segment-plan", response_model=DebugAudioSegmentPlanResponse)
async def preview_debug_audio_segment_plan(
    audio: Annotated[UploadFile, File(...)],
) -> DebugAudioSegmentPlanResponse:
    _require_local_env()

    settings = get_settings()
    content = await audio.read()
    max_bytes = settings.max_audio_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise AppError(
            f"Файл слишком большой. Максимум {settings.max_audio_size_mb} MB.",
            code="audio_too_large",
            status_code=400,
        )

    suffix = Path(audio.filename or "audio").suffix or ".audio"
    temp_dir = Path(settings.local_storage_dir) / "temp" / "debug_audio"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{uuid.uuid4().hex}{suffix}"
    temp_path.write_bytes(content)
    try:
        plan = await anyio.to_thread.run_sync(
            lambda: WorkerAudioService().build_segment_plan(
                input_audio_path=temp_path,
                max_segment_seconds=settings.generation_max_segment_seconds,
                strategy=settings.audio_segmentation_strategy,
                silence_threshold_db=settings.audio_silence_threshold_db,
                silence_min_duration_seconds=settings.audio_silence_min_duration_seconds,
                silence_search_window_seconds=settings.audio_silence_search_window_seconds,
                segment_min_seconds=settings.audio_segment_min_seconds,
            )
        )
    finally:
        temp_path.unlink(missing_ok=True)

    return DebugAudioSegmentPlanResponse(
        strategy=plan.strategy,
        duration_seconds=plan.total_duration_seconds,
        silences_found=len(plan.silences),
        segments=[
            DebugAudioSegmentPlanItemResponse(
                index=boundary.segment_index,
                start=boundary.start_seconds,
                end=boundary.end_seconds,
                duration=boundary.duration_seconds,
                reason=boundary.reason,
            )
            for boundary in plan.boundaries
        ],
    )


@router.post("/storage/test-upload", response_model=DebugStorageTestUploadResponse)
async def test_storage_upload(
    payload: DebugStorageTestUploadRequest,
    session: SessionDep,
) -> DebugStorageTestUploadResponse:
    _require_local_env()

    async with session.begin():
        storage = StorageServiceFactory(session).create()
        uploaded_file = await storage.save_bytes(
            user_id=None,
            file_type=FileType.VIDEO,
            original_filename="debug-storage-test.txt",
            content=payload.content.encode(),
            mime_type="text/plain",
        )
        exists = await storage.exists(uploaded_file)
        download_url = storage.get_download_url(uploaded_file)

    return DebugStorageTestUploadResponse(
        storage_provider=uploaded_file.storage_provider,
        file_id=uploaded_file.id,
        storage_key=uploaded_file.storage_key,
        download_url=download_url,
        exists=exists,
    )


@router.delete("/storage/files/{file_id}", response_model=DebugStorageDeleteResponse)
async def delete_debug_storage_file(
    file_id: UUID,
    session: SessionDep,
) -> DebugStorageDeleteResponse:
    _require_local_env()

    async with session.begin():
        result = await session.execute(select(UploadedFile).where(UploadedFile.id == file_id))
        uploaded_file = result.scalar_one_or_none()
        if uploaded_file is None:
            raise AppError("File not found", code="file_not_found", status_code=404)

        storage = StorageServiceFactory(session).create_for_uploaded_file(uploaded_file)
        await storage.delete(uploaded_file)
        await session.delete(uploaded_file)

    return DebugStorageDeleteResponse(file_id=file_id, deleted=True)


@router.post("/storage/cleanup", response_model=DebugStorageCleanupResponse)
async def cleanup_debug_storage(session: SessionDep) -> DebugStorageCleanupResponse:
    _require_local_env()

    async with session.begin():
        deleted_count = await FileCleanupService(session).cleanup_expired_files()

    return DebugStorageCleanupResponse(deleted_count=deleted_count)


@router.post(
    "/telegram/test-notification",
    response_model=DebugTelegramTestNotificationResponse,
)
async def send_debug_telegram_notification(
    payload: DebugTelegramTestNotificationRequest,
) -> DebugTelegramTestNotificationResponse:
    _require_local_env()

    ok = await TelegramNotificationService().send_message(
        telegram_id=payload.telegram_id,
        message=payload.message,
    )
    return DebugTelegramTestNotificationResponse(ok=ok)


@router.get("/runpod/pods", response_model=DebugRunPodPodsResponse)
async def list_debug_runpod_pods(session: SessionDep) -> DebugRunPodPodsResponse:
    _require_local_env()

    result = await session.execute(select(RunpodPod).order_by(RunpodPod.created_at.desc()))
    return DebugRunPodPodsResponse(pods=[_runpod_pod_response(pod) for pod in result.scalars()])


@router.post("/runpod/create-pod", response_model=DebugRunPodCreatePodResponse)
async def create_debug_runpod_pod(session: SessionDep) -> DebugRunPodCreatePodResponse:
    _require_local_env()

    settings = get_settings()
    if not settings.runpod_auto_manager_enabled:
        raise AppError(
            "RunPod manager is not configured", code="runpod_not_configured", status_code=400
        )

    try:
        create_result = await anyio.to_thread.run_sync(
            lambda: _create_runpod_pod_with_fallback(settings)
        )
    except RunPodError as exc:
        raise AppError(str(exc), code="runpod_create_failed", status_code=502) from exc
    except HTTPException:
        raise

    async with session.begin():
        info = create_result["info"]
        pod = RunpodPod(
            provider_pod_id=info.pod_id,
            runpod_pod_id=info.pod_id,
            name=info.name,
            status="starting",
            cloud_type=settings.runpod_cloud_type,
            gpu_type=info.gpu_type,
            template_id=settings.runpod_template_id,
            base_url=info.base_url,
            comfyui_url=info.base_url,
            comfyui_port=settings.runpod_comfyui_port,
        )
        session.add(pod)
        await session.flush()

    return DebugRunPodCreatePodResponse(
        pod=_runpod_pod_response(pod),
        selected_gpu_type=create_result["selected_gpu_type"],
        selected_min_ram_gb=create_result["selected_min_ram_gb"],
        selected_resource_phase=create_result["selected_resource_phase"],
        attempt=create_result["attempt"],
        tried_gpu_types=create_result["tried_gpu_types"],
    )


@router.delete("/runpod/pods/{runpod_pod_id}", response_model=DebugRunPodDeleteResponse)
async def delete_debug_runpod_pod(
    runpod_pod_id: str,
    session: SessionDep,
) -> DebugRunPodDeleteResponse:
    _require_local_env()

    settings = get_settings()
    if not settings.runpod_auto_manager_enabled:
        raise AppError(
            "RunPod manager is not configured", code="runpod_not_configured", status_code=400
        )

    try:
        await anyio.to_thread.run_sync(lambda: _terminate_runpod_pod(settings, runpod_pod_id))
    except RunPodError as exc:
        if "HTTP 404" not in str(exc) and "not found" not in str(exc).lower():
            raise AppError(str(exc), code="runpod_terminate_failed", status_code=502) from exc
        logger.warning(
            "RunPod debug delete did not find remote pod, continuing local cleanup pod_id=%s",
            runpod_pod_id,
        )

    active_job_id: UUID | None = None
    async with session.begin():
        result = await session.execute(
            select(RunpodPod).where(RunpodPod.runpod_pod_id == runpod_pod_id)
        )
        pod = result.scalar_one_or_none()
        if pod is not None:
            active_job_id = pod.active_job_id or pod.current_job_id
            now = datetime.now(UTC)
            pod.status = "terminated"
            pod.active_job_id = None
            pod.current_job_id = None
            pod.terminated_at = now
            pod.updated_at = now

    if active_job_id is not None:
        await _fail_generation_job_with_refund(
            session=session,
            job_id=active_job_id,
            error_message="RunPod pod was terminated during generation",
            notify=True,
        )

    return DebugRunPodDeleteResponse(runpod_pod_id=runpod_pod_id, terminated=True)


@router.post(
    "/generation/jobs/{job_id}/fail-refund",
    response_model=DebugFailRefundGenerationJobResponse,
)
async def fail_refund_debug_generation_job(
    job_id: UUID,
    session: SessionDep,
) -> DebugFailRefundGenerationJobResponse:
    _require_local_env()

    result = await _fail_generation_job_with_refund(
        session=session,
        job_id=job_id,
        error_message="Generation manually failed by debug endpoint",
        notify=True,
    )
    return DebugFailRefundGenerationJobResponse(**result)


@router.post("/generation/retry-waiting-gpu", response_model=DebugRetryWaitingGpuResponse)
async def retry_debug_waiting_gpu_jobs(
    session: SessionDep,
    limit: int = 20,
) -> DebugRetryWaitingGpuResponse:
    _require_local_env()

    settings = get_settings()
    now = datetime.now(UTC)
    safe_limit = max(1, min(limit, 100))
    async with session.begin():
        result = await session.execute(
            select(GenerationJob)
            .where(
                GenerationJob.status == JobStatus.WAITING_FOR_GPU.value,
                or_(GenerationJob.next_retry_at.is_(None), GenerationJob.next_retry_at <= now),
            )
            .order_by(
                GenerationJob.next_retry_at.asc().nullsfirst(), GenerationJob.created_at.asc()
            )
            .limit(safe_limit)
            .with_for_update(skip_locked=True)
        )
        jobs = list(result.scalars())
        job_ids = [job.id for job in jobs]
        next_retry_at = now + timedelta(seconds=max(settings.runpod_waiting_gpu_retry_seconds, 1))
        for job in jobs:
            job.status = JobStatus.QUEUED.value
            job.queued_at = now
            job.next_retry_at = next_retry_at

    for job_id in job_ids:
        enqueue_generation_job(str(job_id))

    return DebugRetryWaitingGpuResponse(enqueued=len(job_ids), job_ids=job_ids)


@router.get("/generation/jobs", response_model=DebugGenerationJobsResponse)
async def list_debug_generation_jobs(
    session: SessionDep,
    limit: int = 20,
) -> DebugGenerationJobsResponse:
    _require_local_env()

    safe_limit = max(1, min(limit, 100))
    result = await session.execute(
        select(GenerationJob).order_by(GenerationJob.created_at.desc()).limit(safe_limit)
    )
    jobs = list(result.scalars())
    job_ids = [job.id for job in jobs]
    ledger_flags = await _get_job_ledger_flags(session, job_ids)
    runpod_map = await _get_runpod_by_job_id(session, job_ids)

    items: list[DebugGenerationJobListItemResponse] = []
    for job in jobs:
        ledger = ledger_flags.get(job.id, {})
        pod = runpod_map.get(job.id)
        items.append(
            DebugGenerationJobListItemResponse(
                id=job.id,
                status=job.status,
                created_at=job.created_at,
                updated_at=job.updated_at,
                price_usd=job.price_usd,
                cost_usd=job.cost_usd,
                waiting_for_gpu_since=job.waiting_for_gpu_since,
                next_retry_at=job.next_retry_at,
                refunded=ledger.get("refunded", False),
                captured=ledger.get("captured", False),
                runpod_pod_id=pod.runpod_pod_id if pod else None,
                runpod_base_url=pod.base_url or pod.comfyui_url if pod else None,
            )
        )

    return DebugGenerationJobsResponse(items=items)


@router.post("/runpod/cleanup-idle", response_model=DebugRunPodCleanupResponse)
async def cleanup_debug_runpod_idle_pods(
    session: SessionDep,
    force: bool = False,
) -> DebugRunPodCleanupResponse:
    _require_local_env()

    settings = get_settings()
    if not settings.runpod_auto_manager_enabled:
        raise AppError(
            "RunPod manager is not configured", code="runpod_not_configured", status_code=400
        )

    cutoff = datetime.now(UTC)
    if not force:
        cutoff -= timedelta(minutes=settings.runpod_pod_idle_shutdown_minutes)

    statement = select(RunpodPod).where(
        RunpodPod.status.in_(["idle", "ready"]),
        RunpodPod.active_job_id.is_(None),
    )
    if not force:
        statement = statement.where(
            RunpodPod.last_used_at.is_not(None), RunpodPod.last_used_at < cutoff
        )

    result = await session.execute(statement)
    pods = list(result.scalars())
    await session.commit()
    terminated: list[str] = []
    for pod in pods:
        try:
            await anyio.to_thread.run_sync(
                lambda pod_id=pod.runpod_pod_id: _terminate_runpod_pod(settings, pod_id)
            )
        except RunPodError:
            continue
        async with session.begin():
            now = datetime.now(UTC)
            pod.status = "terminated"
            pod.active_job_id = None
            pod.current_job_id = None
            pod.terminated_at = now
            pod.updated_at = now
            terminated.append(pod.runpod_pod_id)

    return DebugRunPodCleanupResponse(terminated_count=len(terminated), pod_ids=terminated)


@router.post("/runpod/keeper-tick", response_model=DebugRunPodKeeperTickResponse)
async def run_debug_runpod_keeper_tick() -> DebugRunPodKeeperTickResponse:
    _require_local_env()

    settings = get_settings()
    result = await anyio.to_thread.run_sync(lambda: RunPodKeeper(settings).tick())
    requeued_waiting_jobs = result.requeued_waiting_jobs
    if result.should_enqueue_waiting_retry:
        try:
            enqueue_retry_waiting_for_gpu_jobs()
            requeued_waiting_jobs = 0
        except Exception:
            logger.warning("Debug RunPod keeper could not enqueue waiting GPU retry")

    return DebugRunPodKeeperTickResponse(
        enabled=result.enabled,
        active_pods=result.active_pods,
        terminated_idle_pods=result.terminated_idle_pods,
        created_warm_pod=result.created_warm_pod,
        requeued_waiting_jobs=requeued_waiting_jobs,
    )


@router.get("/comfyui/health", response_model=DebugComfyUIHealthResponse)
async def get_debug_comfyui_health() -> DebugComfyUIHealthResponse:
    _require_local_env()

    settings = get_settings()
    base_url = settings.comfyui_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=True,
        ) as client:
            response = await client.get("/system_stats")
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        raise AppError(
            "ComfyUI healthcheck failed", code="comfyui_unavailable", status_code=502
        ) from exc
    except ValueError as exc:
        raise AppError(
            "ComfyUI returned invalid JSON", code="comfyui_invalid_response", status_code=502
        ) from exc

    summary = _extract_comfyui_health_summary(data)
    return DebugComfyUIHealthResponse(
        ok=True,
        base_url=base_url,
        device=summary.get("device"),
        vram_free=summary.get("vram_free"),
    )


@router.post(
    "/comfyui/validate-workflow",
    response_model=DebugComfyUIValidateWorkflowResponse,
)
async def validate_debug_comfyui_workflow() -> DebugComfyUIValidateWorkflowResponse:
    _require_local_env()

    settings = get_settings()
    nodes = _load_workflow_node_values(Path(settings.comfyui_workflow_path))
    return DebugComfyUIValidateWorkflowResponse(nodes=nodes)


@router.post(
    "/comfyui/patch-workflow-preview",
    response_model=DebugComfyUIPatchWorkflowPreviewResponse,
)
async def preview_debug_comfyui_workflow_patch(
    payload: DebugComfyUIPatchWorkflowPreviewRequest,
) -> DebugComfyUIPatchWorkflowPreviewResponse:
    _require_local_env()

    settings = get_settings()
    try:
        result = preview_infinite_talk_patch_values(
            workflow_path=Path(settings.comfyui_workflow_path),
            image_filename=payload.image_filename,
            audio_filename=payload.audio_filename,
            width=payload.width,
            height=payload.height,
            fps=payload.fps,
            frame_count=payload.frame_count,
            input_subfolder=settings.comfyui_input_subfolder,
            output_subfolder=settings.comfyui_output_subfolder,
        )
    except FileNotFoundError as exc:
        raise AppError(
            "Workflow file not found", code="workflow_not_found", status_code=404
        ) from exc
    except (json.JSONDecodeError, WorkflowPatchError) as exc:
        raise AppError(str(exc), code="workflow_invalid", status_code=400) from exc

    return DebugComfyUIPatchWorkflowPreviewResponse(nodes=result["nodes"])


def _create_runpod_pod_with_fallback(settings) -> dict[str, Any]:
    client = RunPodClient(settings)
    try:
        tried_gpu_types: list[DebugRunPodGpuAttemptResponse] = []
        max_attempts = max(settings.runpod_create_max_attempts, 1)
        sleep_seconds = max(settings.runpod_create_retry_sleep_seconds, 0)

        for phase, min_ram_gb in _runpod_create_resource_phases(settings):
            logger.info(
                "RunPod create phase started phase=%s min_ram_gb=%s",
                phase,
                min_ram_gb,
            )
            for attempt in range(1, max_attempts + 1):
                logger.info(
                    "RunPod create attempt started phase=%s attempt=%s "
                    "max_attempts=%s min_ram_gb=%s",
                    phase,
                    attempt,
                    max_attempts,
                    min_ram_gb,
                )
                for gpu_type in settings.runpod_allowed_gpu_type_list:
                    logger.info(
                        "RunPod debug create-pod requested gpu_type=%s min_ram_gb=%s " "phase=%s",
                        gpu_type,
                        min_ram_gb,
                        phase,
                    )
                    try:
                        info = client.create_pod(gpu_type, min_ram_gb=min_ram_gb)
                    except RunPodCapacityError as exc:
                        error = _short_error(exc)
                        logger.warning(
                            "RunPod debug create-pod capacity unavailable phase=%s "
                            "gpu_type=%s min_ram_gb=%s attempt=%s error=%s",
                            phase,
                            gpu_type,
                            min_ram_gb,
                            attempt,
                            error,
                        )
                        tried_gpu_types.append(
                            DebugRunPodGpuAttemptResponse(
                                phase=phase,
                                attempt=attempt,
                                gpu_type=gpu_type,
                                min_ram_gb=min_ram_gb,
                                status="capacity_unavailable",
                                error=error,
                            )
                        )
                        continue

                    logger.info(
                        "RunPod pod created gpu_type=%s min_ram_gb=%s phase=%s",
                        info.gpu_type or gpu_type,
                        min_ram_gb,
                        phase,
                    )
                    tried_gpu_types.append(
                        DebugRunPodGpuAttemptResponse(
                            phase=phase,
                            attempt=attempt,
                            gpu_type=gpu_type,
                            min_ram_gb=min_ram_gb,
                            status="created",
                            error=None,
                        )
                    )
                    return {
                        "info": info,
                        "selected_gpu_type": info.gpu_type or gpu_type,
                        "selected_min_ram_gb": min_ram_gb,
                        "selected_resource_phase": phase,
                        "attempt": attempt,
                        "tried_gpu_types": tried_gpu_types,
                    }

                if attempt < max_attempts:
                    logger.warning(
                        "RunPod retrying create after capacity errors phase=%s " "sleep_seconds=%s",
                        phase,
                        sleep_seconds,
                    )
                    time.sleep(sleep_seconds)

            logger.warning(
                "RunPod create phase exhausted phase=%s min_ram_gb=%s",
                phase,
                min_ram_gb,
            )

        if tried_gpu_types:
            logger.warning("RunPod create attempts exhausted")
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "No RunPod instances available for configured GPU types",
                    "attempts": max_attempts,
                    "retry_sleep_seconds": sleep_seconds,
                    "tried_gpu_types": [attempt.model_dump() for attempt in tried_gpu_types],
                },
            )
        raise RunPodError("No RunPod GPU types configured")
    finally:
        client.close()


def _runpod_create_resource_phases(settings) -> list[tuple[str, int]]:
    phases = [("primary", settings.runpod_min_ram_gb)]
    if settings.runpod_ram_fallback_enabled and settings.runpod_fallback_min_ram_gb is not None:
        phases.append(("fallback", settings.runpod_fallback_min_ram_gb))
    return phases


def _short_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:500]


async def _fail_generation_job_with_refund(
    *,
    session: AsyncSession,
    job_id: UUID,
    error_message: str,
    notify: bool,
) -> dict[str, Any]:
    refunded = False
    notification_sent = False
    telegram_id: int | None = None
    notification_message: str | None = None

    async with session.begin():
        result = await session.execute(
            select(GenerationJob)
            .options(selectinload(GenerationJob.user))
            .where(GenerationJob.id == job_id)
            .with_for_update()
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise AppError("Generation job not found", code="job_not_found", status_code=404)

        old_status = job.status
        if old_status == JobStatus.COMPLETED.value:
            return {
                "job_id": job.id,
                "old_status": old_status,
                "new_status": old_status,
                "refunded": False,
                "notification_sent": False,
                "error_message": job.error_message,
            }

        totals = await _get_job_transaction_totals(session, job.id)
        held = totals.get(BalanceTransactionType.HOLD.value, Decimal("0.0000"))
        captured = totals.get(BalanceTransactionType.CAPTURE.value, Decimal("0.0000"))
        returned = _money(
            totals.get(BalanceTransactionType.REFUND.value, Decimal("0.0000"))
            + totals.get(BalanceTransactionType.RELEASE.value, Decimal("0.0000"))
        )
        if captured > Decimal("0"):
            return {
                "job_id": job.id,
                "old_status": old_status,
                "new_status": old_status,
                "refunded": False,
                "notification_sent": False,
                "error_message": job.error_message,
            }

        if old_status not in FAIL_REFUND_ALLOWED_JOB_STATUSES:
            return {
                "job_id": job.id,
                "old_status": old_status,
                "new_status": old_status,
                "refunded": False,
                "notification_sent": False,
                "error_message": job.error_message,
            }

        unsettled_hold = _money(held - captured - returned)
        refund_error: str | None = None
        if unsettled_hold > Decimal("0"):
            try:
                await BalanceService(session).refund_frozen_balance_in_transaction(
                    user_id=job.user_id,
                    amount_usd=unsettled_hold,
                    related_job_id=job.id,
                    reason=error_message,
                )
                refunded = True
            except AppError as exc:
                refund_error = exc.message
                logger.warning(
                    "Debug fail-refund could not refund generation job job_id=%s error=%s",
                    job.id,
                    refund_error,
                )

        now = datetime.now(UTC)
        job.status = JobStatus.FAILED.value
        job.error_message = (
            f"{error_message}; refund failed: {refund_error}" if refund_error else error_message
        )
        job.completed_at = now

        segments_result = await session.execute(
            select(GenerationSegment).where(GenerationSegment.job_id == job.id)
        )
        for segment in segments_result.scalars():
            if segment.status != SegmentStatus.COMPLETED.value:
                segment.status = SegmentStatus.FAILED.value
                segment.error_message = job.error_message
                segment.completed_at = now

        telegram_id = job.user.telegram_id
        balance_line = (
            "Средства возвращены на баланс."
            if refunded
            else "Средства не списывались или уже были возвращены."
        )
        notification_message = (
            "❌ Генерация не удалась\n\n"
            f"ID: {str(job_id)[:8]}\n"
            f"{balance_line}\n"
            f"Причина: {job.error_message}"
        )
        response = {
            "job_id": job.id,
            "old_status": old_status,
            "new_status": job.status,
            "refunded": refunded,
            "notification_sent": False,
            "error_message": job.error_message,
        }

    if notify and telegram_id is not None and notification_message is not None:
        try:
            notification_sent = await TelegramNotificationService().send_message(
                telegram_id=telegram_id,
                message=notification_message,
            )
        except Exception:
            logger.warning("Debug fail-refund notification failed job_id=%s", job_id)

    response["notification_sent"] = notification_sent
    return response


async def _get_job_transaction_totals(
    session: AsyncSession,
    job_id: UUID,
) -> dict[str, Decimal]:
    result = await session.execute(
        select(BalanceTransaction).where(BalanceTransaction.generation_job_id == job_id)
    )
    totals: dict[str, Decimal] = {}
    for transaction in result.scalars():
        totals[transaction.type] = _money(
            totals.get(transaction.type, Decimal("0.0000")) + abs(transaction.amount_usd)
        )
    return totals


async def _get_job_ledger_flags(
    session: AsyncSession,
    job_ids: list[UUID],
) -> dict[UUID, dict[str, bool]]:
    if not job_ids:
        return {}

    result = await session.execute(
        select(BalanceTransaction).where(BalanceTransaction.generation_job_id.in_(job_ids))
    )
    flags: dict[UUID, dict[str, bool]] = {
        job_id: {"captured": False, "refunded": False} for job_id in job_ids
    }
    for transaction in result.scalars():
        job_id = transaction.generation_job_id
        if job_id is None:
            continue
        item = flags.setdefault(job_id, {"captured": False, "refunded": False})
        if transaction.type == BalanceTransactionType.CAPTURE.value:
            item["captured"] = True
        elif transaction.type in {
            BalanceTransactionType.REFUND.value,
            BalanceTransactionType.RELEASE.value,
        }:
            item["refunded"] = True
    return flags


async def _get_runpod_by_job_id(
    session: AsyncSession,
    job_ids: list[UUID],
) -> dict[UUID, RunpodPod]:
    if not job_ids:
        return {}

    result = await session.execute(
        select(RunpodPod)
        .where(
            or_(
                RunpodPod.active_job_id.in_(job_ids),
                RunpodPod.current_job_id.in_(job_ids),
            )
        )
        .order_by(RunpodPod.updated_at.desc())
    )
    pods_by_job_id: dict[UUID, RunpodPod] = {}
    for pod in result.scalars():
        for job_id in {pod.active_job_id, pod.current_job_id}:
            if job_id is not None and job_id not in pods_by_job_id:
                pods_by_job_id[job_id] = pod
    return pods_by_job_id


def _terminate_runpod_pod(settings, runpod_pod_id: str) -> None:
    client = RunPodClient(settings)
    try:
        client.terminate_pod(runpod_pod_id)
    finally:
        client.close()


def _runpod_pod_response(pod: RunpodPod) -> DebugRunPodPodResponse:
    return DebugRunPodPodResponse(
        id=pod.id,
        runpod_pod_id=pod.runpod_pod_id,
        provider_pod_id=pod.provider_pod_id,
        name=pod.name,
        status=pod.status,
        cloud_type=pod.cloud_type,
        gpu_type=pod.gpu_type,
        template_id=pod.template_id,
        base_url=pod.base_url or pod.comfyui_url,
        comfyui_port=pod.comfyui_port,
        active_job_id=pod.active_job_id or pod.current_job_id,
        error_message=pod.error_message,
        last_healthcheck_at=pod.last_healthcheck_at or pod.last_heartbeat_at,
        last_used_at=pod.last_used_at or pod.last_busy_at,
        created_at=pod.created_at,
        updated_at=pod.updated_at,
        terminated_at=pod.terminated_at,
    )


def _require_local_env() -> None:
    settings = get_settings()
    if settings.app_env != "local":
        raise AppError(
            "Debug endpoint is available only in local env", code="not_found", status_code=404
        )


def _load_workflow_node_values(workflow_path: Path) -> dict[str, Any]:
    try:
        result = validate_infinite_talk_workflow(workflow_path)
    except FileNotFoundError as exc:
        raise AppError(
            "Workflow file not found", code="workflow_not_found", status_code=404
        ) from exc
    except (json.JSONDecodeError, WorkflowPatchError) as exc:
        raise AppError(str(exc), code="workflow_invalid", status_code=400) from exc
    return result["nodes"]


def _extract_comfyui_health_summary(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"device": None, "vram_free": None}

    devices = data.get("devices")
    if not isinstance(devices, list) or not devices:
        return {"device": None, "vram_free": None}

    first_device = devices[0]
    if not isinstance(first_device, dict):
        return {"device": None, "vram_free": None}

    device = first_device.get("name") or first_device.get("device_name")
    if not isinstance(device, str):
        device = None
    vram_free = first_device.get("vram_free") or first_device.get("free_memory")
    if not isinstance(vram_free, (int, float)):  # noqa: UP038
        vram_free = None

    return {
        "device": device,
        "vram_free": vram_free,
    }


async def _get_or_create_locked_account(
    session: AsyncSession,
    user_id: UUID,
) -> BalanceAccount:
    await session.execute(
        insert(BalanceAccount)
        .values(user_id=user_id)
        .on_conflict_do_nothing(index_elements=[BalanceAccount.user_id])
    )
    result = await session.execute(
        select(BalanceAccount).where(BalanceAccount.user_id == user_id).with_for_update()
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise AppError("Balance account was not created", code="balance_account_missing")
    return account


async def _get_repair_candidate_jobs(
    session: AsyncSession,
    user_id: UUID,
) -> list[GenerationJob]:
    result = await session.execute(
        select(GenerationJob).where(
            GenerationJob.user_id == user_id,
            GenerationJob.status.in_(
                {
                    JobStatus.COMPLETED.value,
                    JobStatus.FAILED.value,
                    JobStatus.CANCELLED.value,
                }
            ),
            GenerationJob.price_usd.is_not(None),
        )
    )
    return list(result.scalars())


async def _get_transactions_by_job_id(
    session: AsyncSession,
    user_id: UUID,
    job_ids: list[UUID],
) -> dict[UUID, list[BalanceTransaction]]:
    if not job_ids:
        return {}

    result = await session.execute(
        select(BalanceTransaction).where(
            BalanceTransaction.user_id == user_id,
            BalanceTransaction.generation_job_id.in_(job_ids),
        )
    )
    transactions_by_job_id: dict[UUID, list[BalanceTransaction]] = {}
    for transaction in result.scalars():
        if transaction.generation_job_id is None:
            continue
        transactions_by_job_id.setdefault(transaction.generation_job_id, []).append(transaction)
    return transactions_by_job_id


def _sum_transaction_type(
    transactions: list[BalanceTransaction],
    transaction_type: str,
) -> Decimal:
    return _sum_transaction_types(transactions, {transaction_type})


def _sum_transaction_types(
    transactions: list[BalanceTransaction],
    transaction_types: set[str],
) -> Decimal:
    total = Decimal("0.0000")
    for transaction in transactions:
        if transaction.type in transaction_types:
            total += abs(transaction.amount_usd)
    return _money(total)


def _add_repair_transaction(
    session: AsyncSession,
    *,
    user_id: UUID,
    job_id: UUID,
    transaction_type: str,
    amount_usd: Decimal,
    account: BalanceAccount,
    reason: str,
) -> None:
    session.add(
        BalanceTransaction(
            user_id=user_id,
            generation_job_id=job_id,
            type=transaction_type,
            amount_usd=_money(amount_usd),
            balance_available_after=account.available_usd,
            balance_frozen_after=account.frozen_usd,
            reason=reason,
        )
    )


def _money(amount: Decimal) -> Decimal:
    return amount.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
