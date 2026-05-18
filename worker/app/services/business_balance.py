from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.business_account import BusinessAccount
from backend.app.models.business_balance_transaction import BusinessBalanceTransaction
from shared.app.enums import BusinessBalanceTransactionType

MONEY_QUANT = Decimal("0.0001")


class SyncBusinessBalanceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def capture_frozen_balance(
        self,
        *,
        business_account_id: UUID,
        user_id: UUID | None,
        amount_usd: Decimal,
        related_job_id: UUID,
        reason: str,
    ) -> BusinessAccount:
        amount = self._normalize_positive_amount(amount_usd)
        account = self._get_locked_account(business_account_id)
        if self._has_job_transaction(
            related_job_id,
            {BusinessBalanceTransactionType.CAPTURE.value},
        ):
            return account
        if self._has_job_transaction(
            related_job_id,
            {
                BusinessBalanceTransactionType.REFUND.value,
                BusinessBalanceTransactionType.RELEASE.value,
            },
        ):
            raise RuntimeError("Business job frozen balance was already returned")
        if account.frozen_usd < amount:
            raise RuntimeError("Insufficient business frozen balance")

        account.frozen_usd = self._money(account.frozen_usd - amount)
        self._add_transaction(
            business_account_id=business_account_id,
            user_id=user_id,
            generation_job_id=related_job_id,
            transaction_type=BusinessBalanceTransactionType.CAPTURE.value,
            amount_usd=amount,
            balance_available_after=account.available_usd,
            balance_frozen_after=account.frozen_usd,
            reason=reason,
        )
        return account

    def refund_frozen_balance(
        self,
        *,
        business_account_id: UUID,
        user_id: UUID | None,
        amount_usd: Decimal,
        related_job_id: UUID,
        reason: str,
    ) -> BusinessAccount:
        amount = self._normalize_positive_amount(amount_usd)
        account = self._get_locked_account(business_account_id)
        if self._has_job_transaction(
            related_job_id,
            {BusinessBalanceTransactionType.CAPTURE.value},
        ):
            return account
        if self._has_job_transaction(
            related_job_id,
            {
                BusinessBalanceTransactionType.REFUND.value,
                BusinessBalanceTransactionType.RELEASE.value,
            },
        ):
            return account
        if account.frozen_usd < amount:
            raise RuntimeError("Insufficient business frozen balance")

        account.frozen_usd = self._money(account.frozen_usd - amount)
        account.available_usd = self._money(account.available_usd + amount)
        self._add_transaction(
            business_account_id=business_account_id,
            user_id=user_id,
            generation_job_id=related_job_id,
            transaction_type=BusinessBalanceTransactionType.REFUND.value,
            amount_usd=amount,
            balance_available_after=account.available_usd,
            balance_frozen_after=account.frozen_usd,
            reason=reason,
        )
        return account

    def _get_locked_account(self, business_account_id: UUID) -> BusinessAccount:
        account = self._session.execute(
            select(BusinessAccount)
            .where(BusinessAccount.id == business_account_id)
            .with_for_update()
        ).scalar_one_or_none()
        if account is None:
            raise RuntimeError("Business account not found")
        return account

    def _add_transaction(
        self,
        *,
        business_account_id: UUID,
        user_id: UUID | None,
        transaction_type: str,
        amount_usd: Decimal,
        balance_available_after: Decimal,
        balance_frozen_after: Decimal,
        reason: str,
        generation_job_id: UUID,
    ) -> None:
        self._session.add(
            BusinessBalanceTransaction(
                business_account_id=business_account_id,
                user_id=user_id,
                generation_job_id=generation_job_id,
                type=transaction_type,
                amount_usd=amount_usd,
                balance_available_after=balance_available_after,
                balance_frozen_after=balance_frozen_after,
                reason=reason,
            )
        )

    def _has_job_transaction(self, job_id: UUID, transaction_types: set[str]) -> bool:
        result = self._session.execute(
            select(BusinessBalanceTransaction.id).where(
                BusinessBalanceTransaction.generation_job_id == job_id,
                BusinessBalanceTransaction.type.in_(transaction_types),
            )
        )
        return result.first() is not None

    def _normalize_positive_amount(self, amount: Decimal) -> Decimal:
        normalized = self._money(amount)
        if normalized <= Decimal("0"):
            raise RuntimeError("Amount must be positive")
        return normalized

    def _money(self, amount: Decimal) -> Decimal:
        return amount.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
