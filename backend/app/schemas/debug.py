from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from backend.app.schemas.users import BalanceResponse


class DebugTaskResponse(BaseModel):
    task_id: str
    status: str


class DebugAddBalanceRequest(BaseModel):
    amount_usd: Decimal
    reason: str = "local test"


class DebugBalanceResponse(BaseModel):
    telegram_id: int
    balance: BalanceResponse


class DebugCreateMockJobsRequest(BaseModel):
    count: int = Field(default=3, ge=1, le=10)
    duration_seconds: Decimal = Field(default=Decimal("1.000"), gt=Decimal("0"))
    width: int = 480
    height: int = 480


class DebugCreateMockJobsResponse(BaseModel):
    telegram_id: int
    job_ids: list[UUID]
    status: str


class DebugRepairFrozenBalancesResponse(BaseModel):
    telegram_id: int
    released_usd: Decimal
    captured_usd: Decimal
    repaired_job_ids: list[UUID]
    balance: BalanceResponse


class DebugLedgerTransactionResponse(BaseModel):
    id: UUID
    type: str
    amount_usd: Decimal
    balance_available_after: Decimal
    balance_frozen_after: Decimal
    reason: str | None
    generation_job_id: UUID | None
    payment_id: UUID | None
    created_at: datetime


class DebugLedgerJobResponse(BaseModel):
    id: UUID
    status: str
    price_usd: Decimal | None
    error_message: str | None
    mock_result_message: str | None
    created_at: datetime
    updated_at: datetime


class DebugBalanceLedgerResponse(BaseModel):
    telegram_id: int
    balance: BalanceResponse
    transactions: list[DebugLedgerTransactionResponse]
    jobs: list[DebugLedgerJobResponse]


class DebugStorageTestUploadRequest(BaseModel):
    content: str = "hello r2"


class DebugStorageTestUploadResponse(BaseModel):
    storage_provider: str
    file_id: UUID
    storage_key: str
    download_url: str | None
    exists: bool


class DebugStorageDeleteResponse(BaseModel):
    file_id: UUID
    deleted: bool


class DebugStorageCleanupResponse(BaseModel):
    deleted_count: int


class DebugComfyUIHealthResponse(BaseModel):
    ok: bool
    base_url: str
    device: str | None = None
    vram_free: int | float | None = None


class DebugComfyUIValidateWorkflowResponse(BaseModel):
    nodes: dict[str, Any]


class DebugComfyUIPatchWorkflowPreviewRequest(BaseModel):
    image_filename: str = "test.png"
    audio_filename: str = "test.mp3"
    width: int = 480
    height: int = 480
    fps: int = 25
    frame_count: int = 250


class DebugComfyUIPatchWorkflowPreviewResponse(BaseModel):
    nodes: dict[str, Any]


class DebugTelegramTestNotificationRequest(BaseModel):
    telegram_id: int = Field(gt=0)
    message: str = "test notification"


class DebugTelegramTestNotificationResponse(BaseModel):
    ok: bool


class DebugGenerationSegmentResponse(BaseModel):
    segment_index: int
    status: str
    audio_start_seconds: Decimal
    audio_end_seconds: Decimal
    duration_seconds: Decimal
    frame_count: int
    error_message: str | None


class DebugGenerationJobSegmentsResponse(BaseModel):
    job_id: UUID
    segments: list[DebugGenerationSegmentResponse]


class DebugAudioSegmentPlanItemResponse(BaseModel):
    index: int
    start: Decimal
    end: Decimal
    duration: Decimal
    reason: str


class DebugAudioSegmentPlanResponse(BaseModel):
    strategy: str
    duration_seconds: Decimal
    silences_found: int
    segments: list[DebugAudioSegmentPlanItemResponse]


class DebugRunPodPodResponse(BaseModel):
    id: UUID
    runpod_pod_id: str
    provider_pod_id: str
    name: str | None
    status: str
    cloud_type: str | None
    gpu_type: str | None
    template_id: str | None
    base_url: str | None
    comfyui_port: int | None
    active_job_id: UUID | None
    error_message: str | None
    last_healthcheck_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime
    updated_at: datetime
    terminated_at: datetime | None


class DebugRunPodPodsResponse(BaseModel):
    pods: list[DebugRunPodPodResponse]


class DebugRunPodGpuAttemptResponse(BaseModel):
    phase: str
    attempt: int
    gpu_type: str
    min_ram_gb: int
    status: str
    error: str | None = None


class DebugRunPodCreatePodResponse(BaseModel):
    pod: DebugRunPodPodResponse
    selected_gpu_type: str
    selected_min_ram_gb: int | None = None
    selected_resource_phase: str | None = None
    attempt: int
    tried_gpu_types: list[DebugRunPodGpuAttemptResponse]


class DebugRunPodDeleteResponse(BaseModel):
    runpod_pod_id: str
    terminated: bool


class DebugRunPodCleanupResponse(BaseModel):
    terminated_count: int
    pod_ids: list[str]


class DebugRunPodAutoscalingPlanResponse(BaseModel):
    enabled: bool
    strategy: str
    pending_jobs: int
    pending_gpu_minutes: Decimal
    target_queue_wait_minutes: int
    active_pods: int
    busy_pods: int
    idle_pods: int
    max_active_pods: int
    min_warm_pods: int
    estimated_pod_hourly_cost_usd: Decimal
    max_estimated_hourly_cost_usd: Decimal
    desired_active_pods: int
    pods_to_create: int
    pods_to_terminate: int
    reason: str


class DebugRunPodKeeperTickResponse(BaseModel):
    enabled: bool
    autoscaling: DebugRunPodAutoscalingPlanResponse | None = None
    active_pods: int
    busy_pods: int | None = None
    idle_pods: int | None = None
    pending_jobs: int | None = None
    desired_active_pods: int | None = None
    terminated_idle_pods: list[str]
    created_warm_pod: str | None
    created_warm_pods: list[str] = Field(default_factory=list)
    requeued_waiting_jobs: int | None = None


class DebugFailRefundGenerationJobResponse(BaseModel):
    job_id: UUID
    old_status: str
    new_status: str
    refunded: bool
    notification_sent: bool
    error_message: str | None


class DebugGenerationJobListItemResponse(BaseModel):
    id: UUID
    status: str
    created_at: datetime
    updated_at: datetime
    price_usd: Decimal | None
    cost_usd: Decimal | None
    waiting_for_gpu_since: datetime | None
    waiting_for_pod_since: datetime | None
    next_retry_at: datetime | None
    refunded: bool
    captured: bool
    runpod_pod_id: str | None
    runpod_base_url: str | None


class DebugGenerationJobsResponse(BaseModel):
    items: list[DebugGenerationJobListItemResponse]


class DebugRetryWaitingGpuResponse(BaseModel):
    enqueued: int
    job_ids: list[UUID]
