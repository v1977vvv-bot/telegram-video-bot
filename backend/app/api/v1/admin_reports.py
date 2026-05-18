from __future__ import annotations

import csv
from datetime import date
from io import StringIO
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.admin_auth import AdminPrincipal, require_admin_auth
from backend.app.schemas.admin_reports import (
    BusinessSpendingReportResponse,
    FinanceDailyResponse,
    FinanceSummaryResponse,
    UserSpendingReportResponse,
)
from backend.app.services.finance_reports import FinanceReportFilters, FinanceReportService
from shared.app.database import get_session

router = APIRouter(prefix="/admin/reports", tags=["admin-reports"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
AdminDep = Annotated[AdminPrincipal, Depends(require_admin_auth)]


@router.get("/finance/summary", response_model=FinanceSummaryResponse)
async def get_finance_summary(
    session: SessionDep,
    _: AdminDep,
    date_from: date | None = None,
    date_to: date | None = None,
    billing_account_type: str | None = "all",
    user_id: UUID | None = None,
    business_account_id: UUID | None = None,
) -> FinanceSummaryResponse:
    service = FinanceReportService(session)
    filters = _filters(
        service,
        date_from=date_from,
        date_to=date_to,
        billing_account_type=billing_account_type,
        user_id=user_id,
        business_account_id=business_account_id,
    )
    return await service.finance_summary(filters)


@router.get("/finance/daily", response_model=FinanceDailyResponse)
async def get_finance_daily(
    session: SessionDep,
    _: AdminDep,
    date_from: date | None = None,
    date_to: date | None = None,
    billing_account_type: str | None = "all",
    user_id: UUID | None = None,
    business_account_id: UUID | None = None,
) -> FinanceDailyResponse:
    service = FinanceReportService(session)
    filters = _filters(
        service,
        date_from=date_from,
        date_to=date_to,
        billing_account_type=billing_account_type,
        user_id=user_id,
        business_account_id=business_account_id,
    )
    return await service.finance_daily(filters)


@router.get("/users/spending", response_model=UserSpendingReportResponse)
async def get_user_spending_report(
    session: SessionDep,
    _: AdminDep,
    date_from: date | None = None,
    date_to: date | None = None,
    user_id: UUID | None = None,
    telegram_id: int | None = None,
    billing_account_type: str | None = "all",
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserSpendingReportResponse:
    service = FinanceReportService(session)
    filters = _filters(
        service,
        date_from=date_from,
        date_to=date_to,
        billing_account_type=billing_account_type,
        user_id=user_id,
        telegram_id=telegram_id,
    )
    return await service.user_spending(filters, limit=limit, offset=offset)


@router.get("/business/spending", response_model=BusinessSpendingReportResponse)
async def get_business_spending_report(
    session: SessionDep,
    _: AdminDep,
    date_from: date | None = None,
    date_to: date | None = None,
    business_account_id: UUID | None = None,
    user_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BusinessSpendingReportResponse:
    service = FinanceReportService(session)
    filters = _filters(
        service,
        date_from=date_from,
        date_to=date_to,
        billing_account_type="business",
        user_id=user_id,
        business_account_id=business_account_id,
    )
    return await service.business_spending(filters, limit=limit, offset=offset)


@router.get("/finance/daily.csv")
async def export_finance_daily_csv(
    session: SessionDep,
    _: AdminDep,
    date_from: date | None = None,
    date_to: date | None = None,
    billing_account_type: str | None = "all",
    user_id: UUID | None = None,
    business_account_id: UUID | None = None,
) -> Response:
    service = FinanceReportService(session)
    filters = _filters(
        service,
        date_from=date_from,
        date_to=date_to,
        billing_account_type=billing_account_type,
        user_id=user_id,
        business_account_id=business_account_id,
    )
    report = await service.finance_daily(filters)
    return _csv_response(
        filename=f"finance_daily_{filters.date_from}_{filters.date_to}.csv",
        rows=[row.model_dump(mode="json") for row in report.items],
        fieldnames=[
            "date",
            "payment_topups_usd",
            "manual_personal_topups_usd",
            "manual_business_topups_usd",
            "captured_revenue_usd",
            "refunded_usd",
            "estimated_runpod_cost_usd",
            "gross_margin_usd",
            "gross_margin_percent",
            "completed_jobs",
            "failed_jobs",
            "new_users",
        ],
    )


@router.get("/users/spending.csv")
async def export_user_spending_csv(
    session: SessionDep,
    _: AdminDep,
    date_from: date | None = None,
    date_to: date | None = None,
    user_id: UUID | None = None,
    telegram_id: int | None = None,
    billing_account_type: str | None = "all",
    limit: Annotated[int, Query(ge=1, le=10000)] = 10000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    service = FinanceReportService(session)
    filters = _filters(
        service,
        date_from=date_from,
        date_to=date_to,
        billing_account_type=billing_account_type,
        user_id=user_id,
        telegram_id=telegram_id,
    )
    report = await service.user_spending(filters, limit=limit, offset=offset)
    return _csv_response(
        filename=f"user_spending_{filters.date_from}_{filters.date_to}.csv",
        rows=[row.model_dump(mode="json") for row in report.items],
        fieldnames=[
            "date",
            "user_id",
            "telegram_id",
            "username",
            "billing_account_type",
            "business_account_id",
            "business_account_name",
            "payment_topups_usd",
            "manual_topups_usd",
            "spent_usd",
            "refunded_usd",
            "estimated_runpod_cost_usd",
            "gross_margin_usd",
            "completed_generations",
            "failed_generations",
            "ending_personal_available_usd",
            "ending_personal_frozen_usd",
        ],
    )


@router.get("/business/spending.csv")
async def export_business_spending_csv(
    session: SessionDep,
    _: AdminDep,
    date_from: date | None = None,
    date_to: date | None = None,
    business_account_id: UUID | None = None,
    user_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=10000)] = 10000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    service = FinanceReportService(session)
    filters = _filters(
        service,
        date_from=date_from,
        date_to=date_to,
        billing_account_type="business",
        user_id=user_id,
        business_account_id=business_account_id,
    )
    report = await service.business_spending(filters, limit=limit, offset=offset)
    return _csv_response(
        filename=f"business_spending_{filters.date_from}_{filters.date_to}.csv",
        rows=[row.model_dump(mode="json") for row in report.items],
        fieldnames=[
            "date",
            "business_account_id",
            "business_account_name",
            "user_id",
            "telegram_id",
            "username",
            "business_topups_usd",
            "spent_usd",
            "refunded_usd",
            "estimated_runpod_cost_usd",
            "gross_margin_usd",
            "completed_generations",
            "failed_generations",
            "ending_business_available_usd",
            "ending_business_frozen_usd",
        ],
    )


def _filters(
    service: FinanceReportService,
    *,
    date_from: date | None,
    date_to: date | None,
    billing_account_type: str | None,
    user_id: UUID | None = None,
    telegram_id: int | None = None,
    business_account_id: UUID | None = None,
) -> FinanceReportFilters:
    return service.build_filters(
        date_from=date_from,
        date_to=date_to,
        billing_account_type=billing_account_type,
        user_id=user_id,
        telegram_id=telegram_id,
        business_account_id=business_account_id,
    )


def _csv_response(
    *,
    filename: str,
    rows: list[dict[str, object]],
    fieldnames: list[str],
) -> Response:
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: _csv_value(row.get(field)) for field in fieldnames})
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _csv_value(value: object) -> str:
    if value is None:
        return ""
    return str(value)
