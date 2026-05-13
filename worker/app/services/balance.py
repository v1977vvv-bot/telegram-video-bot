from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.balance_account import BalanceAccount
from backend.app.models.balance_transaction import BalanceTransaction
from shared.app.enums import BalanceTransactionType

MONEY_QUANT = Decimal("0.0001")


class SyncBalanceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def capture_frozen_balance(
        self,
        *,
        user_id: UUID,
        amount_usd: Decimal,
        related_job_id: UUID | None,
        reason: str,
    ) -> BalanceAccount:
        amount = self._normalize_positive_amount(amount_usd)
        account = self._get_or_create_locked_account(user_id)
        if related_job_id is not None:
            if self._has_job_transaction(
                related_job_id,
                {BalanceTransactionType.CAPTURE.value},
            ):
                return account
            if self._has_job_transaction(
                related_job_id,
                {
                    BalanceTransactionType.REFUND.value,
                    BalanceTransactionType.RELEASE.value,
                },
            ):
                raise RuntimeError("Job frozen balance was already returned")

        if account.frozen_usd < amount:
            raise RuntimeError("Insufficient frozen balance")

        account.frozen_usd = self._money(account.frozen_usd - amount)
        self._add_transaction(
            user_id=user_id,
            generation_job_id=related_job_id,
            transaction_type=BalanceTransactionType.CAPTURE.value,
            amount_usd=amount,
            balance_available_after=account.available_usd,
            balance_frozen_after=account.frozen_usd,
            reason=reason,
        )
        return account

    def refund_frozen_balance(
        self,
        *,
        user_id: UUID,
        amount_usd: Decimal,
        related_job_id: UUID | None,
        reason: str,
    ) -> BalanceAccount:
        amount = self._normalize_positive_amount(amount_usd)
        account = self._get_or_create_locked_account(user_id)
        if related_job_id is not None:
            if self._has_job_transaction(
                related_job_id,
                {BalanceTransactionType.CAPTURE.value},
            ):
                return account
            if self._has_job_transaction(
                related_job_id,
                {
                    BalanceTransactionType.REFUND.value,
                    BalanceTransactionType.RELEASE.value,
                },
            ):
                return account

        if account.frozen_usd < amount:
            raise RuntimeError("Insufficient frozen balance")

        account.frozen_usd = self._money(account.frozen_usd - amount)
        account.available_usd = self._money(account.available_usd + amount)
        self._add_transaction(
            user_id=user_id,
            generation_job_id=related_job_id,
            transaction_type=BalanceTransactionType.REFUND.value,
            amount_usd=amount,
            balance_available_after=account.available_usd,
            balance_frozen_after=account.frozen_usd,
            reason=reason,
        )
        return account

    def _get_or_create_locked_account(self, user_id: UUID) -> BalanceAccount:
        account = self._session.execute(
            select(BalanceAccount).where(BalanceAccount.user_id == user_id).with_for_update()
        ).scalar_one_or_none()
        if account is not None:
            return account

        account = BalanceAccount(user_id=user_id)
        self._session.add(account)
        self._session.flush()
        return account

    def _add_transaction(
        self,
        *,
        user_id: UUID,
        transaction_type: str,
        amount_usd: Decimal,
        balance_available_after: Decimal,
        balance_frozen_after: Decimal,
        reason: str,
        generation_job_id: UUID | None = None,
    ) -> None:
        self._session.add(
            BalanceTransaction(
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
            select(BalanceTransaction.id).where(
                BalanceTransaction.generation_job_id == job_id,
                BalanceTransaction.type.in_(transaction_types),
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
