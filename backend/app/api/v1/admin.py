from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.debug import (
    _fail_generation_job_with_refund,
    _retry_debug_waiting_jobs,
    _terminate_runpod_pod,
)
from backend.app.core.admin_auth import (
    AdminPrincipal,
    require_admin_actions_enabled,
    require_admin_auth,
)
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
    AdminActionResponse,
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
    BusinessMemberAddRequest,
    BusinessMemberRemoveRequest,
    FailRefundJobRequest,
    ManualBusinessTopUpRequest,
    ManualPersonalTopUpRequest,
    RetryWaitingJobsRequest,
    TerminateRunPodRequest,
    UserBlockRequest,
)
from backend.app.services.admin_audit import AdminAuditService
from backend.app.services.balances import BalanceService
from backend.app.services.business_balance import BusinessBalanceService
from backend.app.services.telegram_notify import TelegramNotificationService
from shared.app.config import Settings
from shared.app.database import get_session
from shared.app.enums import (
    BalanceTransactionType,
    BillingAccountType,
    BusinessAccountMemberRole,
    BusinessAccountStatus,
    BusinessBalanceTransactionType,
    JobStatus,
    PaymentStatus,
    PodStatus,
)
from shared.app.exceptions import AppError
from worker.app.services.runpod import RunPodError
from worker.app.services.runpod_costs import RunPodCostService, calculate_gross_margin

router = APIRouter(prefix="/admin", tags=["admin"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[AdminPrincipal, Depends(require_admin_auth)]
ActionSettingsDep = Annotated[Settings, Depends(require_admin_actions_enabled)]
MONEY_ZERO = Decimal("0.0000")
MONEY_QUANT = Decimal("0.0001")
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


@router.post("/users/{user_id}/balance/top-up", response_model=AdminActionResponse)
async def admin_top_up_personal_balance(
    user_id: UUID,
    payload: ManualPersonalTopUpRequest,
    request: Request,
    session: SessionDep,
    admin: AdminDep,
    settings: ActionSettingsDep,
) -> AdminActionResponse:
    reason = _validate_action_reason(payload.reason, settings)
    amount = _validate_admin_amount(payload.amount_usd, settings)
    telegram_id: int | None = None
    notification_sent = False
    warning: str | None = None

    async with session.begin():
        user = await session.get(User, user_id)
        if user is None:
            raise AppError("User not found", code="user_not_found", status_code=404)
        balance_service = BalanceService(session)
        account, transaction = await balance_service.admin_adjustment_balance_in_transaction(
            user_id=user.id,
            amount_usd=amount,
            reason=reason,
        )
        audit_log = await AdminAuditService(session).log(
            admin_identifier=str(admin),
            action="personal_balance_topup",
            request=request,
            target_type="user",
            target_id=str(user.id),
            metadata={"amount_usd": str(amount), "reason": reason},
        )
        telegram_id = user.telegram_id
        available = account.available_usd
        frozen = account.frozen_usd
        transaction_id = transaction.id
        audit_log_id = audit_log.id

    message = (
        "✅ Баланс пополнен\n\n"
        f"На ваш баланс зачислено: ${amount}\n"
        f"Текущий баланс: ${available}\n\n"
        "Теперь вы можете запустить генерацию."
    )
    notification_sent, warning = await _send_telegram_notification(
        telegram_id=telegram_id,
        message=message,
    )
    return AdminActionResponse(
        success=True,
        target_id=str(user_id),
        action="personal_balance_topup",
        audit_log_id=audit_log_id,
        transaction_id=transaction_id,
        amount_usd=amount,
        balance_available_usd=available,
        balance_frozen_usd=frozen,
        telegram_notification_sent=notification_sent,
        warning=warning,
    )


@router.post(
    "/business-accounts/{business_account_id}/balance/top-up",
    response_model=AdminActionResponse,
)
async def admin_top_up_business_balance(
    business_account_id: UUID,
    payload: ManualBusinessTopUpRequest,
    request: Request,
    session: SessionDep,
    admin: AdminDep,
    settings: ActionSettingsDep,
) -> AdminActionResponse:
    reason = _validate_action_reason(payload.reason, settings)
    amount = _validate_admin_amount(payload.amount_usd, settings)
    owner_telegram_ids: list[int] = []
    notification_sent_count = 0
    warnings: list[str] = []

    async with session.begin():
        mutation = await BusinessBalanceService(session).manual_topup_business_balance(
            business_account_id=business_account_id,
            amount_usd=amount,
            reason=reason,
            admin_note=f"admin={admin}",
        )
        if mutation.transaction is None:
            raise AppError("Business top-up transaction was not created", code="topup_failed")
        await session.refresh(mutation.account)
        owner_telegram_ids = await _active_owner_telegram_ids(session, business_account_id)
        audit_log = await AdminAuditService(session).log(
            admin_identifier=str(admin),
            action="business_balance_topup",
            request=request,
            target_type="business_account",
            target_id=str(business_account_id),
            metadata={"amount_usd": str(amount), "reason": reason},
        )
        account_name = mutation.account.name
        available = mutation.account.available_usd
        frozen = mutation.account.frozen_usd
        transaction_id = mutation.transaction.id
        audit_log_id = audit_log.id

    for telegram_id in owner_telegram_ids:
        message = (
            "✅ Баланс компании пополнен\n\n"
            f"Компания: {account_name}\n"
            f"Зачислено: ${amount}\n"
            f"Текущий баланс компании: ${available}"
        )
        sent, warning = await _send_telegram_notification(telegram_id=telegram_id, message=message)
        if sent:
            notification_sent_count += 1
        if warning:
            warnings.append(warning)

    return AdminActionResponse(
        success=True,
        target_id=str(business_account_id),
        action="business_balance_topup",
        audit_log_id=audit_log_id,
        transaction_id=transaction_id,
        amount_usd=amount,
        balance_available_usd=available,
        balance_frozen_usd=frozen,
        telegram_notification_sent=notification_sent_count > 0 if owner_telegram_ids else None,
        warning="; ".join(warnings) if warnings else None,
    )


@router.post("/business-accounts/{business_account_id}/members", response_model=AdminActionResponse)
async def admin_add_business_member(
    business_account_id: UUID,
    payload: BusinessMemberAddRequest,
    request: Request,
    session: SessionDep,
    admin: AdminDep,
    settings: ActionSettingsDep,
) -> AdminActionResponse:
    reason = _validate_action_reason(payload.reason, settings)
    role = _validate_business_role(payload.role)
    notification_sent = False
    warning: str | None = None

    async with session.begin():
        account = await _get_active_business_account(session, business_account_id)
        user = await _get_user_for_admin_member_payload(session, payload)
        await _ensure_no_other_active_business_membership(session, user.id, business_account_id)
        member = await _get_business_member_for_update(session, business_account_id, user.id)
        old_state = "none" if member is None else ("active" if member.is_active else "inactive")
        if member is None:
            member = BusinessAccountMember(
                business_account_id=business_account_id,
                user_id=user.id,
                role=role,
                is_active=True,
            )
            session.add(member)
        else:
            member.role = role
            member.is_active = True
        await session.flush()
        audit_log = await AdminAuditService(session).log(
            admin_identifier=str(admin),
            action="business_member_add",
            request=request,
            target_type="business_account_member",
            target_id=str(member.id),
            metadata={
                "business_account_id": str(business_account_id),
                "user_id": str(user.id),
                "role": role,
                "reason": reason,
            },
        )
        telegram_id = user.telegram_id
        account_name = account.name
        member_id = member.id
        audit_log_id = audit_log.id

    notification_sent, warning = await _send_telegram_notification(
        telegram_id=telegram_id,
        message=(
            "🏢 Вам подключён бизнес-баланс\n\n"
            f"Компания: {account_name}\n"
            "Теперь генерации будут оплачиваться с баланса компании."
        ),
    )
    return AdminActionResponse(
        success=True,
        target_id=str(member_id),
        action="business_member_add",
        audit_log_id=audit_log_id,
        old_state=old_state,
        new_state="active",
        telegram_notification_sent=notification_sent,
        warning=warning,
    )


@router.post(
    "/business-accounts/{business_account_id}/members/{user_id}/deactivate",
    response_model=AdminActionResponse,
)
async def admin_deactivate_business_member(
    business_account_id: UUID,
    user_id: UUID,
    payload: BusinessMemberRemoveRequest,
    request: Request,
    session: SessionDep,
    admin: AdminDep,
    settings: ActionSettingsDep,
) -> AdminActionResponse:
    reason = _validate_action_reason(payload.reason, settings)
    notification_sent = False
    warning: str | None = None

    async with session.begin():
        account = await _get_business_account(session, business_account_id)
        user = await session.get(User, user_id)
        if user is None:
            raise AppError("User not found", code="user_not_found", status_code=404)
        member = await _get_business_member_for_update(session, business_account_id, user_id)
        if member is None:
            raise AppError(
                "Business member not found",
                code="business_member_not_found",
                status_code=404,
            )
        old_state = "active" if member.is_active else "inactive"
        member.is_active = False
        audit_log = await AdminAuditService(session).log(
            admin_identifier=str(admin),
            action="business_member_remove",
            request=request,
            target_type="business_account_member",
            target_id=str(member.id),
            metadata={
                "business_account_id": str(business_account_id),
                "user_id": str(user_id),
                "reason": reason,
            },
        )
        telegram_id = user.telegram_id
        account_name = account.name
        member_id = member.id
        audit_log_id = audit_log.id

    notification_sent, warning = await _send_telegram_notification(
        telegram_id=telegram_id,
        message=(
            "🏢 Доступ к бизнес-балансу отключён\n\n"
            f"Компания: {account_name}\n"
            "Теперь генерации будут оплачиваться с личного баланса."
        ),
    )
    return AdminActionResponse(
        success=True,
        target_id=str(member_id),
        action="business_member_remove",
        audit_log_id=audit_log_id,
        old_state=old_state,
        new_state="inactive",
        telegram_notification_sent=notification_sent,
        warning=warning,
    )


@router.post("/jobs/{job_id}/fail-refund", response_model=AdminActionResponse)
async def admin_fail_refund_job(
    job_id: UUID,
    payload: FailRefundJobRequest,
    request: Request,
    session: SessionDep,
    admin: AdminDep,
    settings: ActionSettingsDep,
) -> AdminActionResponse:
    reason = _validate_action_reason(payload.reason, settings)
    result = await _fail_generation_job_with_refund(
        session=session,
        job_id=job_id,
        error_message=f"Generation cancelled by admin: {reason}",
        notify=False,
    )
    notification_sent = False
    warning: str | None = None

    async with session.begin():
        job_user = await _job_user_for_notification(session, job_id)
        audit_log = await AdminAuditService(session).log(
            admin_identifier=str(admin),
            action="job_fail_refund",
            request=request,
            target_type="generation_job",
            target_id=str(job_id),
            metadata={
                "reason": reason,
                "old_status": result["old_status"],
                "new_status": result["new_status"],
                "refunded": result["refunded"],
            },
        )
        audit_log_id = audit_log.id

    if result["new_status"] == JobStatus.FAILED.value and job_user is not None:
        notification_sent, warning = await _send_telegram_notification(
            telegram_id=job_user.telegram_id,
            message=(
                "❌ Генерация отменена администратором\n\n"
                "Средства возвращены на баланс.\n"
                f"Причина: {reason}"
            ),
        )

    return AdminActionResponse(
        success=True,
        target_id=str(job_id),
        action="job_fail_refund",
        audit_log_id=audit_log_id,
        old_state=str(result["old_status"]),
        new_state=str(result["new_status"]),
        telegram_notification_sent=notification_sent,
        warning=warning,
    )


@router.post("/jobs/retry-waiting", response_model=AdminActionResponse)
async def admin_retry_waiting_jobs(
    payload: RetryWaitingJobsRequest,
    request: Request,
    session: SessionDep,
    admin: AdminDep,
    settings: ActionSettingsDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AdminActionResponse:
    reason = _validate_action_reason(payload.reason, settings)
    retry_result = await _retry_debug_waiting_jobs(
        session=session,
        statuses=[JobStatus.WAITING_FOR_GPU.value, JobStatus.WAITING_FOR_POD.value],
        limit=limit,
    )
    async with session.begin():
        audit_log = await AdminAuditService(session).log(
            admin_identifier=str(admin),
            action="retry_waiting_jobs",
            request=request,
            target_type="generation_jobs",
            metadata={
                "reason": reason,
                "enqueued": retry_result.enqueued,
                "job_ids": [str(job_id) for job_id in retry_result.job_ids],
            },
        )
        audit_log_id = audit_log.id

    return AdminActionResponse(
        success=True,
        target_id="waiting_generation_jobs",
        action="retry_waiting_jobs",
        audit_log_id=audit_log_id,
        enqueued=retry_result.enqueued,
        job_ids=retry_result.job_ids,
    )


@router.post("/runpod/pods/{runpod_pod_id}/terminate", response_model=AdminActionResponse)
async def admin_terminate_runpod_pod(
    runpod_pod_id: str,
    payload: TerminateRunPodRequest,
    request: Request,
    session: SessionDep,
    admin: AdminDep,
    settings: ActionSettingsDep,
) -> AdminActionResponse:
    reason = _validate_action_reason(payload.reason, settings)
    old_state: str | None = None
    already_terminated = False

    async with session.begin():
        pod = await _get_runpod_pod_for_update(session, runpod_pod_id)
        old_state = pod.status
        if pod.status == PodStatus.BUSY.value or pod.active_job_id or pod.current_job_id:
            raise AppError(
                "Busy RunPod pods cannot be terminated from admin",
                code="pod_busy",
                status_code=409,
            )
        already_terminated = pod.status in {
            PodStatus.TERMINATED.value,
            PodStatus.DELETED.value,
            PodStatus.STOPPING.value,
        }

    if not already_terminated:
        try:
            await anyio.to_thread.run_sync(lambda: _terminate_runpod_pod(settings, runpod_pod_id))
        except RunPodError as exc:
            if "HTTP 404" not in str(exc) and "not found" not in str(exc).lower():
                raise AppError(str(exc), code="runpod_terminate_failed", status_code=502) from exc

    async with session.begin():
        pod = await _get_runpod_pod_for_update(session, runpod_pod_id)
        now = datetime.now(UTC)
        pod.status = PodStatus.TERMINATED.value
        pod.active_job_id = None
        pod.current_job_id = None
        pod.terminated_at = pod.terminated_at or now
        pod.updated_at = now
        audit_log = await AdminAuditService(session).log(
            admin_identifier=str(admin),
            action="runpod_pod_terminate",
            request=request,
            target_type="runpod_pod",
            target_id=runpod_pod_id,
            metadata={
                "reason": reason,
                "old_status": old_state,
                "already_terminated": already_terminated,
            },
        )
        audit_log_id = audit_log.id

    return AdminActionResponse(
        success=True,
        target_id=runpod_pod_id,
        action="runpod_pod_terminate",
        audit_log_id=audit_log_id,
        old_state=old_state,
        new_state=PodStatus.TERMINATED.value,
    )


@router.post("/users/{user_id}/block", response_model=AdminActionResponse)
async def admin_block_user(
    user_id: UUID,
    payload: UserBlockRequest,
    request: Request,
    session: SessionDep,
    admin: AdminDep,
    settings: ActionSettingsDep,
) -> AdminActionResponse:
    return await _set_user_banned(
        user_id=user_id,
        is_banned=True,
        payload=payload,
        request=request,
        session=session,
        admin=admin,
        settings=settings,
    )


@router.post("/users/{user_id}/unblock", response_model=AdminActionResponse)
async def admin_unblock_user(
    user_id: UUID,
    payload: UserBlockRequest,
    request: Request,
    session: SessionDep,
    admin: AdminDep,
    settings: ActionSettingsDep,
) -> AdminActionResponse:
    return await _set_user_banned(
        user_id=user_id,
        is_banned=False,
        payload=payload,
        request=request,
        session=session,
        admin=admin,
        settings=settings,
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
    provider_amount = _optional_decimal(metadata.get("provider_amount"))
    payment_url = _optional_str(metadata.get("payment_url"))
    if provider_currency is None:
        nested = metadata.get("ultronlab_metadata")
        if isinstance(nested, dict):
            provider_currency = _optional_str(nested.get("provider_currency"))
            provider_amount = _optional_decimal(nested.get("provider_amount"))
            payment_url = _optional_str(nested.get("payment_url"))
    return AdminPaymentListItem(
        id=payment.id,
        user=AdminUserSummary(id=user.id, telegram_id=user.telegram_id, username=user.username),
        provider=payment.provider,
        provider_invoice_id=payment.provider_invoice_id,
        amount_usd=payment.amount_usd,
        currency=payment.currency,
        provider_currency=provider_currency,
        provider_amount=provider_amount,
        payment_url=payment_url,
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


async def _get_business_account(
    session: AsyncSession,
    business_account_id: UUID,
) -> BusinessAccount:
    account = await session.get(BusinessAccount, business_account_id)
    if account is None:
        raise AppError(
            "Business account not found",
            code="business_account_not_found",
            status_code=404,
        )
    return account


async def _get_active_business_account(
    session: AsyncSession,
    business_account_id: UUID,
) -> BusinessAccount:
    account = await _get_business_account(session, business_account_id)
    if account.status != BusinessAccountStatus.ACTIVE.value:
        raise AppError(
            "Business account is not active",
            code="business_account_inactive",
            status_code=400,
        )
    return account


async def _get_user_for_admin_member_payload(
    session: AsyncSession,
    payload: BusinessMemberAddRequest,
) -> User:
    if payload.user_id is not None:
        user = await session.get(User, payload.user_id)
    elif payload.telegram_id is not None:
        result = await session.execute(select(User).where(User.telegram_id == payload.telegram_id))
        user = result.scalar_one_or_none()
    else:
        raise AppError("telegram_id or user_id is required", code="member_user_required")
    if user is None:
        raise AppError("User not found", code="user_not_found", status_code=404)
    return user


async def _ensure_no_other_active_business_membership(
    session: AsyncSession,
    user_id: UUID,
    business_account_id: UUID,
) -> None:
    result = await session.execute(
        select(BusinessAccountMember)
        .join(BusinessAccount, BusinessAccount.id == BusinessAccountMember.business_account_id)
        .where(
            BusinessAccountMember.user_id == user_id,
            BusinessAccountMember.business_account_id != business_account_id,
            BusinessAccountMember.is_active.is_(True),
            BusinessAccount.status == BusinessAccountStatus.ACTIVE.value,
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        raise AppError(
            "User already has an active business account",
            code="business_member_already_active",
            status_code=400,
        )


async def _get_business_member_for_update(
    session: AsyncSession,
    business_account_id: UUID,
    user_id: UUID,
) -> BusinessAccountMember | None:
    result = await session.execute(
        select(BusinessAccountMember)
        .where(
            BusinessAccountMember.business_account_id == business_account_id,
            BusinessAccountMember.user_id == user_id,
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def _active_owner_telegram_ids(
    session: AsyncSession,
    business_account_id: UUID,
) -> list[int]:
    result = await session.execute(
        select(User.telegram_id)
        .join(BusinessAccountMember, BusinessAccountMember.user_id == User.id)
        .where(
            BusinessAccountMember.business_account_id == business_account_id,
            BusinessAccountMember.role == BusinessAccountMemberRole.OWNER.value,
            BusinessAccountMember.is_active.is_(True),
        )
    )
    return [int(item) for item in result.scalars()]


async def _job_user_for_notification(session: AsyncSession, job_id: UUID) -> User | None:
    result = await session.execute(
        select(User)
        .join(GenerationJob, GenerationJob.user_id == User.id)
        .where(GenerationJob.id == job_id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_runpod_pod_for_update(session: AsyncSession, runpod_pod_id: str) -> RunpodPod:
    result = await session.execute(
        select(RunpodPod).where(RunpodPod.runpod_pod_id == runpod_pod_id).with_for_update()
    )
    pod = result.scalar_one_or_none()
    if pod is None:
        raise AppError("RunPod pod not found", code="runpod_pod_not_found", status_code=404)
    return pod


async def _set_user_banned(
    *,
    user_id: UUID,
    is_banned: bool,
    payload: UserBlockRequest,
    request: Request,
    session: AsyncSession,
    admin: AdminPrincipal,
    settings: Settings,
) -> AdminActionResponse:
    reason = _validate_action_reason(payload.reason, settings)
    async with session.begin():
        user = await session.get(User, user_id, with_for_update=True)
        if user is None:
            raise AppError("User not found", code="user_not_found", status_code=404)
        old_state = "blocked" if user.is_banned else "active"
        user.is_banned = is_banned
        action = "user_block" if is_banned else "user_unblock"
        audit_log = await AdminAuditService(session).log(
            admin_identifier=str(admin),
            action=action,
            request=request,
            target_type="user",
            target_id=str(user_id),
            metadata={"reason": reason},
        )
        audit_log_id = audit_log.id
    return AdminActionResponse(
        success=True,
        target_id=str(user_id),
        action=action,
        audit_log_id=audit_log_id,
        old_state=old_state,
        new_state="blocked" if is_banned else "active",
    )


async def _send_telegram_notification(
    *,
    telegram_id: int,
    message: str,
) -> tuple[bool, str | None]:
    try:
        sent = await TelegramNotificationService().send_message(
            telegram_id=telegram_id,
            message=message,
        )
        return sent, None if sent else "Telegram returned ok=false"
    except Exception as exc:
        return False, f"Telegram notification failed: {exc.__class__.__name__}"


def _validate_action_reason(reason: str, settings: Settings) -> str:
    normalized = reason.strip()
    if settings.admin_require_action_reason and not normalized:
        raise AppError("Action reason is required", code="admin_reason_required", status_code=400)
    return normalized or "admin action"


def _validate_admin_amount(amount: Decimal, settings: Settings) -> Decimal:
    normalized = amount.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    if normalized <= Decimal("0"):
        raise AppError("Amount must be positive", code="invalid_amount", status_code=400)
    if normalized > settings.admin_max_manual_topup_usd:
        raise AppError(
            f"Amount exceeds admin limit ${settings.admin_max_manual_topup_usd}",
            code="admin_amount_limit_exceeded",
            status_code=400,
        )
    return normalized


def _validate_business_role(role: str) -> str:
    normalized = role.strip().lower()
    if normalized not in {
        BusinessAccountMemberRole.OWNER.value,
        BusinessAccountMemberRole.MEMBER.value,
    }:
        raise AppError("Unsupported business member role", code="invalid_business_role")
    return normalized


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


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _truncate(value: str | None, max_len: int) -> str | None:
    if value is None or len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."
