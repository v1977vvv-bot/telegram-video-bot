from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.balance_account import BalanceAccount
from backend.app.models.balance_transaction import BalanceTransaction
from backend.app.models.business_balance_transaction import BusinessBalanceTransaction
from backend.app.models.generation_job import GenerationJob
from backend.app.repositories.users import UserRepository
from backend.app.services.business_balance import BusinessBalanceService
from shared.app.enums import BalanceTransactionType, BusinessBalanceTransactionType, JobStatus
from shared.app.exceptions import AppError


@dataclass(frozen=True, slots=True)
class BalanceSnapshot:
    available_usd: Decimal
    frozen_usd: Decimal


@dataclass(frozen=True, slots=True)
class BusinessBalanceSnapshot:
    id: UUID
    name: str
    available_usd: Decimal
    frozen_usd: Decimal


@dataclass(frozen=True, slots=True)
class GenerationStatistics:
    today: int
    month: int
    all_time: int
    completed_all_time: int
    failed_all_time: int


@dataclass(frozen=True, slots=True)
class SpendingStatistics:
    today_usd: Decimal
    month_usd: Decimal
    all_time_usd: Decimal


@dataclass(frozen=True, slots=True)
class UserStatistics:
    telegram_id: int
    balance: BalanceSnapshot
    business_account: BusinessBalanceSnapshot | None
    generations: GenerationStatistics
    spending: SpendingStatistics


class UserStatisticsService:
    def __init__(
        self,
        session: AsyncSession,
        user_repository: UserRepository | None = None,
    ) -> None:
        self._session = session
        self._user_repository = user_repository or UserRepository()

    async def get_by_telegram_id(self, telegram_id: int) -> UserStatistics:
        user = await self._user_repository.get_by_telegram_id(self._session, telegram_id)
        if user is None:
            raise AppError("User not found", code="user_not_found", status_code=404)

        now = datetime.now(UTC)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = today_start.replace(day=1)

        balance = await self._get_balance(user.id)
        business_account = await self._get_business_balance(user.id)
        return UserStatistics(
            telegram_id=user.telegram_id,
            balance=balance,
            business_account=business_account,
            generations=GenerationStatistics(
                today=await self._count_generations(user.id, created_since=today_start),
                month=await self._count_generations(user.id, created_since=month_start),
                all_time=await self._count_generations(user.id),
                completed_all_time=await self._count_generations(
                    user.id,
                    status=JobStatus.COMPLETED.value,
                ),
                failed_all_time=await self._count_generations(
                    user.id,
                    status=JobStatus.FAILED.value,
                ),
            ),
            spending=SpendingStatistics(
                today_usd=await self._sum_captured_spending(user.id, created_since=today_start),
                month_usd=await self._sum_captured_spending(user.id, created_since=month_start),
                all_time_usd=await self._sum_captured_spending(user.id),
            ),
        )

    async def _get_balance(self, user_id: UUID) -> BalanceSnapshot:
        result = await self._session.execute(
            select(BalanceAccount).where(BalanceAccount.user_id == user_id)
        )
        account = result.scalar_one_or_none()
        if account is None:
            return BalanceSnapshot(available_usd=Decimal("0.0000"), frozen_usd=Decimal("0.0000"))
        return BalanceSnapshot(
            available_usd=account.available_usd,
            frozen_usd=account.frozen_usd,
        )

    async def _get_business_balance(self, user_id: UUID) -> BusinessBalanceSnapshot | None:
        selection = await BusinessBalanceService(
            self._session
        ).get_active_business_account_for_user(user_id)
        if selection is None:
            return None
        account = selection.account
        return BusinessBalanceSnapshot(
            id=account.id,
            name=account.name,
            available_usd=account.available_usd,
            frozen_usd=account.frozen_usd,
        )

    async def _count_generations(
        self,
        user_id: UUID,
        *,
        created_since: datetime | None = None,
        status: str | None = None,
    ) -> int:
        statement = select(func.count(GenerationJob.id)).where(GenerationJob.user_id == user_id)
        if created_since is not None:
            statement = statement.where(GenerationJob.created_at >= created_since)
        if status is not None:
            statement = statement.where(GenerationJob.status == status)
        return int((await self._session.execute(statement)).scalar_one())

    async def _sum_captured_spending(
        self,
        user_id: UUID,
        *,
        created_since: datetime | None = None,
    ) -> Decimal:
        personal_statement = select(
            func.coalesce(func.sum(func.abs(BalanceTransaction.amount_usd)), 0)
        ).where(
            BalanceTransaction.user_id == user_id,
            BalanceTransaction.type == BalanceTransactionType.CAPTURE.value,
        )
        if created_since is not None:
            personal_statement = personal_statement.where(
                BalanceTransaction.created_at >= created_since
            )
        personal_value = (await self._session.execute(personal_statement)).scalar_one()

        business_statement = select(
            func.coalesce(func.sum(func.abs(BusinessBalanceTransaction.amount_usd)), 0)
        ).where(
            BusinessBalanceTransaction.user_id == user_id,
            BusinessBalanceTransaction.type == BusinessBalanceTransactionType.CAPTURE.value,
        )
        if created_since is not None:
            business_statement = business_statement.where(
                BusinessBalanceTransaction.created_at >= created_since
            )
        business_value = (await self._session.execute(business_statement)).scalar_one()
        return (Decimal(personal_value) + Decimal(business_value)).quantize(Decimal("0.0001"))
