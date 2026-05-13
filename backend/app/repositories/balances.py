from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.balance_account import BalanceAccount
from backend.app.models.balance_transaction import BalanceTransaction


class BalanceRepository:
    async def get_account(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        for_update: bool = False,
    ) -> BalanceAccount | None:
        statement = select(BalanceAccount).where(BalanceAccount.user_id == user_id)
        if for_update:
            statement = statement.with_for_update()
        result = await session.execute(statement)
        return result.scalar_one_or_none()

    async def create_account_if_missing(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> None:
        statement = (
            insert(BalanceAccount)
            .values(user_id=user_id)
            .on_conflict_do_nothing(index_elements=[BalanceAccount.user_id])
        )
        await session.execute(statement)

    def add_transaction(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        transaction_type: str,
        amount_usd: Decimal,
        balance_available_after: Decimal,
        balance_frozen_after: Decimal,
        reason: str | None = None,
        payment_id: UUID | None = None,
        generation_job_id: UUID | None = None,
    ) -> BalanceTransaction:
        transaction = BalanceTransaction(
            user_id=user_id,
            payment_id=payment_id,
            generation_job_id=generation_job_id,
            type=transaction_type,
            amount_usd=amount_usd,
            balance_available_after=balance_available_after,
            balance_frozen_after=balance_frozen_after,
            reason=reason,
        )
        session.add(transaction)
        return transaction
