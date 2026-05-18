from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from backend.app.services.cryptomus import CryptomusService
from backend.app.services.payment_providers.base import PaymentInvoiceResult
from shared.app.config import Settings, get_settings
from shared.app.enums import PaymentProvider
from shared.app.exceptions import AppError


class CryptomusPaymentProvider:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._service = CryptomusService(self._settings)

    async def create_invoice(
        self,
        *,
        payment_id: UUID,
        user_id: UUID,
        amount_usd: Decimal,
        provider_amount: Decimal,
    ) -> PaymentInvoiceResult:
        if not self._settings.cryptomus_enabled:
            raise AppError("Cryptomus payments are disabled", code="cryptomus_disabled")
        invoice = await self._service.create_invoice(
            payment_id=payment_id,
            user_id=user_id,
            amount_usd=amount_usd,
            provider_amount=provider_amount,
            display_currency=self._settings.payment_display_currency,
            provider_currency=self._settings.payment_provider_currency,
        )
        return PaymentInvoiceResult(
            provider=PaymentProvider.CRYPTOMUS.value,
            provider_invoice_id=invoice.provider_invoice_id,
            amount_usd=amount_usd,
            provider_asset=self._settings.payment_provider_currency,
            provider_amount=provider_amount,
            payment_url=invoice.payment_url,
            expires_at=None,
            raw=invoice.raw_payload,
        )
