from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class AdminUserSummary(BaseModel):
    id: UUID
    telegram_id: int
    username: str | None


class AdminBusinessSummary(BaseModel):
    id: UUID
    name: str
    status: str
    available_usd: Decimal
    frozen_usd: Decimal


class AdminOverviewJobs(BaseModel):
    queued: int = 0
    waiting_for_gpu: int = 0
    waiting_for_pod: int = 0
    generating: int = 0
    completed_24h: int = 0
    failed_24h: int = 0


class AdminOverviewPayments(BaseModel):
    paid_24h_usd: Decimal = Decimal("0.0000")
    pending_count: int = 0
    failed_count: int = 0


class AdminOverviewBalances(BaseModel):
    total_personal_available_usd: Decimal = Decimal("0.0000")
    total_personal_frozen_usd: Decimal = Decimal("0.0000")
    total_business_available_usd: Decimal = Decimal("0.0000")
    total_business_frozen_usd: Decimal = Decimal("0.0000")


class AdminOverviewRunPod(BaseModel):
    active_pods: int = 0
    starting_pods: int = 0
    idle_pods: int = 0
    busy_pods: int = 0
    auto_create_enabled: bool = True
    estimated_active_cost_usd: Decimal = Decimal("0.0000")


class AdminQueueLoadPlan(BaseModel):
    waiting_for_pod_jobs_count: int = 0
    queued_jobs_count: int = 0
    generating_jobs_count: int = 0
    total_waiting_audio_seconds: Decimal = Decimal("0")
    total_waiting_audio_minutes: Decimal = Decimal("0")
    healthy_pods_count: int = 0
    idle_healthy_pods_count: int = 0
    busy_pods_count: int = 0
    active_pods_count: int = 0
    oldest_wait_minutes: int = 0
    target_minutes_per_pod_min: Decimal = Decimal("5")
    target_minutes_per_pod_max: Decimal = Decimal("6")
    current_capacity_minutes_min: Decimal = Decimal("0")
    current_capacity_minutes_max: Decimal = Decimal("0")
    recommended_total_pods: int = 0
    recommended_additional_pods: int = 0
    max_active_pods: int = 0
    alert_min_total_minutes: Decimal = Decimal("5")
    max_recommended_pods: int = 0
    include_generating: bool = True
    planning_enabled: bool = True
    should_alert: bool = False
    alert_reason: str | None = None


class AdminOverviewBusiness(BaseModel):
    active_accounts: int = 0
    active_members: int = 0


class AdminOverviewResponse(BaseModel):
    jobs: AdminOverviewJobs
    payments: AdminOverviewPayments
    balances: AdminOverviewBalances
    runpod: AdminOverviewRunPod
    queue_load_plan: AdminQueueLoadPlan
    business: AdminOverviewBusiness
    anomalies_count: int


class AdminUserListItem(BaseModel):
    id: UUID
    telegram_id: int
    username: str | None
    created_at: datetime
    updated_at: datetime
    personal_available_usd: Decimal
    personal_frozen_usd: Decimal
    business_account: AdminBusinessSummary | None = None
    generation_count: int
    payment_count: int


class AdminUsersResponse(BaseModel):
    items: list[AdminUserListItem]
    limit: int
    offset: int


class AdminJobListItem(BaseModel):
    id: UUID
    user: AdminUserSummary
    status: str
    price_usd: Decimal | None
    cost_usd: Decimal | None
    gross_margin_usd: Decimal | None
    billing_account_type: str
    business_account_id: UUID | None
    captured: bool
    refunded: bool
    created_at: datetime
    updated_at: datetime
    runpod_pod_id: str | None
    runpod_cloud_type: str | None
    runpod_gpu_type: str | None
    runpod_base_url: str | None
    error_message: str | None


class AdminJobsResponse(BaseModel):
    items: list[AdminJobListItem]
    limit: int
    offset: int


class AdminPaymentListItem(BaseModel):
    id: UUID
    user: AdminUserSummary
    provider: str
    provider_invoice_id: str | None
    amount_usd: Decimal
    currency: str
    provider_currency: str | None
    provider_amount: Decimal | None = None
    payment_url: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    paid_at: datetime | None


class AdminPaymentsResponse(BaseModel):
    items: list[AdminPaymentListItem]
    limit: int
    offset: int


class AdminRunPodPodItem(BaseModel):
    id: UUID
    runpod_pod_id: str
    provider_pod_id: str
    name: str | None
    status: str
    cloud_type: str | None
    gpu_type: str | None
    base_url: str | None
    active_job_id: UUID | None
    current_job_id: UUID | None = None
    health_status: str | None = None
    created_at: datetime
    updated_at: datetime
    registered_age_minutes: int | None = None
    last_healthcheck_at: datetime | None
    last_heartbeat_at: datetime | None = None
    last_used_at: datetime | None
    last_busy_at: datetime | None = None
    terminated_at: datetime | None
    estimated_runtime_seconds: int | None
    estimated_hourly_cost_usd: Decimal | None
    estimated_startup_surcharge_usd: Decimal | None
    estimated_cost_usd: Decimal | None


class AdminRunPodPodsResponse(BaseModel):
    items: list[AdminRunPodPodItem]
    queue_load_plan: AdminQueueLoadPlan | None = None
    runpod_auto_create_enabled: bool = True
    manual_only_mode: bool = False
    starting_count: int = 0


class AdminRunPodSkippedPod(BaseModel):
    pod_id: str
    reason: str
    status: str | None = None
    gpu_type: str | None = None
    template_id: str | None = None


class AdminRunPodSyncResponse(BaseModel):
    found: int
    registered: int
    updated: int
    healthy: int
    starting: int = 0
    skipped_count: int = 0
    starting_healthcheck_interval_seconds: int | None = None
    skipped: list[AdminRunPodSkippedPod]
    audit_log_id: UUID | None = None


class AdminRunPodHealthCheckResponse(BaseModel):
    checked: int
    healthy: int
    unhealthy: int
    skipped: list[AdminRunPodSkippedPod]
    audit_log_id: UUID | None = None


class AdminRunPodCleanupIdleRequest(BaseModel):
    reason: str


class AdminRunPodCleanupIdleResponse(BaseModel):
    terminated_count: int
    terminated_pod_ids: list[str]
    audit_log_id: UUID | None = None


class AdminWaitingPodJobItem(BaseModel):
    id: UUID
    short_id: str
    telegram_id: int
    username: str | None = None
    audio_duration_seconds: Decimal | None = None
    waiting_minutes: int
    price_usd: Decimal | None = None
    created_at: datetime
    waiting_for_pod_since: datetime | None = None


class AdminWaitingPodJobsResponse(BaseModel):
    items: list[AdminWaitingPodJobItem]
    limit: int
    total_waiting_jobs: int = 0
    total_waiting_audio_minutes: Decimal = Decimal("0")
    oldest_wait_minutes: int = 0
    recommended_additional_pods: int = 0
    queue_load_plan: AdminQueueLoadPlan | None = None


class AdminBusinessAccountListItem(BaseModel):
    id: UUID
    short_id: str
    name: str
    status: str
    available_usd: Decimal
    frozen_usd: Decimal
    active_members_count: int
    members_count: int
    created_at: datetime
    updated_at: datetime


class AdminBusinessAccountsResponse(BaseModel):
    items: list[AdminBusinessAccountListItem]
    limit: int
    offset: int


class BusinessAccountCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    owner_telegram_id: int | None = None
    owner_user_id: UUID | None = None
    initial_balance_usd: Decimal | None = Field(default=Decimal("0"), ge=0)
    reason: str

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Business account name is required")
        return normalized

    @model_validator(mode="after")
    def validate_owner_identifier(self) -> BusinessAccountCreateRequest:
        if self.owner_telegram_id is not None and self.owner_user_id is not None:
            raise ValueError("Use either owner_telegram_id or owner_user_id, not both")
        return self


class BusinessAccountCreateResponse(BaseModel):
    business_account_id: UUID
    business_account_name: str
    owner_user_id: UUID | None = None
    initial_balance_usd: Decimal
    audit_log_id: UUID | None = None
    telegram_notification_sent: bool | None = None
    warning: str | None = None


class AdminBusinessMemberItem(BaseModel):
    user_id: UUID
    telegram_id: int
    username: str | None
    role: str
    is_active: bool
    created_at: datetime


class AdminBusinessTransactionItem(BaseModel):
    id: UUID
    type: str
    amount_usd: Decimal
    user_id: UUID | None
    generation_job_id: UUID | None
    reason: str | None
    created_at: datetime


class AdminBusinessJobItem(BaseModel):
    id: UUID
    user_id: UUID
    status: str
    price_usd: Decimal | None
    cost_usd: Decimal | None
    created_at: datetime
    updated_at: datetime


class AdminBusinessUsageSummary(BaseModel):
    topups_usd: Decimal
    spent_usd: Decimal
    refunded_usd: Decimal


class AdminBusinessAccountDetailResponse(BaseModel):
    account: AdminBusinessAccountListItem
    members: list[AdminBusinessMemberItem]
    recent_transactions: list[AdminBusinessTransactionItem]
    recent_jobs: list[AdminBusinessJobItem]
    usage: AdminBusinessUsageSummary


class AdminAuditLogItem(BaseModel):
    id: UUID
    admin_identifier: str
    action: str
    target_type: str | None
    target_id: str | None
    request_path: str | None
    request_method: str | None
    ip_address: str | None
    user_agent: str | None
    metadata: dict[str, Any] | None = Field(default=None)
    created_at: datetime


class AdminAuditLogsResponse(BaseModel):
    items: list[AdminAuditLogItem]
    limit: int
    offset: int


class ManualPersonalTopUpRequest(BaseModel):
    amount_usd: Decimal
    reason: str


class ManualBusinessTopUpRequest(BaseModel):
    amount_usd: Decimal
    reason: str


class BusinessMemberAddRequest(BaseModel):
    telegram_id: int | None = None
    user_id: UUID | None = None
    role: str = "member"
    reason: str


class BusinessMemberRemoveRequest(BaseModel):
    reason: str


class FailRefundJobRequest(BaseModel):
    reason: str


class TerminateRunPodRequest(BaseModel):
    reason: str


class RetryWaitingJobsRequest(BaseModel):
    reason: str


class PaymentRecheckRequest(BaseModel):
    reason: str


class PaymentRecheckResponse(BaseModel):
    payment_id: UUID
    provider: str
    provider_invoice_id: str | None
    old_status: str
    new_status: str
    credited: bool
    credited_amount_usd: Decimal | None = None
    message: str
    audit_log_id: UUID | None = None
    telegram_notification_sent: bool | None = None
    warning: str | None = None


class UserBlockRequest(BaseModel):
    reason: str


class AdminActionResponse(BaseModel):
    success: bool
    target_id: str
    action: str
    audit_log_id: UUID | None = None
    old_state: str | None = None
    new_state: str | None = None
    transaction_id: UUID | None = None
    amount_usd: Decimal | None = None
    balance_available_usd: Decimal | None = None
    balance_frozen_usd: Decimal | None = None
    telegram_notification_sent: bool | None = None
    warning: str | None = None
    enqueued: int | None = None
    job_ids: list[UUID] | None = None
