from __future__ import annotations

import logging
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.debug_access import require_debug_access
from backend.app.models.business_account import BusinessAccount
from backend.app.models.business_account_member import BusinessAccountMember
from backend.app.models.business_balance_transaction import BusinessBalanceTransaction
from backend.app.models.user import User
from backend.app.schemas.business import (
    BusinessAccountCreateRequest,
    BusinessAccountMemberAddRequest,
    BusinessAccountMemberAddResponse,
    BusinessAccountMemberDeactivateResponse,
    BusinessAccountMemberResponse,
    BusinessAccountResponse,
    BusinessAccountsResponse,
    BusinessAccountTopUpRequest,
    BusinessAccountTopUpResponse,
    BusinessBalanceTransactionResponse,
    BusinessBalanceTransactionsResponse,
    BusinessUsageMemberResponse,
    BusinessUsageResponse,
)
from backend.app.services.business_balance import BusinessBalanceService
from backend.app.services.telegram_notify import TelegramNotificationService
from shared.app.database import get_session
from shared.app.enums import (
    BusinessAccountMemberRole,
    BusinessAccountStatus,
    BusinessBalanceTransactionType,
)
from shared.app.exceptions import AppError

router = APIRouter(
    prefix="/debug/business-accounts",
    tags=["debug"],
    dependencies=[Depends(require_debug_access)],
)
SessionDep = Annotated[AsyncSession, Depends(get_session)]
Money = Decimal("0.0001")
logger = logging.getLogger(__name__)


@router.post("", response_model=BusinessAccountResponse)
async def create_business_account(
    payload: BusinessAccountCreateRequest,
    session: SessionDep,
) -> BusinessAccountResponse:
    async with session.begin():
        account = BusinessAccount(
            name=payload.name.strip(),
            status=BusinessAccountStatus.ACTIVE.value,
        )
        session.add(account)
        await session.flush()
        return await _business_account_response(session, account)


@router.get("", response_model=BusinessAccountsResponse)
async def list_business_accounts(session: SessionDep) -> BusinessAccountsResponse:
    result = await session.execute(
        select(BusinessAccount).order_by(BusinessAccount.created_at.desc())
    )
    return BusinessAccountsResponse(
        items=[await _business_account_response(session, account) for account in result.scalars()]
    )


@router.post("/{business_account_id}/top-up", response_model=BusinessAccountTopUpResponse)
async def top_up_business_account(
    business_account_id: UUID,
    payload: BusinessAccountTopUpRequest,
    session: SessionDep,
) -> BusinessAccountTopUpResponse:
    owner_telegram_ids: list[int] = []
    async with session.begin():
        mutation = await BusinessBalanceService(session).manual_topup_business_balance(
            business_account_id=business_account_id,
            amount_usd=payload.amount_usd,
            reason=payload.reason,
            admin_note=payload.admin_note,
        )
        if mutation.transaction is None:
            raise AppError("Business top-up transaction was not created", code="topup_failed")
        await session.refresh(mutation.account)
        response_account = await _business_account_response(session, mutation.account)
        owner_telegram_ids = await _active_owner_telegram_ids(session, business_account_id)
        transaction_id = mutation.transaction.id

    notification_sent = 0
    for telegram_id in owner_telegram_ids:
        message = (
            "✅ Баланс компании пополнен\n\n"
            f"Компания: {response_account.name}\n"
            f"Зачислено: ${_money(payload.amount_usd)}\n"
            f"Текущий баланс компании: ${_money(response_account.available_usd)}"
        )
        try:
            if await TelegramNotificationService().send_message(
                telegram_id=telegram_id,
                message=message,
            ):
                notification_sent += 1
        except Exception:
            logger.warning(
                "Business top-up notification failed business_account_id=%s telegram_id=%s",
                business_account_id,
                telegram_id,
            )

    return BusinessAccountTopUpResponse(
        business_account=response_account,
        transaction_id=transaction_id,
        amount_usd=_money(payload.amount_usd),
        notification_sent=notification_sent,
    )


@router.post("/{business_account_id}/members", response_model=BusinessAccountMemberAddResponse)
async def add_business_account_member(
    business_account_id: UUID,
    payload: BusinessAccountMemberAddRequest,
    session: SessionDep,
) -> BusinessAccountMemberAddResponse:
    role = _validate_member_role(payload.role)
    if payload.telegram_id is None and payload.user_id is None:
        raise AppError("telegram_id or user_id is required", code="member_user_required")

    async with session.begin():
        account = await _get_active_business_account(session, business_account_id)
        user = await _get_user_for_member_payload(session, payload)
        await _ensure_user_has_no_other_active_business(session, user.id, business_account_id)

        result = await session.execute(
            select(BusinessAccountMember)
            .where(
                BusinessAccountMember.business_account_id == business_account_id,
                BusinessAccountMember.user_id == user.id,
            )
            .with_for_update()
        )
        member = result.scalar_one_or_none()
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
        await session.refresh(member)
        response = _member_response(member, user)

    notification_sent = False
    try:
        notification_sent = await TelegramNotificationService().send_message(
            telegram_id=user.telegram_id,
            message=(
                "🏢 Вам подключён бизнес-баланс\n\n"
                f"Компания: {account.name}\n"
                "Теперь генерации будут оплачиваться с баланса компании."
            ),
        )
    except Exception:
        logger.warning(
            "Business member notification failed business_account_id=%s user_id=%s",
            business_account_id,
            user.id,
        )

    return BusinessAccountMemberAddResponse(member=response, notification_sent=notification_sent)


@router.delete(
    "/{business_account_id}/members/{user_id}",
    response_model=BusinessAccountMemberDeactivateResponse,
)
async def deactivate_business_account_member(
    business_account_id: UUID,
    user_id: UUID,
    session: SessionDep,
) -> BusinessAccountMemberDeactivateResponse:
    deactivated = False
    async with session.begin():
        result = await session.execute(
            select(BusinessAccountMember)
            .where(
                BusinessAccountMember.business_account_id == business_account_id,
                BusinessAccountMember.user_id == user_id,
            )
            .with_for_update()
        )
        member = result.scalar_one_or_none()
        if member is not None and member.is_active:
            member.is_active = False
            deactivated = True
    return BusinessAccountMemberDeactivateResponse(
        business_account_id=business_account_id,
        user_id=user_id,
        deactivated=deactivated,
    )


@router.get(
    "/{business_account_id}/transactions",
    response_model=BusinessBalanceTransactionsResponse,
)
async def list_business_account_transactions(
    business_account_id: UUID,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> BusinessBalanceTransactionsResponse:
    await _get_business_account(session, business_account_id)
    result = await session.execute(
        select(BusinessBalanceTransaction)
        .where(BusinessBalanceTransaction.business_account_id == business_account_id)
        .order_by(BusinessBalanceTransaction.created_at.desc())
        .limit(limit)
    )
    return BusinessBalanceTransactionsResponse(
        items=[_transaction_response(transaction) for transaction in result.scalars()]
    )


@router.get("/{business_account_id}/usage", response_model=BusinessUsageResponse)
async def get_business_account_usage(
    business_account_id: UUID,
    session: SessionDep,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> BusinessUsageResponse:
    account = await _get_business_account(session, business_account_id)
    transaction_filter = [BusinessBalanceTransaction.business_account_id == business_account_id]
    if date_from is not None:
        transaction_filter.append(BusinessBalanceTransaction.created_at >= date_from)
    if date_to is not None:
        transaction_filter.append(BusinessBalanceTransaction.created_at <= date_to)

    totals = await _business_transaction_totals(session, transaction_filter)
    members = await _business_usage_members(session, business_account_id, date_from, date_to)
    return BusinessUsageResponse(
        business_account=await _business_account_response(session, account),
        topups_usd=totals.get(BusinessBalanceTransactionType.MANUAL_TOPUP.value, Decimal("0.0000")),
        spent_usd=totals.get(BusinessBalanceTransactionType.CAPTURE.value, Decimal("0.0000")),
        refunded_usd=_money(
            totals.get(BusinessBalanceTransactionType.REFUND.value, Decimal("0.0000"))
            + totals.get(BusinessBalanceTransactionType.RELEASE.value, Decimal("0.0000"))
        ),
        members=members,
    )


async def _business_account_response(
    session: AsyncSession,
    account: BusinessAccount,
) -> BusinessAccountResponse:
    active_members_count = await BusinessBalanceService(session).active_member_count(account.id)
    return BusinessAccountResponse(
        id=account.id,
        name=account.name,
        status=account.status,
        available_usd=account.available_usd,
        frozen_usd=account.frozen_usd,
        active_members_count=active_members_count,
        created_at=account.created_at,
        updated_at=account.updated_at,
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


async def _get_user_for_member_payload(
    session: AsyncSession,
    payload: BusinessAccountMemberAddRequest,
) -> User:
    if payload.user_id is not None:
        user = await session.get(User, payload.user_id)
    else:
        result = await session.execute(select(User).where(User.telegram_id == payload.telegram_id))
        user = result.scalar_one_or_none()
    if user is None:
        raise AppError("User not found", code="user_not_found", status_code=404)
    return user


async def _ensure_user_has_no_other_active_business(
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


async def _business_transaction_totals(
    session: AsyncSession,
    filters: list[object],
) -> dict[str, Decimal]:
    result = await session.execute(
        select(
            BusinessBalanceTransaction.type,
            func.coalesce(func.sum(func.abs(BusinessBalanceTransaction.amount_usd)), 0),
        )
        .where(*filters)
        .group_by(BusinessBalanceTransaction.type)
    )
    return {str(row[0]): _money(Decimal(row[1])) for row in result.all()}


async def _business_usage_members(
    session: AsyncSession,
    business_account_id: UUID,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list[BusinessUsageMemberResponse]:
    member_result = await session.execute(
        select(BusinessAccountMember, User)
        .join(User, User.id == BusinessAccountMember.user_id)
        .where(
            BusinessAccountMember.business_account_id == business_account_id,
            BusinessAccountMember.is_active.is_(True),
        )
        .order_by(BusinessAccountMember.created_at.asc())
    )
    members = list(member_result.all())
    user_ids = [member.user_id for member, _ in members]
    spend_by_user: dict[UUID, tuple[int, Decimal]] = {}
    if user_ids:
        filters = [
            BusinessBalanceTransaction.business_account_id == business_account_id,
            BusinessBalanceTransaction.user_id.in_(user_ids),
            BusinessBalanceTransaction.type == BusinessBalanceTransactionType.CAPTURE.value,
        ]
        if date_from is not None:
            filters.append(BusinessBalanceTransaction.created_at >= date_from)
        if date_to is not None:
            filters.append(BusinessBalanceTransaction.created_at <= date_to)
        spend_result = await session.execute(
            select(
                BusinessBalanceTransaction.user_id,
                func.count(func.distinct(BusinessBalanceTransaction.generation_job_id)),
                func.coalesce(func.sum(func.abs(BusinessBalanceTransaction.amount_usd)), 0),
            )
            .where(*filters)
            .group_by(BusinessBalanceTransaction.user_id)
        )
        spend_by_user = {
            row[0]: (int(row[1]), _money(Decimal(row[2]))) for row in spend_result.all()
        }

    return [
        BusinessUsageMemberResponse(
            user_id=user.id,
            telegram_id=user.telegram_id,
            username=user.username,
            role=member.role,
            generations_count=spend_by_user.get(user.id, (0, Decimal("0.0000")))[0],
            spent_usd=spend_by_user.get(user.id, (0, Decimal("0.0000")))[1],
        )
        for member, user in members
    ]


def _member_response(member: BusinessAccountMember, user: User) -> BusinessAccountMemberResponse:
    return BusinessAccountMemberResponse(
        id=member.id,
        business_account_id=member.business_account_id,
        user_id=member.user_id,
        telegram_id=user.telegram_id,
        role=member.role,
        is_active=member.is_active,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


def _transaction_response(
    transaction: BusinessBalanceTransaction,
) -> BusinessBalanceTransactionResponse:
    return BusinessBalanceTransactionResponse(
        id=transaction.id,
        business_account_id=transaction.business_account_id,
        user_id=transaction.user_id,
        generation_job_id=transaction.generation_job_id,
        type=transaction.type,
        amount_usd=transaction.amount_usd,
        balance_available_after=transaction.balance_available_after,
        balance_frozen_after=transaction.balance_frozen_after,
        reason=transaction.reason,
        created_at=transaction.created_at,
    )


def _validate_member_role(role: str) -> str:
    normalized = role.strip().lower()
    if normalized not in {
        BusinessAccountMemberRole.OWNER.value,
        BusinessAccountMemberRole.MEMBER.value,
    }:
        raise AppError("Unsupported business member role", code="invalid_business_role")
    return normalized


def _money(value: Decimal) -> Decimal:
    return value.quantize(Money, rounding=ROUND_HALF_UP)
