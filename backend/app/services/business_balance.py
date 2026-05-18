from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.business_account import BusinessAccount
from backend.app.models.business_account_member import BusinessAccountMember
from backend.app.models.business_balance_transaction import BusinessBalanceTransaction
from shared.app.enums import BusinessAccountStatus, BusinessBalanceTransactionType
from shared.app.exceptions import AppError

logger = logging.getLogger(__name__)
MONEY_QUANT = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class BusinessAccountSelection:
    account: BusinessAccount
    membership: BusinessAccountMember


@dataclass(frozen=True, slots=True)
class BusinessBalanceMutation:
    account: BusinessAccount
    transaction: BusinessBalanceTransaction | None


class BusinessBalanceService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_business_account_for_user(
        self,
        user_id: UUID,
    ) -> BusinessAccountSelection | None:
        result = await self._session.execute(
            select(BusinessAccount, BusinessAccountMember)
            .join(
                BusinessAccountMember,
                BusinessAccountMember.business_account_id == BusinessAccount.id,
            )
            .where(
                BusinessAccountMember.user_id == user_id,
                BusinessAccountMember.is_active.is_(True),
                BusinessAccount.status == BusinessAccountStatus.ACTIVE.value,
            )
            .order_by(BusinessAccountMember.created_at.desc())
        )
        rows = result.all()
        if not rows:
            return None
        if len(rows) > 1:
            logger.warning(
                "User has multiple active business memberships user_id=%s count=%s",
                user_id,
                len(rows),
            )
        account, membership = rows[0]
        return BusinessAccountSelection(account=account, membership=membership)

    async def reserve_business_balance_in_transaction(
        self,
        *,
        business_account_id: UUID,
        user_id: UUID,
        job_id: UUID,
        amount_usd: Decimal,
        reason: str = "Funds frozen for generation job",
    ) -> BusinessBalanceMutation:
        amount = self._normalize_positive_amount(amount_usd)
        account = await self._get_locked_account(business_account_id, require_active=True)
        if account.available_usd < amount:
            raise AppError(
                (
                    "Недостаточно средств на балансе компании. "
                    f"Нужно ${amount}, доступно ${account.available_usd}"
                ),
                code="business_insufficient_balance",
                status_code=402,
            )

        account.available_usd = self._money(account.available_usd - amount)
        account.frozen_usd = self._money(account.frozen_usd + amount)
        transaction = self._add_transaction(
            business_account_id=account.id,
            user_id=user_id,
            generation_job_id=job_id,
            transaction_type=BusinessBalanceTransactionType.HOLD.value,
            amount_usd=amount,
            balance_available_after=account.available_usd,
            balance_frozen_after=account.frozen_usd,
            reason=reason,
        )
        await self._session.flush()
        return BusinessBalanceMutation(account=account, transaction=transaction)

    async def capture_business_balance_in_transaction(
        self,
        *,
        business_account_id: UUID,
        job_id: UUID,
        amount_usd: Decimal,
        user_id: UUID | None = None,
        reason: str = "Business frozen funds captured for generation job",
    ) -> BusinessBalanceMutation:
        amount = self._normalize_positive_amount(amount_usd)
        account = await self._get_locked_account(business_account_id)
        if await self._has_job_transaction(
            job_id,
            {BusinessBalanceTransactionType.CAPTURE.value},
        ):
            return BusinessBalanceMutation(account=account, transaction=None)
        if await self._has_job_transaction(
            job_id,
            {
                BusinessBalanceTransactionType.REFUND.value,
                BusinessBalanceTransactionType.RELEASE.value,
            },
        ):
            raise AppError(
                "Business frozen balance was already returned",
                code="business_hold_already_returned",
                status_code=400,
            )
        if account.frozen_usd < amount:
            raise AppError(
                "Insufficient business frozen balance",
                code="business_insufficient_frozen_balance",
                status_code=400,
            )

        account.frozen_usd = self._money(account.frozen_usd - amount)
        transaction = self._add_transaction(
            business_account_id=account.id,
            user_id=user_id,
            generation_job_id=job_id,
            transaction_type=BusinessBalanceTransactionType.CAPTURE.value,
            amount_usd=amount,
            balance_available_after=account.available_usd,
            balance_frozen_after=account.frozen_usd,
            reason=reason,
        )
        await self._session.flush()
        return BusinessBalanceMutation(account=account, transaction=transaction)

    async def refund_business_frozen_balance_in_transaction(
        self,
        *,
        business_account_id: UUID,
        job_id: UUID,
        amount_usd: Decimal,
        user_id: UUID | None = None,
        reason: str = "Business frozen funds refunded",
    ) -> BusinessBalanceMutation:
        amount = self._normalize_positive_amount(amount_usd)
        account = await self._get_locked_account(business_account_id)
        if await self._has_job_transaction(
            job_id,
            {BusinessBalanceTransactionType.CAPTURE.value},
        ):
            return BusinessBalanceMutation(account=account, transaction=None)
        if await self._has_job_transaction(
            job_id,
            {
                BusinessBalanceTransactionType.REFUND.value,
                BusinessBalanceTransactionType.RELEASE.value,
            },
        ):
            return BusinessBalanceMutation(account=account, transaction=None)
        if account.frozen_usd < amount:
            raise AppError(
                "Insufficient business frozen balance",
                code="business_insufficient_frozen_balance",
                status_code=400,
            )

        account.frozen_usd = self._money(account.frozen_usd - amount)
        account.available_usd = self._money(account.available_usd + amount)
        transaction = self._add_transaction(
            business_account_id=account.id,
            user_id=user_id,
            generation_job_id=job_id,
            transaction_type=BusinessBalanceTransactionType.REFUND.value,
            amount_usd=amount,
            balance_available_after=account.available_usd,
            balance_frozen_after=account.frozen_usd,
            reason=reason,
        )
        await self._session.flush()
        return BusinessBalanceMutation(account=account, transaction=transaction)

    async def manual_topup_business_balance(
        self,
        *,
        business_account_id: UUID,
        amount_usd: Decimal,
        reason: str,
        admin_note: str | None = None,
    ) -> BusinessBalanceMutation:
        amount = self._normalize_positive_amount(amount_usd)
        account = await self._get_locked_account(business_account_id, require_active=True)
        account.available_usd = self._money(account.available_usd + amount)
        metadata = {"admin_note": admin_note} if admin_note else None
        transaction = self._add_transaction(
            business_account_id=account.id,
            user_id=None,
            generation_job_id=None,
            transaction_type=BusinessBalanceTransactionType.MANUAL_TOPUP.value,
            amount_usd=amount,
            balance_available_after=account.available_usd,
            balance_frozen_after=account.frozen_usd,
            reason=reason,
            transaction_metadata=metadata,
        )
        await self._session.flush()
        return BusinessBalanceMutation(account=account, transaction=transaction)

    async def _get_locked_account(
        self,
        business_account_id: UUID,
        *,
        require_active: bool = False,
    ) -> BusinessAccount:
        result = await self._session.execute(
            select(BusinessAccount)
            .where(BusinessAccount.id == business_account_id)
            .with_for_update()
        )
        account = result.scalar_one_or_none()
        if account is None:
            raise AppError(
                "Business account not found",
                code="business_account_not_found",
                status_code=404,
            )
        if require_active and account.status != BusinessAccountStatus.ACTIVE.value:
            raise AppError(
                "Business account is not active",
                code="business_account_inactive",
                status_code=400,
            )
        return account

    async def _has_job_transaction(self, job_id: UUID, transaction_types: set[str]) -> bool:
        result = await self._session.execute(
            select(BusinessBalanceTransaction.id).where(
                BusinessBalanceTransaction.generation_job_id == job_id,
                BusinessBalanceTransaction.type.in_(transaction_types),
            )
        )
        return result.first() is not None

    def _add_transaction(
        self,
        *,
        business_account_id: UUID,
        user_id: UUID | None,
        generation_job_id: UUID | None,
        transaction_type: str,
        amount_usd: Decimal,
        balance_available_after: Decimal,
        balance_frozen_after: Decimal,
        reason: str | None,
        transaction_metadata: dict[str, Any] | None = None,
    ) -> BusinessBalanceTransaction:
        transaction = BusinessBalanceTransaction(
            business_account_id=business_account_id,
            user_id=user_id,
            generation_job_id=generation_job_id,
            type=transaction_type,
            amount_usd=amount_usd,
            balance_available_after=balance_available_after,
            balance_frozen_after=balance_frozen_after,
            reason=reason,
            transaction_metadata=transaction_metadata,
        )
        self._session.add(transaction)
        return transaction

    async def active_member_count(self, business_account_id: UUID) -> int:
        result = await self._session.execute(
            select(func.count(BusinessAccountMember.id)).where(
                BusinessAccountMember.business_account_id == business_account_id,
                BusinessAccountMember.is_active.is_(True),
            )
        )
        return int(result.scalar_one())

    def _normalize_positive_amount(self, amount: Decimal) -> Decimal:
        normalized = self._money(amount)
        if normalized <= Decimal("0"):
            raise AppError("Amount must be positive", code="invalid_amount", status_code=400)
        return normalized

    def _money(self, amount: Decimal) -> Decimal:
        return amount.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
