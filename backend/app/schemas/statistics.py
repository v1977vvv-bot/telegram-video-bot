from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from backend.app.schemas.users import BalanceResponse


class BusinessBalanceResponse(BaseModel):
    id: UUID
    name: str
    available_usd: Decimal
    frozen_usd: Decimal


class GenerationStatisticsResponse(BaseModel):
    today: int
    month: int
    all_time: int
    completed_all_time: int
    failed_all_time: int


class SpendingStatisticsResponse(BaseModel):
    today_usd: Decimal
    month_usd: Decimal
    all_time_usd: Decimal


class UserStatisticsResponse(BaseModel):
    telegram_id: int
    balance: BalanceResponse
    business_account: BusinessBalanceResponse | None = None
    generations: GenerationStatisticsResponse
    spending: SpendingStatisticsResponse
