from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.balance_account import BalanceAccount
from backend.app.models.balance_transaction import BalanceTransaction
from backend.app.repositories.balances import BalanceRepository
from shared.app.enums import BalanceTransactionType
from shared.app.exceptions import AppError

MONEY_QUANT = Decimal("0.0001")


class BalanceService:
    def __init__(
        self,
        session: AsyncSession,
        repository: BalanceRepository | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or BalanceRepository()

    async def get_account(self, user_id: UUID) -> BalanceAccount | None:
        return await self._repository.get_account(self._session, user_id)

    async def get_or_create_account(self, user_id: UUID) -> BalanceAccount:
        async with self._session.begin():
            return await self._get_or_create_locked_account(user_id)

    async def add_balance(
        self,
        user_id: UUID,
        amount_usd: Decimal,
        reason: str,
        related_payment_id: UUID | None = None,
    ) -> BalanceAccount:
        amount = self._normalize_positive_amount(amount_usd)
        async with self._session.begin():
            return await self.add_balance_in_transaction(
                user_id=user_id,
                amount_usd=amount,
                reason=reason,
                related_payment_id=related_payment_id,
            )

    async def add_balance_in_transaction(
        self,
        user_id: UUID,
        amount_usd: Decimal,
        reason: str,
        related_payment_id: UUID | None = None,
    ) -> BalanceAccount:
        amount = self._normalize_positive_amount(amount_usd)
        account = await self._get_or_create_locked_account(user_id)
        account.available_usd = self._money(account.available_usd + amount)
        self._repository.add_transaction(
            self._session,
            user_id=user_id,
            payment_id=related_payment_id,
            transaction_type=BalanceTransactionType.DEPOSIT.value,
            amount_usd=amount,
            balance_available_after=account.available_usd,
            balance_frozen_after=account.frozen_usd,
            reason=reason,
        )
        return account

    async def admin_adjustment_balance_in_transaction(
        self,
        user_id: UUID,
        amount_usd: Decimal,
        reason: str,
    ) -> tuple[BalanceAccount, BalanceTransaction]:
        amount = self._normalize_positive_amount(amount_usd)
        account = await self._get_or_create_locked_account(user_id)
        account.available_usd = self._money(account.available_usd + amount)
        transaction = self._repository.add_transaction(
            self._session,
            user_id=user_id,
            transaction_type=BalanceTransactionType.ADMIN_ADJUSTMENT.value,
            amount_usd=amount,
            balance_available_after=account.available_usd,
            balance_frozen_after=account.frozen_usd,
            reason=reason,
        )
        await self._session.flush()
        return account, transaction

    async def freeze_balance(
        self,
        user_id: UUID,
        amount_usd: Decimal,
        related_job_id: UUID | None = None,
    ) -> BalanceAccount:
        amount = self._normalize_positive_amount(amount_usd)
        async with self._session.begin():
            return await self.freeze_balance_in_transaction(
                user_id=user_id,
                amount_usd=amount,
                related_job_id=related_job_id,
            )

    async def freeze_balance_in_transaction(
        self,
        user_id: UUID,
        amount_usd: Decimal,
        related_job_id: UUID | None = None,
        reason: str = "Funds frozen for generation job",
    ) -> BalanceAccount:
        amount = self._normalize_positive_amount(amount_usd)
        account = await self._get_or_create_locked_account(user_id)
        if account.available_usd < amount:
            raise AppError(
                f"Недостаточно средств. Нужно ${amount}, доступно ${account.available_usd}",
                code="insufficient_balance",
                status_code=402,
            )
        account.available_usd = self._money(account.available_usd - amount)
        account.frozen_usd = self._money(account.frozen_usd + amount)
        self._repository.add_transaction(
            self._session,
            user_id=user_id,
            generation_job_id=related_job_id,
            transaction_type=BalanceTransactionType.HOLD.value,
            amount_usd=amount,
            balance_available_after=account.available_usd,
            balance_frozen_after=account.frozen_usd,
            reason=reason,
        )
        return account

    async def capture_frozen_balance(
        self,
        user_id: UUID,
        amount_usd: Decimal,
        related_job_id: UUID | None = None,
    ) -> BalanceAccount:
        amount = self._normalize_positive_amount(amount_usd)
        async with self._session.begin():
            return await self.capture_frozen_balance_in_transaction(
                user_id=user_id,
                amount_usd=amount,
                related_job_id=related_job_id,
            )

    async def capture_frozen_balance_in_transaction(
        self,
        user_id: UUID,
        amount_usd: Decimal,
        related_job_id: UUID | None = None,
        reason: str = "Frozen funds captured for generation job",
    ) -> BalanceAccount:
        amount = self._normalize_positive_amount(amount_usd)
        account = await self._get_or_create_locked_account(user_id)
        if account.frozen_usd < amount:
            raise AppError(
                "Insufficient frozen balance",
                code="insufficient_frozen_balance",
                status_code=400,
            )
        account.frozen_usd = self._money(account.frozen_usd - amount)
        self._repository.add_transaction(
            self._session,
            user_id=user_id,
            generation_job_id=related_job_id,
            transaction_type=BalanceTransactionType.CAPTURE.value,
            amount_usd=amount,
            balance_available_after=account.available_usd,
            balance_frozen_after=account.frozen_usd,
            reason=reason,
        )
        return account

    async def refund_frozen_balance(
        self,
        user_id: UUID,
        amount_usd: Decimal,
        related_job_id: UUID | None = None,
    ) -> BalanceAccount:
        amount = self._normalize_positive_amount(amount_usd)
        async with self._session.begin():
            return await self.refund_frozen_balance_in_transaction(
                user_id=user_id,
                amount_usd=amount,
                related_job_id=related_job_id,
            )

    async def refund_frozen_balance_in_transaction(
        self,
        user_id: UUID,
        amount_usd: Decimal,
        related_job_id: UUID | None = None,
        reason: str = "Frozen funds refunded",
    ) -> BalanceAccount:
        amount = self._normalize_positive_amount(amount_usd)
        account = await self._get_or_create_locked_account(user_id)
        if account.frozen_usd < amount:
            raise AppError(
                "Insufficient frozen balance",
                code="insufficient_frozen_balance",
                status_code=400,
            )
        account.frozen_usd = self._money(account.frozen_usd - amount)
        account.available_usd = self._money(account.available_usd + amount)
        self._repository.add_transaction(
            self._session,
            user_id=user_id,
            generation_job_id=related_job_id,
            transaction_type=BalanceTransactionType.REFUND.value,
            amount_usd=amount,
            balance_available_after=account.available_usd,
            balance_frozen_after=account.frozen_usd,
            reason=reason,
        )
        return account

    async def _get_or_create_locked_account(self, user_id: UUID) -> BalanceAccount:
        await self._repository.create_account_if_missing(self._session, user_id)
        account = await self._repository.get_account(self._session, user_id, for_update=True)
        if account is None:
            raise AppError("Balance account was not created", code="balance_account_missing")
        return account

    def _normalize_positive_amount(self, amount: Decimal) -> Decimal:
        normalized = self._money(amount)
        if normalized <= Decimal("0"):
            raise AppError("Amount must be positive", code="invalid_amount", status_code=400)
        return normalized

    def _money(self, amount: Decimal) -> Decimal:
        return amount.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
