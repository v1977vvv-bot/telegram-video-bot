from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CryptomusInvoice:
    provider_invoice_id: str
    payment_url: str
    raw_payload: dict[str, object]


class CryptomusService:
    """Cryptomus billing boundary. Real HTTP calls are added in the payment stage."""

    async def create_invoice(self, *, user_id: UUID, amount_usd: Decimal) -> CryptomusInvoice:
        raise NotImplementedError("Cryptomus invoice creation is not implemented in stage 1")

    async def verify_webhook(self, *, payload: bytes, signature: str) -> bool:
        raise NotImplementedError("Cryptomus webhook verification is not implemented in stage 1")
