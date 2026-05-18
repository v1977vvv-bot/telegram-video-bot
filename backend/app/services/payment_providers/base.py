from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PaymentInvoiceResult:
    provider: str
    provider_invoice_id: str
    amount_usd: Decimal
    provider_asset: str
    provider_amount: Decimal
    payment_url: str
    expires_at: datetime | None
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class NormalizedPaymentUpdate:
    provider: str
    provider_invoice_id: str
    status: str
    amount_usd: Decimal
    asset: str
    user_id: UUID | None
    payment_id: UUID | None
    raw: dict[str, Any]
