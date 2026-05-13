from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class BalanceResponse(BaseModel):
    available_usd: Decimal
    frozen_usd: Decimal


class TelegramUserUpsertRequest(BaseModel):
    telegram_id: int = Field(gt=0)
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None


class TelegramUserResponse(BaseModel):
    id: UUID
    telegram_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None
    is_banned: bool
    balance: BalanceResponse
