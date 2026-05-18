from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class BusinessAccountCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class BusinessAccountResponse(BaseModel):
    id: UUID
    name: str
    status: str
    available_usd: Decimal
    frozen_usd: Decimal
    active_members_count: int
    created_at: datetime
    updated_at: datetime


class BusinessAccountsResponse(BaseModel):
    items: list[BusinessAccountResponse]


class BusinessAccountTopUpRequest(BaseModel):
    amount_usd: Decimal = Field(gt=Decimal("0"))
    reason: str = Field(default="Direct business payment", min_length=1, max_length=500)
    admin_note: str | None = Field(default=None, max_length=1000)


class BusinessAccountTopUpResponse(BaseModel):
    business_account: BusinessAccountResponse
    transaction_id: UUID
    amount_usd: Decimal
    notification_sent: int


class BusinessAccountMemberAddRequest(BaseModel):
    telegram_id: int | None = Field(default=None, gt=0)
    user_id: UUID | None = None
    role: str = "member"


class BusinessAccountMemberResponse(BaseModel):
    id: UUID
    business_account_id: UUID
    user_id: UUID
    telegram_id: int
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class BusinessAccountMemberAddResponse(BaseModel):
    member: BusinessAccountMemberResponse
    notification_sent: bool


class BusinessAccountMemberDeactivateResponse(BaseModel):
    business_account_id: UUID
    user_id: UUID
    deactivated: bool


class BusinessBalanceTransactionResponse(BaseModel):
    id: UUID
    business_account_id: UUID
    user_id: UUID | None
    generation_job_id: UUID | None
    type: str
    amount_usd: Decimal
    balance_available_after: Decimal
    balance_frozen_after: Decimal
    reason: str | None
    created_at: datetime


class BusinessBalanceTransactionsResponse(BaseModel):
    items: list[BusinessBalanceTransactionResponse]


class BusinessUsageMemberResponse(BaseModel):
    user_id: UUID
    telegram_id: int
    username: str | None
    role: str
    generations_count: int
    spent_usd: Decimal


class BusinessUsageResponse(BaseModel):
    business_account: BusinessAccountResponse
    topups_usd: Decimal
    spent_usd: Decimal
    refunded_usd: Decimal
    members: list[BusinessUsageMemberResponse]
