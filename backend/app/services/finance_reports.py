from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import Date, and_, case, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.balance_account import BalanceAccount
from backend.app.models.balance_transaction import BalanceTransaction
from backend.app.models.business_account import BusinessAccount
from backend.app.models.business_balance_transaction import BusinessBalanceTransaction
from backend.app.models.generation_job import GenerationJob
from backend.app.models.payment import Payment
from backend.app.models.user import User
from backend.app.schemas.admin_reports import (
    BusinessSpendingReportResponse,
    BusinessSpendingReportRow,
    FinanceDailyResponse,
    FinanceDailyRow,
    FinanceReportTotals,
    FinanceSummaryResponse,
    UserSpendingReportResponse,
    UserSpendingReportRow,
)
from shared.app.enums import (
    BalanceTransactionType,
    BillingAccountType,
    BusinessBalanceTransactionType,
    JobStatus,
    PaymentStatus,
)
from shared.app.exceptions import AppError

MONEY_ZERO = Decimal("0.0000")
MONEY_QUANT = Decimal("0.0001")
PERCENT_QUANT = Decimal("0.01")
DEFAULT_LIMIT = 100


@dataclass(frozen=True, slots=True)
class FinanceReportFilters:
    date_from: date
    date_to: date
    start_at: datetime
    end_at: datetime
    billing_account_type: str
    user_id: UUID | None = None
    telegram_id: int | None = None
    business_account_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _UserRowKey:
    day: date
    user_id: UUID
    billing_account_type: str
    business_account_id: UUID | None


@dataclass(frozen=True, slots=True)
class _BusinessRowKey:
    day: date
    business_account_id: UUID
    user_id: UUID | None


class FinanceReportService:
    """Read-only finance reporting over existing ledger and generation tables."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def build_filters(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        billing_account_type: str | None = None,
        user_id: UUID | None = None,
        telegram_id: int | None = None,
        business_account_id: UUID | None = None,
    ) -> FinanceReportFilters:
        today = datetime.now(UTC).date()
        start_date = date_from or today.replace(day=1)
        end_date = date_to or today
        if end_date < start_date:
            raise AppError(
                "date_to must be greater than or equal to date_from", code="bad_date_range"
            )

        billing_type = (billing_account_type or "all").strip().lower()
        if billing_type not in {
            "all",
            BillingAccountType.PERSONAL.value,
            BillingAccountType.BUSINESS.value,
        }:
            raise AppError("Unsupported billing_account_type", code="invalid_billing_account_type")

        return FinanceReportFilters(
            date_from=start_date,
            date_to=end_date,
            start_at=datetime.combine(start_date, time.min, tzinfo=UTC),
            end_at=datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=UTC),
            billing_account_type=billing_type,
            user_id=user_id,
            telegram_id=telegram_id,
            business_account_id=business_account_id,
        )

    async def finance_summary(self, filters: FinanceReportFilters) -> FinanceSummaryResponse:
        payment_topups = await self._payment_topups_total(filters)
        manual_personal_topups = await self._manual_personal_topups_total(filters)
        manual_business_topups = await self._manual_business_topups_total(filters)
        captured_revenue = await self._captured_revenue_total(filters)
        refunded = await self._refunds_total(filters)
        estimated_cost = await self._runpod_cost_total(filters)
        completed_jobs = await self._job_count(filters, JobStatus.COMPLETED.value)
        failed_jobs = await self._job_count(filters, JobStatus.FAILED.value)
        cancelled_jobs = await self._job_count(filters, JobStatus.CANCELLED.value)
        margin = _money(captured_revenue - estimated_cost)
        return FinanceSummaryResponse(
            date_from=filters.date_from,
            date_to=filters.date_to,
            totals=FinanceReportTotals(
                payment_topups_usd=payment_topups,
                manual_personal_topups_usd=manual_personal_topups,
                manual_business_topups_usd=manual_business_topups,
                captured_revenue_usd=captured_revenue,
                refunded_usd=refunded,
                estimated_runpod_cost_usd=estimated_cost,
                gross_margin_usd=margin,
                gross_margin_percent=_gross_margin_percent(margin, captured_revenue),
                completed_jobs=completed_jobs,
                failed_jobs=failed_jobs,
                cancelled_jobs=cancelled_jobs,
            ),
        )

    async def finance_daily(self, filters: FinanceReportFilters) -> FinanceDailyResponse:
        rows: dict[date, FinanceDailyRow] = {}
        await self._merge_daily_payment_topups(rows, filters)
        await self._merge_daily_manual_personal_topups(rows, filters)
        await self._merge_daily_manual_business_topups(rows, filters)
        await self._merge_daily_jobs(rows, filters)
        await self._merge_daily_refunds(rows, filters)
        await self._merge_daily_new_users(rows, filters)
        for row in rows.values():
            row.gross_margin_usd = _money(row.captured_revenue_usd - row.estimated_runpod_cost_usd)
            row.gross_margin_percent = _gross_margin_percent(
                row.gross_margin_usd,
                row.captured_revenue_usd,
            )
        return FinanceDailyResponse(
            date_from=filters.date_from,
            date_to=filters.date_to,
            items=sorted(rows.values(), key=lambda item: item.date, reverse=True),
        )

    async def user_spending(
        self,
        filters: FinanceReportFilters,
        *,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> UserSpendingReportResponse:
        rows: dict[_UserRowKey, UserSpendingReportRow] = {}
        await self._merge_user_job_rows(rows, filters)
        await self._merge_user_refunds(rows, filters)
        await self._merge_user_payment_topups(rows, filters)
        await self._merge_user_manual_topups(rows, filters)
        await self._fill_user_ending_balances(rows)
        for row in rows.values():
            row.gross_margin_usd = _money(row.spent_usd - row.estimated_runpod_cost_usd)
        sorted_rows = sorted(
            rows.values(),
            key=lambda item: (
                item.date,
                item.username or "",
                str(item.user_id),
                item.billing_account_type,
            ),
            reverse=True,
        )
        return UserSpendingReportResponse(
            date_from=filters.date_from,
            date_to=filters.date_to,
            items=sorted_rows[offset : offset + limit],
            limit=limit,
            offset=offset,
        )

    async def business_spending(
        self,
        filters: FinanceReportFilters,
        *,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> BusinessSpendingReportResponse:
        rows: dict[_BusinessRowKey, BusinessSpendingReportRow] = {}
        await self._merge_business_job_rows(rows, filters)
        await self._merge_business_refunds(rows, filters)
        await self._merge_business_topups(rows, filters)
        await self._fill_business_ending_balances(rows)
        for row in rows.values():
            row.gross_margin_usd = _money(row.spent_usd - row.estimated_runpod_cost_usd)
        sorted_rows = sorted(
            rows.values(),
            key=lambda item: (
                item.date,
                item.business_account_name,
                item.username or "",
                str(item.user_id or ""),
            ),
            reverse=True,
        )
        return BusinessSpendingReportResponse(
            date_from=filters.date_from,
            date_to=filters.date_to,
            items=sorted_rows[offset : offset + limit],
            limit=limit,
            offset=offset,
        )

    async def _payment_topups_total(self, filters: FinanceReportFilters) -> Decimal:
        if not _include_personal(filters):
            return MONEY_ZERO
        statement = (
            select(func.coalesce(func.sum(Payment.amount_usd), 0))
            .select_from(Payment)
            .join(User, User.id == Payment.user_id)
            .where(
                Payment.status.in_([PaymentStatus.PAID.value, PaymentStatus.PAID_OVER.value]),
                Payment.paid_at >= filters.start_at,
                Payment.paid_at < filters.end_at,
                *_user_filter_conditions(filters),
            )
        )
        return _money(await self._session.scalar(statement))

    async def _manual_personal_topups_total(self, filters: FinanceReportFilters) -> Decimal:
        if not _include_personal(filters):
            return MONEY_ZERO
        statement = (
            select(func.coalesce(func.sum(BalanceTransaction.amount_usd), 0))
            .select_from(BalanceTransaction)
            .join(User, User.id == BalanceTransaction.user_id)
            .where(
                BalanceTransaction.type == BalanceTransactionType.ADMIN_ADJUSTMENT.value,
                BalanceTransaction.created_at >= filters.start_at,
                BalanceTransaction.created_at < filters.end_at,
                *_user_filter_conditions(filters),
            )
        )
        return _money(await self._session.scalar(statement))

    async def _manual_business_topups_total(self, filters: FinanceReportFilters) -> Decimal:
        if not _include_business(filters) or filters.user_id or filters.telegram_id:
            return MONEY_ZERO
        statement = (
            select(func.coalesce(func.sum(BusinessBalanceTransaction.amount_usd), 0))
            .select_from(BusinessBalanceTransaction)
            .join(
                BusinessAccount,
                BusinessAccount.id == BusinessBalanceTransaction.business_account_id,
            )
            .where(
                BusinessBalanceTransaction.type.in_(
                    [
                        BusinessBalanceTransactionType.MANUAL_TOPUP.value,
                        BusinessBalanceTransactionType.ADJUSTMENT.value,
                    ]
                ),
                BusinessBalanceTransaction.created_at >= filters.start_at,
                BusinessBalanceTransaction.created_at < filters.end_at,
                *_business_filter_conditions(filters),
            )
        )
        return _money(await self._session.scalar(statement))

    async def _captured_revenue_total(self, filters: FinanceReportFilters) -> Decimal:
        statement = (
            select(func.coalesce(func.sum(GenerationJob.price_usd), 0))
            .select_from(GenerationJob)
            .join(User, User.id == GenerationJob.user_id)
            .where(
                GenerationJob.status == JobStatus.COMPLETED.value,
                _job_terminal_at() >= filters.start_at,
                _job_terminal_at() < filters.end_at,
                _capture_exists_condition(filters),
                *_job_filter_conditions(filters),
            )
        )
        return _money(await self._session.scalar(statement))

    async def _runpod_cost_total(self, filters: FinanceReportFilters) -> Decimal:
        statement = (
            select(func.coalesce(func.sum(GenerationJob.cost_usd), 0))
            .select_from(GenerationJob)
            .join(User, User.id == GenerationJob.user_id)
            .where(
                _job_terminal_at() >= filters.start_at,
                _job_terminal_at() < filters.end_at,
                *_job_filter_conditions(filters),
            )
        )
        return _money(await self._session.scalar(statement))

    async def _refunds_total(self, filters: FinanceReportFilters) -> Decimal:
        total = MONEY_ZERO
        if _include_personal(filters):
            total += await self._personal_refunds_total(filters)
        if _include_business(filters):
            total += await self._business_refunds_total(filters)
        return _money(total)

    async def _personal_refunds_total(self, filters: FinanceReportFilters) -> Decimal:
        statement = (
            select(func.coalesce(func.sum(BalanceTransaction.amount_usd), 0))
            .select_from(BalanceTransaction)
            .join(GenerationJob, GenerationJob.id == BalanceTransaction.generation_job_id)
            .join(User, User.id == BalanceTransaction.user_id)
            .where(
                BalanceTransaction.type.in_(
                    [BalanceTransactionType.REFUND.value, BalanceTransactionType.RELEASE.value]
                ),
                BalanceTransaction.created_at >= filters.start_at,
                BalanceTransaction.created_at < filters.end_at,
                *_job_filter_conditions(
                    filters, force_billing_type=BillingAccountType.PERSONAL.value
                ),
            )
        )
        return _money(await self._session.scalar(statement))

    async def _business_refunds_total(self, filters: FinanceReportFilters) -> Decimal:
        statement = (
            select(func.coalesce(func.sum(BusinessBalanceTransaction.amount_usd), 0))
            .select_from(BusinessBalanceTransaction)
            .join(GenerationJob, GenerationJob.id == BusinessBalanceTransaction.generation_job_id)
            .join(User, User.id == GenerationJob.user_id)
            .join(
                BusinessAccount,
                BusinessAccount.id == BusinessBalanceTransaction.business_account_id,
            )
            .where(
                BusinessBalanceTransaction.type.in_(
                    [
                        BusinessBalanceTransactionType.REFUND.value,
                        BusinessBalanceTransactionType.RELEASE.value,
                    ]
                ),
                BusinessBalanceTransaction.created_at >= filters.start_at,
                BusinessBalanceTransaction.created_at < filters.end_at,
                *_job_filter_conditions(
                    filters, force_billing_type=BillingAccountType.BUSINESS.value
                ),
            )
        )
        return _money(await self._session.scalar(statement))

    async def _job_count(self, filters: FinanceReportFilters, status: str) -> int:
        statement = (
            select(func.count(GenerationJob.id))
            .select_from(GenerationJob)
            .join(User, User.id == GenerationJob.user_id)
            .where(
                GenerationJob.status == status,
                _job_terminal_at() >= filters.start_at,
                _job_terminal_at() < filters.end_at,
                *_job_filter_conditions(filters),
            )
        )
        return int(await self._session.scalar(statement) or 0)

    async def _merge_daily_payment_topups(
        self,
        rows: dict[date, FinanceDailyRow],
        filters: FinanceReportFilters,
    ) -> None:
        if not _include_personal(filters):
            return
        day = cast(Payment.paid_at, Date)
        result = await self._session.execute(
            select(day, func.coalesce(func.sum(Payment.amount_usd), 0))
            .select_from(Payment)
            .join(User, User.id == Payment.user_id)
            .where(
                Payment.status.in_([PaymentStatus.PAID.value, PaymentStatus.PAID_OVER.value]),
                Payment.paid_at >= filters.start_at,
                Payment.paid_at < filters.end_at,
                *_user_filter_conditions(filters),
            )
            .group_by(day)
        )
        for day_value, amount in result.all():
            _daily_row(rows, day_value).payment_topups_usd = _money(amount)

    async def _merge_daily_manual_personal_topups(
        self,
        rows: dict[date, FinanceDailyRow],
        filters: FinanceReportFilters,
    ) -> None:
        if not _include_personal(filters):
            return
        day = cast(BalanceTransaction.created_at, Date)
        result = await self._session.execute(
            select(day, func.coalesce(func.sum(BalanceTransaction.amount_usd), 0))
            .select_from(BalanceTransaction)
            .join(User, User.id == BalanceTransaction.user_id)
            .where(
                BalanceTransaction.type == BalanceTransactionType.ADMIN_ADJUSTMENT.value,
                BalanceTransaction.created_at >= filters.start_at,
                BalanceTransaction.created_at < filters.end_at,
                *_user_filter_conditions(filters),
            )
            .group_by(day)
        )
        for day_value, amount in result.all():
            _daily_row(rows, day_value).manual_personal_topups_usd = _money(amount)

    async def _merge_daily_manual_business_topups(
        self,
        rows: dict[date, FinanceDailyRow],
        filters: FinanceReportFilters,
    ) -> None:
        if not _include_business(filters) or filters.user_id or filters.telegram_id:
            return
        day = cast(BusinessBalanceTransaction.created_at, Date)
        result = await self._session.execute(
            select(day, func.coalesce(func.sum(BusinessBalanceTransaction.amount_usd), 0))
            .select_from(BusinessBalanceTransaction)
            .join(
                BusinessAccount,
                BusinessAccount.id == BusinessBalanceTransaction.business_account_id,
            )
            .where(
                BusinessBalanceTransaction.type.in_(
                    [
                        BusinessBalanceTransactionType.MANUAL_TOPUP.value,
                        BusinessBalanceTransactionType.ADJUSTMENT.value,
                    ]
                ),
                BusinessBalanceTransaction.created_at >= filters.start_at,
                BusinessBalanceTransaction.created_at < filters.end_at,
                *_business_filter_conditions(filters),
            )
            .group_by(day)
        )
        for day_value, amount in result.all():
            _daily_row(rows, day_value).manual_business_topups_usd = _money(amount)

    async def _merge_daily_jobs(
        self,
        rows: dict[date, FinanceDailyRow],
        filters: FinanceReportFilters,
    ) -> None:
        terminal_at = _job_terminal_at()
        day = cast(terminal_at, Date)
        captured_condition = and_(
            GenerationJob.status == JobStatus.COMPLETED.value,
            _capture_exists_condition(filters),
        )
        result = await self._session.execute(
            select(
                day,
                func.coalesce(
                    func.sum(case((captured_condition, GenerationJob.price_usd), else_=0)),
                    0,
                ),
                func.coalesce(func.sum(GenerationJob.cost_usd), 0),
                func.coalesce(
                    func.sum(case((GenerationJob.status == JobStatus.COMPLETED.value, 1), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(case((GenerationJob.status == JobStatus.FAILED.value, 1), else_=0)),
                    0,
                ),
            )
            .select_from(GenerationJob)
            .join(User, User.id == GenerationJob.user_id)
            .where(
                terminal_at >= filters.start_at,
                terminal_at < filters.end_at,
                *_job_filter_conditions(filters),
            )
            .group_by(day)
        )
        for day_value, revenue, cost, completed, failed in result.all():
            row = _daily_row(rows, day_value)
            row.captured_revenue_usd = _money(revenue)
            row.estimated_runpod_cost_usd = _money(cost)
            row.completed_jobs = int(completed or 0)
            row.failed_jobs = int(failed or 0)

    async def _merge_daily_refunds(
        self,
        rows: dict[date, FinanceDailyRow],
        filters: FinanceReportFilters,
    ) -> None:
        if _include_personal(filters):
            await self._merge_daily_personal_refunds(rows, filters)
        if _include_business(filters):
            await self._merge_daily_business_refunds(rows, filters)

    async def _merge_daily_personal_refunds(
        self,
        rows: dict[date, FinanceDailyRow],
        filters: FinanceReportFilters,
    ) -> None:
        day = cast(BalanceTransaction.created_at, Date)
        result = await self._session.execute(
            select(day, func.coalesce(func.sum(BalanceTransaction.amount_usd), 0))
            .select_from(BalanceTransaction)
            .join(GenerationJob, GenerationJob.id == BalanceTransaction.generation_job_id)
            .join(User, User.id == BalanceTransaction.user_id)
            .where(
                BalanceTransaction.type.in_(
                    [BalanceTransactionType.REFUND.value, BalanceTransactionType.RELEASE.value]
                ),
                BalanceTransaction.created_at >= filters.start_at,
                BalanceTransaction.created_at < filters.end_at,
                *_job_filter_conditions(
                    filters, force_billing_type=BillingAccountType.PERSONAL.value
                ),
            )
            .group_by(day)
        )
        for day_value, amount in result.all():
            row = _daily_row(rows, day_value)
            row.refunded_usd = _money(row.refunded_usd + _money(amount))

    async def _merge_daily_business_refunds(
        self,
        rows: dict[date, FinanceDailyRow],
        filters: FinanceReportFilters,
    ) -> None:
        day = cast(BusinessBalanceTransaction.created_at, Date)
        result = await self._session.execute(
            select(day, func.coalesce(func.sum(BusinessBalanceTransaction.amount_usd), 0))
            .select_from(BusinessBalanceTransaction)
            .join(GenerationJob, GenerationJob.id == BusinessBalanceTransaction.generation_job_id)
            .join(User, User.id == GenerationJob.user_id)
            .join(
                BusinessAccount,
                BusinessAccount.id == BusinessBalanceTransaction.business_account_id,
            )
            .where(
                BusinessBalanceTransaction.type.in_(
                    [
                        BusinessBalanceTransactionType.REFUND.value,
                        BusinessBalanceTransactionType.RELEASE.value,
                    ]
                ),
                BusinessBalanceTransaction.created_at >= filters.start_at,
                BusinessBalanceTransaction.created_at < filters.end_at,
                *_job_filter_conditions(
                    filters, force_billing_type=BillingAccountType.BUSINESS.value
                ),
            )
            .group_by(day)
        )
        for day_value, amount in result.all():
            row = _daily_row(rows, day_value)
            row.refunded_usd = _money(row.refunded_usd + _money(amount))

    async def _merge_daily_new_users(
        self,
        rows: dict[date, FinanceDailyRow],
        filters: FinanceReportFilters,
    ) -> None:
        if filters.business_account_id:
            return
        day = cast(User.created_at, Date)
        result = await self._session.execute(
            select(day, func.count(User.id))
            .where(
                User.created_at >= filters.start_at,
                User.created_at < filters.end_at,
                *_user_filter_conditions(filters),
            )
            .group_by(day)
        )
        for day_value, count in result.all():
            _daily_row(rows, day_value).new_users = int(count or 0)

    async def _merge_user_job_rows(
        self,
        rows: dict[_UserRowKey, UserSpendingReportRow],
        filters: FinanceReportFilters,
    ) -> None:
        terminal_at = _job_terminal_at()
        day = cast(terminal_at, Date)
        captured_condition = and_(
            GenerationJob.status == JobStatus.COMPLETED.value,
            _capture_exists_condition(filters),
        )
        result = await self._session.execute(
            select(
                day,
                User.id,
                User.telegram_id,
                User.username,
                GenerationJob.billing_account_type,
                GenerationJob.business_account_id,
                BusinessAccount.name,
                func.coalesce(
                    func.sum(case((captured_condition, GenerationJob.price_usd), else_=0)),
                    0,
                ),
                func.coalesce(func.sum(GenerationJob.cost_usd), 0),
                func.coalesce(
                    func.sum(case((GenerationJob.status == JobStatus.COMPLETED.value, 1), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(case((GenerationJob.status == JobStatus.FAILED.value, 1), else_=0)),
                    0,
                ),
            )
            .select_from(GenerationJob)
            .join(User, User.id == GenerationJob.user_id)
            .outerjoin(BusinessAccount, BusinessAccount.id == GenerationJob.business_account_id)
            .where(
                terminal_at >= filters.start_at,
                terminal_at < filters.end_at,
                *_job_filter_conditions(filters),
            )
            .group_by(
                day,
                User.id,
                User.telegram_id,
                User.username,
                GenerationJob.billing_account_type,
                GenerationJob.business_account_id,
                BusinessAccount.name,
            )
        )
        for (
            day_value,
            user_id,
            telegram_id,
            username,
            billing_type,
            business_account_id,
            business_name,
            spent,
            cost,
            completed,
            failed,
        ) in result.all():
            row = _user_row(
                rows,
                day_value,
                user_id,
                telegram_id,
                username,
                billing_type,
                business_account_id,
                business_name,
            )
            row.spent_usd = _money(row.spent_usd + _money(spent))
            row.estimated_runpod_cost_usd = _money(row.estimated_runpod_cost_usd + _money(cost))
            row.completed_generations += int(completed or 0)
            row.failed_generations += int(failed or 0)

    async def _merge_user_refunds(
        self,
        rows: dict[_UserRowKey, UserSpendingReportRow],
        filters: FinanceReportFilters,
    ) -> None:
        if _include_personal(filters):
            await self._merge_user_personal_refunds(rows, filters)
        if _include_business(filters):
            await self._merge_user_business_refunds(rows, filters)

    async def _merge_user_personal_refunds(
        self,
        rows: dict[_UserRowKey, UserSpendingReportRow],
        filters: FinanceReportFilters,
    ) -> None:
        day = cast(BalanceTransaction.created_at, Date)
        result = await self._session.execute(
            select(
                day,
                User.id,
                User.telegram_id,
                User.username,
                func.coalesce(func.sum(BalanceTransaction.amount_usd), 0),
            )
            .select_from(BalanceTransaction)
            .join(GenerationJob, GenerationJob.id == BalanceTransaction.generation_job_id)
            .join(User, User.id == BalanceTransaction.user_id)
            .where(
                BalanceTransaction.type.in_(
                    [BalanceTransactionType.REFUND.value, BalanceTransactionType.RELEASE.value]
                ),
                BalanceTransaction.created_at >= filters.start_at,
                BalanceTransaction.created_at < filters.end_at,
                *_job_filter_conditions(
                    filters, force_billing_type=BillingAccountType.PERSONAL.value
                ),
            )
            .group_by(day, User.id, User.telegram_id, User.username)
        )
        for day_value, user_id, telegram_id, username, amount in result.all():
            row = _user_row(
                rows,
                day_value,
                user_id,
                telegram_id,
                username,
                BillingAccountType.PERSONAL.value,
                None,
                None,
            )
            row.refunded_usd = _money(row.refunded_usd + _money(amount))

    async def _merge_user_business_refunds(
        self,
        rows: dict[_UserRowKey, UserSpendingReportRow],
        filters: FinanceReportFilters,
    ) -> None:
        day = cast(BusinessBalanceTransaction.created_at, Date)
        result = await self._session.execute(
            select(
                day,
                User.id,
                User.telegram_id,
                User.username,
                BusinessAccount.id,
                BusinessAccount.name,
                func.coalesce(func.sum(BusinessBalanceTransaction.amount_usd), 0),
            )
            .select_from(BusinessBalanceTransaction)
            .join(GenerationJob, GenerationJob.id == BusinessBalanceTransaction.generation_job_id)
            .join(User, User.id == GenerationJob.user_id)
            .join(
                BusinessAccount,
                BusinessAccount.id == BusinessBalanceTransaction.business_account_id,
            )
            .where(
                BusinessBalanceTransaction.type.in_(
                    [
                        BusinessBalanceTransactionType.REFUND.value,
                        BusinessBalanceTransactionType.RELEASE.value,
                    ]
                ),
                BusinessBalanceTransaction.created_at >= filters.start_at,
                BusinessBalanceTransaction.created_at < filters.end_at,
                *_job_filter_conditions(
                    filters, force_billing_type=BillingAccountType.BUSINESS.value
                ),
            )
            .group_by(
                day,
                User.id,
                User.telegram_id,
                User.username,
                BusinessAccount.id,
                BusinessAccount.name,
            )
        )
        for (
            day_value,
            user_id,
            telegram_id,
            username,
            business_account_id,
            business_name,
            amount,
        ) in result.all():
            row = _user_row(
                rows,
                day_value,
                user_id,
                telegram_id,
                username,
                BillingAccountType.BUSINESS.value,
                business_account_id,
                business_name,
            )
            row.refunded_usd = _money(row.refunded_usd + _money(amount))

    async def _merge_user_payment_topups(
        self,
        rows: dict[_UserRowKey, UserSpendingReportRow],
        filters: FinanceReportFilters,
    ) -> None:
        if not _include_personal(filters):
            return
        day = cast(Payment.paid_at, Date)
        result = await self._session.execute(
            select(
                day,
                User.id,
                User.telegram_id,
                User.username,
                func.coalesce(func.sum(Payment.amount_usd), 0),
            )
            .select_from(Payment)
            .join(User, User.id == Payment.user_id)
            .where(
                Payment.status.in_([PaymentStatus.PAID.value, PaymentStatus.PAID_OVER.value]),
                Payment.paid_at >= filters.start_at,
                Payment.paid_at < filters.end_at,
                *_user_filter_conditions(filters),
            )
            .group_by(day, User.id, User.telegram_id, User.username)
        )
        for day_value, user_id, telegram_id, username, amount in result.all():
            row = _user_row(
                rows,
                day_value,
                user_id,
                telegram_id,
                username,
                BillingAccountType.PERSONAL.value,
                None,
                None,
            )
            row.payment_topups_usd = _money(row.payment_topups_usd + _money(amount))

    async def _merge_user_manual_topups(
        self,
        rows: dict[_UserRowKey, UserSpendingReportRow],
        filters: FinanceReportFilters,
    ) -> None:
        if not _include_personal(filters):
            return
        day = cast(BalanceTransaction.created_at, Date)
        result = await self._session.execute(
            select(
                day,
                User.id,
                User.telegram_id,
                User.username,
                func.coalesce(func.sum(BalanceTransaction.amount_usd), 0),
            )
            .select_from(BalanceTransaction)
            .join(User, User.id == BalanceTransaction.user_id)
            .where(
                BalanceTransaction.type == BalanceTransactionType.ADMIN_ADJUSTMENT.value,
                BalanceTransaction.created_at >= filters.start_at,
                BalanceTransaction.created_at < filters.end_at,
                *_user_filter_conditions(filters),
            )
            .group_by(day, User.id, User.telegram_id, User.username)
        )
        for day_value, user_id, telegram_id, username, amount in result.all():
            row = _user_row(
                rows,
                day_value,
                user_id,
                telegram_id,
                username,
                BillingAccountType.PERSONAL.value,
                None,
                None,
            )
            row.manual_topups_usd = _money(row.manual_topups_usd + _money(amount))

    async def _fill_user_ending_balances(
        self,
        rows: dict[_UserRowKey, UserSpendingReportRow],
    ) -> None:
        user_ids = list({key.user_id for key in rows})
        if not user_ids:
            return
        result = await self._session.execute(
            select(BalanceAccount).where(BalanceAccount.user_id.in_(user_ids))
        )
        balances = {balance.user_id: balance for balance in result.scalars()}
        for row in rows.values():
            balance = balances.get(row.user_id)
            if balance is not None:
                row.ending_personal_available_usd = _money(balance.available_usd)
                row.ending_personal_frozen_usd = _money(balance.frozen_usd)

    async def _merge_business_job_rows(
        self,
        rows: dict[_BusinessRowKey, BusinessSpendingReportRow],
        filters: FinanceReportFilters,
    ) -> None:
        if not _include_business(filters):
            return
        terminal_at = _job_terminal_at()
        day = cast(terminal_at, Date)
        captured_condition = and_(
            GenerationJob.status == JobStatus.COMPLETED.value,
            _business_capture_exists(),
        )
        result = await self._session.execute(
            select(
                day,
                BusinessAccount.id,
                BusinessAccount.name,
                User.id,
                User.telegram_id,
                User.username,
                func.coalesce(
                    func.sum(case((captured_condition, GenerationJob.price_usd), else_=0)),
                    0,
                ),
                func.coalesce(func.sum(GenerationJob.cost_usd), 0),
                func.coalesce(
                    func.sum(case((GenerationJob.status == JobStatus.COMPLETED.value, 1), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(case((GenerationJob.status == JobStatus.FAILED.value, 1), else_=0)),
                    0,
                ),
            )
            .select_from(GenerationJob)
            .join(User, User.id == GenerationJob.user_id)
            .join(BusinessAccount, BusinessAccount.id == GenerationJob.business_account_id)
            .where(
                terminal_at >= filters.start_at,
                terminal_at < filters.end_at,
                *_job_filter_conditions(
                    filters, force_billing_type=BillingAccountType.BUSINESS.value
                ),
            )
            .group_by(
                day,
                BusinessAccount.id,
                BusinessAccount.name,
                User.id,
                User.telegram_id,
                User.username,
            )
        )
        for (
            day_value,
            business_account_id,
            business_name,
            user_id,
            telegram_id,
            username,
            spent,
            cost,
            completed,
            failed,
        ) in result.all():
            row = _business_row(
                rows,
                day_value,
                business_account_id,
                business_name,
                user_id,
                telegram_id,
                username,
            )
            row.spent_usd = _money(row.spent_usd + _money(spent))
            row.estimated_runpod_cost_usd = _money(row.estimated_runpod_cost_usd + _money(cost))
            row.completed_generations += int(completed or 0)
            row.failed_generations += int(failed or 0)

    async def _merge_business_refunds(
        self,
        rows: dict[_BusinessRowKey, BusinessSpendingReportRow],
        filters: FinanceReportFilters,
    ) -> None:
        if not _include_business(filters):
            return
        day = cast(BusinessBalanceTransaction.created_at, Date)
        result = await self._session.execute(
            select(
                day,
                BusinessAccount.id,
                BusinessAccount.name,
                User.id,
                User.telegram_id,
                User.username,
                func.coalesce(func.sum(BusinessBalanceTransaction.amount_usd), 0),
            )
            .select_from(BusinessBalanceTransaction)
            .join(GenerationJob, GenerationJob.id == BusinessBalanceTransaction.generation_job_id)
            .join(User, User.id == GenerationJob.user_id)
            .join(
                BusinessAccount,
                BusinessAccount.id == BusinessBalanceTransaction.business_account_id,
            )
            .where(
                BusinessBalanceTransaction.type.in_(
                    [
                        BusinessBalanceTransactionType.REFUND.value,
                        BusinessBalanceTransactionType.RELEASE.value,
                    ]
                ),
                BusinessBalanceTransaction.created_at >= filters.start_at,
                BusinessBalanceTransaction.created_at < filters.end_at,
                *_job_filter_conditions(
                    filters, force_billing_type=BillingAccountType.BUSINESS.value
                ),
            )
            .group_by(
                day,
                BusinessAccount.id,
                BusinessAccount.name,
                User.id,
                User.telegram_id,
                User.username,
            )
        )
        for (
            day_value,
            business_account_id,
            business_name,
            user_id,
            telegram_id,
            username,
            amount,
        ) in result.all():
            row = _business_row(
                rows,
                day_value,
                business_account_id,
                business_name,
                user_id,
                telegram_id,
                username,
            )
            row.refunded_usd = _money(row.refunded_usd + _money(amount))

    async def _merge_business_topups(
        self,
        rows: dict[_BusinessRowKey, BusinessSpendingReportRow],
        filters: FinanceReportFilters,
    ) -> None:
        if not _include_business(filters) or filters.user_id or filters.telegram_id:
            return
        day = cast(BusinessBalanceTransaction.created_at, Date)
        result = await self._session.execute(
            select(
                day,
                BusinessAccount.id,
                BusinessAccount.name,
                func.coalesce(func.sum(BusinessBalanceTransaction.amount_usd), 0),
            )
            .select_from(BusinessBalanceTransaction)
            .join(
                BusinessAccount,
                BusinessAccount.id == BusinessBalanceTransaction.business_account_id,
            )
            .where(
                BusinessBalanceTransaction.type.in_(
                    [
                        BusinessBalanceTransactionType.MANUAL_TOPUP.value,
                        BusinessBalanceTransactionType.ADJUSTMENT.value,
                    ]
                ),
                BusinessBalanceTransaction.created_at >= filters.start_at,
                BusinessBalanceTransaction.created_at < filters.end_at,
                *_business_filter_conditions(filters),
            )
            .group_by(day, BusinessAccount.id, BusinessAccount.name)
        )
        for day_value, business_account_id, business_name, amount in result.all():
            row = _business_row(
                rows, day_value, business_account_id, business_name, None, None, None
            )
            row.business_topups_usd = _money(row.business_topups_usd + _money(amount))

    async def _fill_business_ending_balances(
        self,
        rows: dict[_BusinessRowKey, BusinessSpendingReportRow],
    ) -> None:
        account_ids = list({key.business_account_id for key in rows})
        if not account_ids:
            return
        result = await self._session.execute(
            select(BusinessAccount).where(BusinessAccount.id.in_(account_ids))
        )
        accounts = {account.id: account for account in result.scalars()}
        for row in rows.values():
            account = accounts.get(row.business_account_id)
            if account is not None:
                row.ending_business_available_usd = _money(account.available_usd)
                row.ending_business_frozen_usd = _money(account.frozen_usd)


def _job_terminal_at():
    return func.coalesce(
        GenerationJob.completed_at, GenerationJob.updated_at, GenerationJob.created_at
    )


def _personal_capture_exists():
    return exists(
        select(BalanceTransaction.id).where(
            BalanceTransaction.generation_job_id == GenerationJob.id,
            BalanceTransaction.type == BalanceTransactionType.CAPTURE.value,
        )
    )


def _business_capture_exists():
    return exists(
        select(BusinessBalanceTransaction.id).where(
            BusinessBalanceTransaction.generation_job_id == GenerationJob.id,
            BusinessBalanceTransaction.type == BusinessBalanceTransactionType.CAPTURE.value,
        )
    )


def _capture_exists_condition(filters: FinanceReportFilters):
    if filters.billing_account_type == BillingAccountType.PERSONAL.value:
        return _personal_capture_exists()
    if filters.billing_account_type == BillingAccountType.BUSINESS.value:
        return _business_capture_exists()
    return or_(_personal_capture_exists(), _business_capture_exists())


def _job_filter_conditions(
    filters: FinanceReportFilters,
    *,
    force_billing_type: str | None = None,
) -> list[object]:
    conditions: list[object] = []
    billing_type = force_billing_type or filters.billing_account_type
    if billing_type != "all":
        conditions.append(GenerationJob.billing_account_type == billing_type)
    if filters.user_id is not None:
        conditions.append(GenerationJob.user_id == filters.user_id)
    if filters.telegram_id is not None:
        conditions.append(User.telegram_id == filters.telegram_id)
    if filters.business_account_id is not None:
        conditions.append(GenerationJob.business_account_id == filters.business_account_id)
    return conditions


def _user_filter_conditions(filters: FinanceReportFilters) -> list[object]:
    conditions: list[object] = []
    if filters.user_id is not None:
        conditions.append(User.id == filters.user_id)
    if filters.telegram_id is not None:
        conditions.append(User.telegram_id == filters.telegram_id)
    return conditions


def _business_filter_conditions(filters: FinanceReportFilters) -> list[object]:
    conditions: list[object] = []
    if filters.business_account_id is not None:
        conditions.append(BusinessAccount.id == filters.business_account_id)
    return conditions


def _include_personal(filters: FinanceReportFilters) -> bool:
    return (
        filters.billing_account_type in {"all", BillingAccountType.PERSONAL.value}
        and filters.business_account_id is None
    )


def _include_business(filters: FinanceReportFilters) -> bool:
    return filters.billing_account_type in {"all", BillingAccountType.BUSINESS.value}


def _daily_row(rows: dict[date, FinanceDailyRow], day: date) -> FinanceDailyRow:
    if day not in rows:
        rows[day] = FinanceDailyRow(date=day)
    return rows[day]


def _user_row(
    rows: dict[_UserRowKey, UserSpendingReportRow],
    day: date,
    user_id: UUID,
    telegram_id: int,
    username: str | None,
    billing_account_type: str,
    business_account_id: UUID | None,
    business_account_name: str | None,
) -> UserSpendingReportRow:
    key = _UserRowKey(
        day=day,
        user_id=user_id,
        billing_account_type=billing_account_type,
        business_account_id=business_account_id,
    )
    if key not in rows:
        rows[key] = UserSpendingReportRow(
            date=day,
            user_id=user_id,
            telegram_id=telegram_id,
            username=username,
            billing_account_type=billing_account_type,
            business_account_id=business_account_id,
            business_account_name=business_account_name,
        )
    return rows[key]


def _business_row(
    rows: dict[_BusinessRowKey, BusinessSpendingReportRow],
    day: date,
    business_account_id: UUID,
    business_account_name: str,
    user_id: UUID | None,
    telegram_id: int | None,
    username: str | None,
) -> BusinessSpendingReportRow:
    key = _BusinessRowKey(day=day, business_account_id=business_account_id, user_id=user_id)
    if key not in rows:
        rows[key] = BusinessSpendingReportRow(
            date=day,
            business_account_id=business_account_id,
            business_account_name=business_account_name,
            user_id=user_id,
            telegram_id=telegram_id,
            username=username,
        )
    return rows[key]


def _money(value: object) -> Decimal:
    if value is None:
        return MONEY_ZERO
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _gross_margin_percent(margin: Decimal, revenue: Decimal) -> Decimal | None:
    if revenue <= MONEY_ZERO:
        return None
    return ((margin / revenue) * Decimal("100")).quantize(PERCENT_QUANT, rounding=ROUND_HALF_UP)
