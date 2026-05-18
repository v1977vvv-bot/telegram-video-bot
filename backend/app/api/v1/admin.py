from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.admin_auth import AdminPrincipal, require_admin_auth
from backend.app.models.admin_audit_log import AdminAuditLog
from backend.app.models.balance_account import BalanceAccount
from backend.app.models.balance_transaction import BalanceTransaction
from backend.app.models.business_account import BusinessAccount
from backend.app.models.business_account_member import BusinessAccountMember
from backend.app.models.business_balance_transaction import BusinessBalanceTransaction
from backend.app.models.generation_job import GenerationJob
from backend.app.models.payment import Payment
from backend.app.models.runpod_pod import RunpodPod
from backend.app.models.user import User
from backend.app.schemas.admin import (
    AdminAuditLogItem,
    AdminAuditLogsResponse,
    AdminBusinessAccountDetailResponse,
    AdminBusinessAccountListItem,
    AdminBusinessAccountsResponse,
    AdminBusinessJobItem,
    AdminBusinessMemberItem,
    AdminBusinessSummary,
    AdminBusinessTransactionItem,
    AdminBusinessUsageSummary,
    AdminJobListItem,
    AdminJobsResponse,
    AdminOverviewBalances,
    AdminOverviewBusiness,
    AdminOverviewJobs,
    AdminOverviewPayments,
    AdminOverviewResponse,
    AdminOverviewRunPod,
    AdminPaymentListItem,
    AdminPaymentsResponse,
    AdminRunPodPodItem,
    AdminRunPodPodsResponse,
    AdminUserListItem,
    AdminUsersResponse,
    AdminUserSummary,
)
from backend.app.services.admin_audit import AdminAuditService
from shared.app.database import get_session
from shared.app.enums import (
    BalanceTransactionType,
    BillingAccountType,
    BusinessAccountStatus,
    BusinessBalanceTransactionType,
    JobStatus,
    PaymentStatus,
    PodStatus,
)
from shared.app.exceptions import AppError
from worker.app.services.runpod_costs import RunPodCostService, calculate_gross_margin

router = APIRouter(prefix="/admin", tags=["admin"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[AdminPrincipal, Depends(require_admin_auth)]
MONEY_ZERO = Decimal("0.0000")
ACTIVE_POD_STATUSES = {
    PodStatus.CREATING.value,
    PodStatus.STARTING.value,
    PodStatus.READY.value,
    PodStatus.IDLE.value,
    PodStatus.BUSY.value,
}


@router.get("/overview", response_model=AdminOverviewResponse)
async def get_admin_overview(
    request: Request,
    session: SessionDep,
    admin: AdminDep,
) -> AdminOverviewResponse:
    await AdminAuditService(session).log(
        admin_identifier=str(admin),
        action="admin_overview_access",
        request=request,
    )
    response = AdminOverviewResponse(
        jobs=await _overview_jobs(session),
        payments=await _overview_payments(session),
        balances=await _overview_balances(session),
        runpod=await _overview_runpod(session),
        business=await _overview_business(session),
        anomalies_count=await _count_anomalies(session),
    )
    await session.commit()
    return response


@router.get("/users", response_model=AdminUsersResponse)
async def list_admin_users(
    session: SessionDep,
    _: AdminDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: str | None = None,
) -> AdminUsersResponse:
    query = select(User).order_by(desc(User.created_at)).limit(limit).offset(offset)
    if search:
        search_value = search.strip()
        conditions = [User.username.ilike(f"%{search_value}%")]
        if search_value.isdigit():
            conditions.append(User.telegram_id == int(search_value))
        query = query.where(or_(*conditions))

    result = await session.execute(query)
    users = list(result.scalars())
    items = [await _admin_user_item(session, user) for user in users]
    return AdminUsersResponse(items=items, limit=limit, offset=offset)


@router.get("/jobs", response_model=AdminJobsResponse)
async def list_admin_jobs(
    session: SessionDep,
    _: AdminDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: str | None = None,
    billing_account_type: str | None = None,
    user_id: UUID | None = None,
) -> AdminJobsResponse:
    query = select(GenerationJob, User).join(User, User.id == GenerationJob.user_id)
    if status:
        query = query.where(GenerationJob.status == status)
    if billing_account_type:
        query = query.where(GenerationJob.billing_account_type == billing_account_type)
    if user_id:
        query = query.where(GenerationJob.user_id == user_id)
    query = query.order_by(desc(GenerationJob.created_at)).limit(limit).offset(offset)

    result = await session.execute(query)
    items = [await _admin_job_item(session, job, user) for job, user in result.all()]
    return AdminJobsResponse(items=items, limit=limit, offset=offset)


@router.get("/payments", response_model=AdminPaymentsResponse)
async def list_admin_payments(
    session: SessionDep,
    _: AdminDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: str | None = None,
    user_id: UUID | None = None,
) -> AdminPaymentsResponse:
    query = select(Payment, User).join(User, User.id == Payment.user_id)
    if status:
        query = query.where(Payment.status == status)
    if user_id:
        query = query.where(Payment.user_id == user_id)
    query = query.order_by(desc(Payment.created_at)).limit(limit).offset(offset)

    result = await session.execute(query)
    items = [_admin_payment_item(payment, user) for payment, user in result.all()]
    return AdminPaymentsResponse(items=items, limit=limit, offset=offset)


@router.get("/runpod/pods", response_model=AdminRunPodPodsResponse)
async def list_admin_runpod_pods(
    session: SessionDep,
    _: AdminDep,
) -> AdminRunPodPodsResponse:
    result = await session.execute(select(RunpodPod).order_by(desc(RunpodPod.created_at)))
    return AdminRunPodPodsResponse(items=[_admin_runpod_pod_item(pod) for pod in result.scalars()])


@router.get("/business-accounts", response_model=AdminBusinessAccountsResponse)
async def list_admin_business_accounts(
    session: SessionDep,
    _: AdminDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminBusinessAccountsResponse:
    result = await session.execute(
        select(BusinessAccount)
        .order_by(desc(BusinessAccount.created_at))
        .limit(limit)
        .offset(offset)
    )
    accounts = list(result.scalars())
    items = [await _admin_business_account_item(session, account) for account in accounts]
    return AdminBusinessAccountsResponse(items=items, limit=limit, offset=offset)


@router.get(
    "/business-accounts/{business_account_id}",
    response_model=AdminBusinessAccountDetailResponse,
)
async def get_admin_business_account(
    business_account_id: UUID,
    session: SessionDep,
    _: AdminDep,
) -> AdminBusinessAccountDetailResponse:
    account = await session.get(BusinessAccount, business_account_id)
    if account is None:
        raise AppError(
            "Business account not found",
            code="business_account_not_found",
            status_code=404,
        )

    members = await _business_members(session, business_account_id)
    transactions = await _business_recent_transactions(session, business_account_id)
    jobs = await _business_recent_jobs(session, business_account_id)
    usage = await _business_usage_summary(session, business_account_id)
    return AdminBusinessAccountDetailResponse(
        account=await _admin_business_account_item(session, account),
        members=members,
        recent_transactions=transactions,
        recent_jobs=jobs,
        usage=usage,
    )


@router.get("/audit-logs", response_model=AdminAuditLogsResponse)
async def list_admin_audit_logs(
    session: SessionDep,
    _: AdminDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminAuditLogsResponse:
    result = await session.execute(
        select(AdminAuditLog).order_by(desc(AdminAuditLog.created_at)).limit(limit).offset(offset)
    )
    return AdminAuditLogsResponse(
        items=[_admin_audit_log_item(item) for item in result.scalars()],
        limit=limit,
        offset=offset,
    )


async def _overview_jobs(session: AsyncSession) -> AdminOverviewJobs:
    now = datetime.now(UTC)
    since = now - timedelta(hours=24)
    active_result = await session.execute(
        select(GenerationJob.status, func.count())
        .where(
            GenerationJob.status.in_(
                [
                    JobStatus.QUEUED.value,
                    JobStatus.WAITING_FOR_GPU.value,
                    JobStatus.WAITING_FOR_POD.value,
                    JobStatus.GENERATING.value,
                ]
            )
        )
        .group_by(GenerationJob.status)
    )
    counts = {str(status): int(count) for status, count in active_result.all()}
    completed_24h = await session.scalar(
        select(func.count(GenerationJob.id)).where(
            GenerationJob.status == JobStatus.COMPLETED.value,
            GenerationJob.updated_at >= since,
        )
    )
    failed_24h = await session.scalar(
        select(func.count(GenerationJob.id)).where(
            GenerationJob.status == JobStatus.FAILED.value,
            GenerationJob.updated_at >= since,
        )
    )
    return AdminOverviewJobs(
        queued=counts.get(JobStatus.QUEUED.value, 0),
        waiting_for_gpu=counts.get(JobStatus.WAITING_FOR_GPU.value, 0),
        waiting_for_pod=counts.get(JobStatus.WAITING_FOR_POD.value, 0),
        generating=counts.get(JobStatus.GENERATING.value, 0),
        completed_24h=int(completed_24h or 0),
        failed_24h=int(failed_24h or 0),
    )


async def _overview_payments(session: AsyncSession) -> AdminOverviewPayments:
    since = datetime.now(UTC) - timedelta(hours=24)
    paid_sum = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount_usd), 0)).where(
            Payment.status.in_([PaymentStatus.PAID.value, PaymentStatus.PAID_OVER.value]),
            Payment.paid_at >= since,
        )
    )
    pending_count = await session.scalar(
        select(func.count(Payment.id)).where(Payment.status == PaymentStatus.PENDING.value)
    )
    failed_count = await session.scalar(
        select(func.count(Payment.id)).where(Payment.status == PaymentStatus.FAILED.value)
    )
    return AdminOverviewPayments(
        paid_24h_usd=_money(paid_sum),
        pending_count=int(pending_count or 0),
        failed_count=int(failed_count or 0),
    )


async def _overview_balances(session: AsyncSession) -> AdminOverviewBalances:
    personal_available = await session.scalar(
        select(func.coalesce(func.sum(BalanceAccount.available_usd), 0))
    )
    personal_frozen = await session.scalar(
        select(func.coalesce(func.sum(BalanceAccount.frozen_usd), 0))
    )
    business_available = await session.scalar(
        select(func.coalesce(func.sum(BusinessAccount.available_usd), 0))
    )
    business_frozen = await session.scalar(
        select(func.coalesce(func.sum(BusinessAccount.frozen_usd), 0))
    )
    return AdminOverviewBalances(
        total_personal_available_usd=_money(personal_available),
        total_personal_frozen_usd=_money(personal_frozen),
        total_business_available_usd=_money(business_available),
        total_business_frozen_usd=_money(business_frozen),
    )


async def _overview_runpod(session: AsyncSession) -> AdminOverviewRunPod:
    result = await session.execute(
        select(RunpodPod).where(RunpodPod.status.in_(ACTIVE_POD_STATUSES))
    )
    pods = list(result.scalars())
    idle_pods = sum(
        1 for pod in pods if pod.status in {PodStatus.IDLE.value, PodStatus.READY.value}
    )
    busy_pods = sum(1 for pod in pods if pod.status == PodStatus.BUSY.value)
    estimated_cost = sum((_pod_estimated_cost(pod) or MONEY_ZERO for pod in pods), MONEY_ZERO)
    return AdminOverviewRunPod(
        active_pods=len(pods),
        idle_pods=idle_pods,
        busy_pods=busy_pods,
        estimated_active_cost_usd=estimated_cost,
    )


async def _overview_business(session: AsyncSession) -> AdminOverviewBusiness:
    accounts = await session.scalar(
        select(func.count(BusinessAccount.id)).where(
            BusinessAccount.status == BusinessAccountStatus.ACTIVE.value
        )
    )
    members = await session.scalar(
        select(func.count(BusinessAccountMember.id)).where(
            BusinessAccountMember.is_active.is_(True)
        )
    )
    return AdminOverviewBusiness(
        active_accounts=int(accounts or 0),
        active_members=int(members or 0),
    )


async def _admin_user_item(session: AsyncSession, user: User) -> AdminUserListItem:
    balance = await session.scalar(select(BalanceAccount).where(BalanceAccount.user_id == user.id))
    business = await _active_business_summary_for_user(session, user.id)
    generation_count = await session.scalar(
        select(func.count(GenerationJob.id)).where(GenerationJob.user_id == user.id)
    )
    payment_count = await session.scalar(
        select(func.count(Payment.id)).where(Payment.user_id == user.id)
    )
    return AdminUserListItem(
        id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        created_at=user.created_at,
        updated_at=user.updated_at,
        personal_available_usd=balance.available_usd if balance is not None else MONEY_ZERO,
        personal_frozen_usd=balance.frozen_usd if balance is not None else MONEY_ZERO,
        business_account=business,
        generation_count=int(generation_count or 0),
        payment_count=int(payment_count or 0),
    )


async def _admin_job_item(
    session: AsyncSession,
    job: GenerationJob,
    user: User,
) -> AdminJobListItem:
    captured, refunded = await _job_ledger_flags(session, job)
    margin, _ = calculate_gross_margin(price_usd=job.price_usd, cost_usd=job.cost_usd)
    pod = await _pod_for_job(session, job.id)
    return AdminJobListItem(
        id=job.id,
        user=AdminUserSummary(id=user.id, telegram_id=user.telegram_id, username=user.username),
        status=job.status,
        price_usd=job.price_usd,
        cost_usd=job.cost_usd,
        gross_margin_usd=margin,
        billing_account_type=job.billing_account_type,
        business_account_id=job.business_account_id,
        captured=captured,
        refunded=refunded,
        created_at=job.created_at,
        updated_at=job.updated_at,
        runpod_pod_id=pod.runpod_pod_id if pod is not None else None,
        runpod_base_url=pod.base_url if pod is not None else None,
        error_message=_truncate(job.error_message, 500),
    )


def _admin_payment_item(payment: Payment, user: User) -> AdminPaymentListItem:
    metadata = payment.raw_payload if isinstance(payment.raw_payload, dict) else {}
    provider_currency = _optional_str(metadata.get("provider_currency"))
    if provider_currency is None:
        nested = metadata.get("ultronlab_metadata")
        if isinstance(nested, dict):
            provider_currency = _optional_str(nested.get("provider_currency"))
    return AdminPaymentListItem(
        id=payment.id,
        user=AdminUserSummary(id=user.id, telegram_id=user.telegram_id, username=user.username),
        provider=payment.provider,
        provider_invoice_id=payment.provider_invoice_id,
        amount_usd=payment.amount_usd,
        currency=payment.currency,
        provider_currency=provider_currency,
        status=payment.status,
        created_at=payment.created_at,
        updated_at=payment.updated_at,
        paid_at=payment.paid_at,
    )


def _admin_runpod_pod_item(pod: RunpodPod) -> AdminRunPodPodItem:
    return AdminRunPodPodItem(
        id=pod.id,
        runpod_pod_id=pod.runpod_pod_id,
        provider_pod_id=pod.provider_pod_id,
        name=pod.name,
        status=pod.status,
        gpu_type=pod.gpu_type,
        base_url=pod.base_url,
        active_job_id=pod.active_job_id or pod.current_job_id,
        created_at=pod.created_at,
        updated_at=pod.updated_at,
        last_healthcheck_at=pod.last_healthcheck_at,
        last_used_at=pod.last_used_at,
        terminated_at=pod.terminated_at,
        estimated_runtime_seconds=_pod_runtime_seconds(pod),
        estimated_cost_usd=_pod_estimated_cost(pod),
    )


async def _admin_business_account_item(
    session: AsyncSession,
    account: BusinessAccount,
) -> AdminBusinessAccountListItem:
    active_members = await session.scalar(
        select(func.count(BusinessAccountMember.id)).where(
            BusinessAccountMember.business_account_id == account.id,
            BusinessAccountMember.is_active.is_(True),
        )
    )
    return AdminBusinessAccountListItem(
        id=account.id,
        name=account.name,
        status=account.status,
        available_usd=account.available_usd,
        frozen_usd=account.frozen_usd,
        active_members_count=int(active_members or 0),
        created_at=account.created_at,
        updated_at=account.updated_at,
    )


async def _active_business_summary_for_user(
    session: AsyncSession,
    user_id: UUID,
) -> AdminBusinessSummary | None:
    result = await session.execute(
        select(BusinessAccount)
        .join(
            BusinessAccountMember,
            BusinessAccountMember.business_account_id == BusinessAccount.id,
        )
        .where(
            BusinessAccountMember.user_id == user_id,
            BusinessAccountMember.is_active.is_(True),
            BusinessAccount.status == BusinessAccountStatus.ACTIVE.value,
        )
        .order_by(desc(BusinessAccountMember.created_at))
        .limit(1)
    )
    account = result.scalar_one_or_none()
    if account is None:
        return None
    return AdminBusinessSummary(
        id=account.id,
        name=account.name,
        status=account.status,
        available_usd=account.available_usd,
        frozen_usd=account.frozen_usd,
    )


async def _business_members(
    session: AsyncSession,
    business_account_id: UUID,
) -> list[AdminBusinessMemberItem]:
    result = await session.execute(
        select(BusinessAccountMember, User)
        .join(User, User.id == BusinessAccountMember.user_id)
        .where(BusinessAccountMember.business_account_id == business_account_id)
        .order_by(desc(BusinessAccountMember.is_active), BusinessAccountMember.created_at)
    )
    return [
        AdminBusinessMemberItem(
            user_id=user.id,
            telegram_id=user.telegram_id,
            username=user.username,
            role=member.role,
            is_active=member.is_active,
            created_at=member.created_at,
        )
        for member, user in result.all()
    ]


async def _business_recent_transactions(
    session: AsyncSession,
    business_account_id: UUID,
) -> list[AdminBusinessTransactionItem]:
    result = await session.execute(
        select(BusinessBalanceTransaction)
        .where(BusinessBalanceTransaction.business_account_id == business_account_id)
        .order_by(desc(BusinessBalanceTransaction.created_at))
        .limit(20)
    )
    return [
        AdminBusinessTransactionItem(
            id=tx.id,
            type=tx.type,
            amount_usd=tx.amount_usd,
            user_id=tx.user_id,
            generation_job_id=tx.generation_job_id,
            reason=tx.reason,
            created_at=tx.created_at,
        )
        for tx in result.scalars()
    ]


async def _business_recent_jobs(
    session: AsyncSession,
    business_account_id: UUID,
) -> list[AdminBusinessJobItem]:
    result = await session.execute(
        select(GenerationJob)
        .where(GenerationJob.business_account_id == business_account_id)
        .order_by(desc(GenerationJob.created_at))
        .limit(20)
    )
    return [
        AdminBusinessJobItem(
            id=job.id,
            user_id=job.user_id,
            status=job.status,
            price_usd=job.price_usd,
            cost_usd=job.cost_usd,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
        for job in result.scalars()
    ]


async def _business_usage_summary(
    session: AsyncSession,
    business_account_id: UUID,
) -> AdminBusinessUsageSummary:
    topups = await _business_tx_sum(
        session,
        business_account_id,
        [
            BusinessBalanceTransactionType.MANUAL_TOPUP.value,
            BusinessBalanceTransactionType.ADJUSTMENT.value,
        ],
    )
    spent = await _business_tx_sum(
        session,
        business_account_id,
        [BusinessBalanceTransactionType.CAPTURE.value],
    )
    refunded = await _business_tx_sum(
        session,
        business_account_id,
        [BusinessBalanceTransactionType.REFUND.value, BusinessBalanceTransactionType.RELEASE.value],
    )
    return AdminBusinessUsageSummary(topups_usd=topups, spent_usd=spent, refunded_usd=refunded)


async def _business_tx_sum(
    session: AsyncSession,
    business_account_id: UUID,
    types: list[str],
) -> Decimal:
    value = await session.scalar(
        select(func.coalesce(func.sum(BusinessBalanceTransaction.amount_usd), 0)).where(
            BusinessBalanceTransaction.business_account_id == business_account_id,
            BusinessBalanceTransaction.type.in_(types),
        )
    )
    return _money(value)


async def _job_ledger_flags(session: AsyncSession, job: GenerationJob) -> tuple[bool, bool]:
    if job.billing_account_type == BillingAccountType.BUSINESS.value:
        capture_count = await session.scalar(
            select(func.count(BusinessBalanceTransaction.id)).where(
                BusinessBalanceTransaction.generation_job_id == job.id,
                BusinessBalanceTransaction.type == BusinessBalanceTransactionType.CAPTURE.value,
            )
        )
        refund_count = await session.scalar(
            select(func.count(BusinessBalanceTransaction.id)).where(
                BusinessBalanceTransaction.generation_job_id == job.id,
                BusinessBalanceTransaction.type.in_(
                    [
                        BusinessBalanceTransactionType.REFUND.value,
                        BusinessBalanceTransactionType.RELEASE.value,
                    ]
                ),
            )
        )
    else:
        capture_count = await session.scalar(
            select(func.count(BalanceTransaction.id)).where(
                BalanceTransaction.generation_job_id == job.id,
                BalanceTransaction.type == BalanceTransactionType.CAPTURE.value,
            )
        )
        refund_count = await session.scalar(
            select(func.count(BalanceTransaction.id)).where(
                BalanceTransaction.generation_job_id == job.id,
                BalanceTransaction.type.in_(
                    [BalanceTransactionType.REFUND.value, BalanceTransactionType.RELEASE.value]
                ),
            )
        )
    return bool(capture_count), bool(refund_count)


async def _pod_for_job(session: AsyncSession, job_id: UUID) -> RunpodPod | None:
    result = await session.execute(
        select(RunpodPod)
        .where(or_(RunpodPod.active_job_id == job_id, RunpodPod.current_job_id == job_id))
        .order_by(desc(RunpodPod.updated_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _count_anomalies(session: AsyncSession) -> int:
    terminal_retry = await session.scalar(
        select(func.count(GenerationJob.id)).where(
            GenerationJob.status.in_(
                [JobStatus.FAILED.value, JobStatus.CANCELLED.value, JobStatus.COMPLETED.value]
            ),
            or_(
                GenerationJob.next_retry_at.is_not(None),
                GenerationJob.waiting_for_gpu_since.is_not(None),
                GenerationJob.waiting_for_pod_since.is_not(None),
            ),
        )
    )
    business_missing = await session.scalar(
        select(func.count(GenerationJob.id)).where(
            GenerationJob.billing_account_type == BillingAccountType.BUSINESS.value,
            GenerationJob.business_account_id.is_(None),
        )
    )
    negative_business = await session.scalar(
        select(func.count(BusinessAccount.id)).where(
            or_(BusinessAccount.available_usd < 0, BusinessAccount.frozen_usd < 0)
        )
    )
    active_pods_without_base = await session.scalar(
        select(func.count(RunpodPod.id)).where(
            RunpodPod.status.in_(ACTIVE_POD_STATUSES),
            RunpodPod.base_url.is_(None),
        )
    )
    pods_busy_without_job = await session.scalar(
        select(func.count(RunpodPod.id)).where(
            RunpodPod.status == PodStatus.BUSY.value,
            RunpodPod.active_job_id.is_(None),
            RunpodPod.current_job_id.is_(None),
        )
    )
    return sum(
        int(value or 0)
        for value in [
            terminal_retry,
            business_missing,
            negative_business,
            active_pods_without_base,
            pods_busy_without_job,
        ]
    )


def _admin_audit_log_item(log: AdminAuditLog) -> AdminAuditLogItem:
    return AdminAuditLogItem(
        id=log.id,
        admin_identifier=log.admin_identifier,
        action=log.action,
        target_type=log.target_type,
        target_id=log.target_id,
        request_path=log.request_path,
        request_method=log.request_method,
        ip_address=log.ip_address,
        user_agent=log.user_agent,
        metadata=log.audit_metadata,
        created_at=log.created_at,
    )


def _pod_runtime_seconds(pod: RunpodPod) -> int | None:
    ended_at = pod.terminated_at or datetime.now(UTC)
    return RunPodCostService().runtime_seconds(started_at=pod.created_at, ended_at=ended_at)


def _pod_estimated_cost(pod: RunpodPod) -> Decimal | None:
    if pod.created_at is None:
        return None
    ended_at = pod.terminated_at or datetime.now(UTC)
    return RunPodCostService().calculate_runpod_cost_usd(
        gpu_type=pod.gpu_type,
        started_at=pod.created_at,
        ended_at=ended_at,
        min_billing_seconds=0,
    )


def _money(value: Any) -> Decimal:
    if value is None:
        return MONEY_ZERO
    return Decimal(str(value)).quantize(Decimal("0.0001"))


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _truncate(value: str | None, max_len: int) -> str | None:
    if value is None or len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."
