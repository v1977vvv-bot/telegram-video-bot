from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


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
    idle_pods: int = 0
    busy_pods: int = 0
    estimated_active_cost_usd: Decimal = Decimal("0.0000")


class AdminOverviewBusiness(BaseModel):
    active_accounts: int = 0
    active_members: int = 0


class AdminOverviewResponse(BaseModel):
    jobs: AdminOverviewJobs
    payments: AdminOverviewPayments
    balances: AdminOverviewBalances
    runpod: AdminOverviewRunPod
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
    gpu_type: str | None
    base_url: str | None
    active_job_id: UUID | None
    created_at: datetime
    updated_at: datetime
    last_healthcheck_at: datetime | None
    last_used_at: datetime | None
    terminated_at: datetime | None
    estimated_runtime_seconds: int | None
    estimated_cost_usd: Decimal | None


class AdminRunPodPodsResponse(BaseModel):
    items: list[AdminRunPodPodItem]


class AdminBusinessAccountListItem(BaseModel):
    id: UUID
    name: str
    status: str
    available_usd: Decimal
    frozen_usd: Decimal
    active_members_count: int
    created_at: datetime
    updated_at: datetime


class AdminBusinessAccountsResponse(BaseModel):
    items: list[AdminBusinessAccountListItem]
    limit: int
    offset: int


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
