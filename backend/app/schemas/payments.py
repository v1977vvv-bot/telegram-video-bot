from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class PaymentPackageResponse(BaseModel):
    amount_usd: Decimal
    display_label: str
    provider_currency: str
    provider_amount: Decimal


class PaymentPackagesResponse(BaseModel):
    packages_enabled: bool
    custom_amount_enabled: bool
    display_currency: str
    provider_currency: str
    packages: list[PaymentPackageResponse]


class CreatePaymentInvoiceRequest(BaseModel):
    telegram_id: int = Field(gt=0)
    amount_usd: Decimal = Field(gt=Decimal("0"))


class PaymentInvoiceResponse(BaseModel):
    payment_id: UUID
    amount_usd: Decimal
    display_currency: str
    provider_currency: str
    provider_amount: Decimal
    payment_url: str
    status: str


class CryptomusWebhookResponse(BaseModel):
    ok: bool
    payment_id: UUID | None = None
    status: str | None = None
