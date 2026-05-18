from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class FinanceReportTotals(BaseModel):
    payment_topups_usd: Decimal = Decimal("0.0000")
    manual_personal_topups_usd: Decimal = Decimal("0.0000")
    manual_business_topups_usd: Decimal = Decimal("0.0000")
    captured_revenue_usd: Decimal = Decimal("0.0000")
    refunded_usd: Decimal = Decimal("0.0000")
    estimated_runpod_cost_usd: Decimal = Decimal("0.0000")
    gross_margin_usd: Decimal = Decimal("0.0000")
    gross_margin_percent: Decimal | None = None
    completed_jobs: int = 0
    failed_jobs: int = 0
    cancelled_jobs: int = 0


class FinanceSummaryResponse(BaseModel):
    date_from: date
    date_to: date
    totals: FinanceReportTotals


class FinanceDailyRow(BaseModel):
    date: date
    payment_topups_usd: Decimal = Decimal("0.0000")
    manual_personal_topups_usd: Decimal = Decimal("0.0000")
    manual_business_topups_usd: Decimal = Decimal("0.0000")
    captured_revenue_usd: Decimal = Decimal("0.0000")
    refunded_usd: Decimal = Decimal("0.0000")
    estimated_runpod_cost_usd: Decimal = Decimal("0.0000")
    gross_margin_usd: Decimal = Decimal("0.0000")
    gross_margin_percent: Decimal | None = None
    completed_jobs: int = 0
    failed_jobs: int = 0
    new_users: int = 0


class FinanceDailyResponse(BaseModel):
    date_from: date
    date_to: date
    items: list[FinanceDailyRow]


class UserSpendingReportRow(BaseModel):
    date: date
    user_id: UUID
    telegram_id: int
    username: str | None
    billing_account_type: str
    business_account_id: UUID | None = None
    business_account_name: str | None = None
    payment_topups_usd: Decimal = Decimal("0.0000")
    manual_topups_usd: Decimal = Decimal("0.0000")
    spent_usd: Decimal = Decimal("0.0000")
    refunded_usd: Decimal = Decimal("0.0000")
    estimated_runpod_cost_usd: Decimal = Decimal("0.0000")
    gross_margin_usd: Decimal = Decimal("0.0000")
    completed_generations: int = 0
    failed_generations: int = 0
    ending_personal_available_usd: Decimal = Decimal("0.0000")
    ending_personal_frozen_usd: Decimal = Decimal("0.0000")


class UserSpendingReportResponse(BaseModel):
    date_from: date
    date_to: date
    items: list[UserSpendingReportRow]
    limit: int
    offset: int


class BusinessSpendingReportRow(BaseModel):
    date: date
    business_account_id: UUID
    business_account_name: str
    user_id: UUID | None = None
    telegram_id: int | None = None
    username: str | None = None
    business_topups_usd: Decimal = Decimal("0.0000")
    spent_usd: Decimal = Decimal("0.0000")
    refunded_usd: Decimal = Decimal("0.0000")
    estimated_runpod_cost_usd: Decimal = Decimal("0.0000")
    gross_margin_usd: Decimal = Decimal("0.0000")
    completed_generations: int = 0
    failed_generations: int = 0
    ending_business_available_usd: Decimal = Decimal("0.0000")
    ending_business_frozen_usd: Decimal = Decimal("0.0000")


class BusinessSpendingReportResponse(BaseModel):
    date_from: date
    date_to: date
    items: list[BusinessSpendingReportRow]
    limit: int
    offset: int
