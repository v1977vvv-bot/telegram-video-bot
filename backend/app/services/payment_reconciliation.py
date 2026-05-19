from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.balance_transaction import BalanceTransaction
from backend.app.models.payment import Payment
from backend.app.models.user import User
from backend.app.services.balances import BalanceService
from backend.app.services.payment_packages import PaymentPackageService
from backend.app.services.payment_providers.cryptobot import (
    CryptoBotPayClient,
    invoice_amount,
    invoice_asset,
    invoice_provider_id,
    invoice_status,
)
from backend.app.services.telegram_notify import TelegramNotificationService
from shared.app.config import Settings, get_settings
from shared.app.enums import BalanceTransactionType, PaymentProvider, PaymentStatus
from shared.app.exceptions import AppError
from shared.app.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PaymentReconciliationResult:
    payment_id: UUID
    provider: str
    provider_invoice_id: str | None
    old_status: str
    new_status: str
    credited: bool
    credited_amount_usd: Decimal | None
    message: str
    telegram_notification_sent: bool | None = None
    warning: str | None = None


class CryptoBotPaymentReconciliationService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        client: CryptoBotPayClient | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._client = client or CryptoBotPayClient(self._settings)

    async def reconcile_payment(
        self,
        *,
        payment_id: UUID | None = None,
        provider_invoice_id: str | None = None,
        notify_user: bool = True,
    ) -> PaymentReconciliationResult:
        if payment_id is None and provider_invoice_id is None:
            raise AppError("payment_id or provider_invoice_id is required", code="payment_required")

        async with self._session.begin():
            payment = await self._load_payment_for_recheck(
                payment_id=payment_id,
                provider_invoice_id=provider_invoice_id,
                for_update=False,
            )
            if payment.provider != PaymentProvider.CRYPTOBOT.value:
                raise AppError(
                    "Only CryptoBot payments can be rechecked by this endpoint",
                    code="unsupported_payment_provider",
                    status_code=400,
                )
            if payment.provider_invoice_id is None:
                raise AppError(
                    "Payment has no CryptoBot invoice id",
                    code="payment_invoice_missing",
                    status_code=400,
                )

            old_status = payment.status
            if payment.status in {PaymentStatus.PAID.value, PaymentStatus.PAID_OVER.value}:
                credited = await self._has_balance_deposit(payment.id)
                return PaymentReconciliationResult(
                    payment_id=payment.id,
                    provider=payment.provider,
                    provider_invoice_id=payment.provider_invoice_id,
                    old_status=old_status,
                    new_status=payment.status,
                    credited=False,
                    credited_amount_usd=None,
                    message=(
                        "Payment is already paid and credited"
                        if credited
                        else "Payment is already marked paid"
                    ),
                )
            loaded_payment_id = payment.id
            loaded_invoice_id = payment.provider_invoice_id

        try:
            invoice = await self._client.get_invoice(loaded_invoice_id)
        except AppError as exc:
            logger.warning(
                "CryptoBot reconciliation API error payment_id=%s invoice_id=%s code=%s",
                loaded_payment_id,
                loaded_invoice_id,
                exc.code,
            )
            raise

        if invoice is None:
            return PaymentReconciliationResult(
                payment_id=loaded_payment_id,
                provider=PaymentProvider.CRYPTOBOT.value,
                provider_invoice_id=loaded_invoice_id,
                old_status=old_status,
                new_status=old_status,
                credited=False,
                credited_amount_usd=None,
                message="CryptoBot invoice was not found",
            )

        async with self._session.begin():
            payment = await self._load_payment_for_recheck(
                payment_id=loaded_payment_id,
                for_update=False,
            )
            self._validate_invoice(payment, invoice)
        status = (invoice_status(invoice) or "").lower()

        if status == "paid":
            return await self._credit_paid_invoice(
                payment_id=loaded_payment_id,
                verified_invoice=invoice,
                old_status=old_status,
                notify_user=notify_user,
            )
        if status == "expired":
            return await self._set_payment_terminal_status(
                payment_id=payment.id,
                old_status=old_status,
                status=PaymentStatus.EXPIRED.value,
                invoice=invoice,
                message="CryptoBot invoice is expired",
            )
        if status in {"cancelled", "canceled"}:
            return await self._set_payment_terminal_status(
                payment_id=payment.id,
                old_status=old_status,
                status=PaymentStatus.CANCELLED.value,
                invoice=invoice,
                message="CryptoBot invoice is cancelled",
            )

        await self._store_recheck_payload(payment.id, invoice)
        return PaymentReconciliationResult(
            payment_id=payment.id,
            provider=payment.provider,
            provider_invoice_id=payment.provider_invoice_id,
            old_status=old_status,
            new_status=payment.status,
            credited=False,
            credited_amount_usd=None,
            message=f"CryptoBot invoice is still {status or 'not paid'}",
        )

    async def _credit_paid_invoice(
        self,
        *,
        payment_id: UUID,
        verified_invoice: dict[str, object],
        old_status: str,
        notify_user: bool,
    ) -> PaymentReconciliationResult:
        notification: tuple[int, Decimal, Decimal] | None = None
        async with self._session.begin():
            payment = await self._load_payment_for_recheck(payment_id=payment_id, for_update=True)
            if payment.status in {PaymentStatus.PAID.value, PaymentStatus.PAID_OVER.value}:
                return PaymentReconciliationResult(
                    payment_id=payment.id,
                    provider=payment.provider,
                    provider_invoice_id=payment.provider_invoice_id,
                    old_status=old_status,
                    new_status=payment.status,
                    credited=False,
                    credited_amount_usd=None,
                    message="Payment is already paid and credited",
                )
            self._validate_invoice(payment, verified_invoice)
            _validate_payment_package(payment)
            account = await BalanceService(self._session).add_balance_in_transaction(
                user_id=payment.user_id,
                amount_usd=payment.amount_usd,
                reason="CryptoBot payment reconciliation top-up",
                related_payment_id=payment.id,
            )
            payment.status = PaymentStatus.PAID.value
            payment.paid_at = _parse_datetime(_optional_str(verified_invoice.get("paid_at")))
            payment.paid_at = payment.paid_at or datetime.now(UTC)
            payment.raw_payload = _merge_payment_payload(
                payment.raw_payload,
                _reconciliation_payload(verified_invoice),
            )
            user = await self._session.get(User, payment.user_id)
            if user is not None:
                notification = (user.telegram_id, payment.amount_usd, account.available_usd)
            provider_invoice_id = payment.provider_invoice_id
            new_status = payment.status
            amount = payment.amount_usd

        notification_sent: bool | None = None
        warning: str | None = None
        if notify_user and notification is not None:
            telegram_id, amount_for_message, available = notification
            notification_sent, warning = await _notify_payment_success(
                telegram_id=telegram_id,
                amount=amount_for_message,
                available=available,
            )

        return PaymentReconciliationResult(
            payment_id=payment_id,
            provider=PaymentProvider.CRYPTOBOT.value,
            provider_invoice_id=provider_invoice_id,
            old_status=old_status,
            new_status=new_status,
            credited=True,
            credited_amount_usd=amount,
            message="Payment verified as paid and balance credited",
            telegram_notification_sent=notification_sent,
            warning=warning,
        )

    async def _set_payment_terminal_status(
        self,
        *,
        payment_id: UUID,
        old_status: str,
        status: str,
        invoice: dict[str, object],
        message: str,
    ) -> PaymentReconciliationResult:
        async with self._session.begin():
            payment = await self._load_payment_for_recheck(payment_id=payment_id, for_update=True)
            if payment.status not in {PaymentStatus.PAID.value, PaymentStatus.PAID_OVER.value}:
                payment.status = status
                payment.raw_payload = _merge_payment_payload(
                    payment.raw_payload,
                    _reconciliation_payload(invoice),
                )
            return PaymentReconciliationResult(
                payment_id=payment.id,
                provider=payment.provider,
                provider_invoice_id=payment.provider_invoice_id,
                old_status=old_status,
                new_status=payment.status,
                credited=False,
                credited_amount_usd=None,
                message=message,
            )

    async def _store_recheck_payload(
        self,
        payment_id: UUID,
        invoice: dict[str, object],
    ) -> None:
        async with self._session.begin():
            payment = await self._load_payment_for_recheck(payment_id=payment_id, for_update=True)
            payment.raw_payload = _merge_payment_payload(
                payment.raw_payload,
                _reconciliation_payload(invoice),
            )

    async def _load_payment_for_recheck(
        self,
        *,
        payment_id: UUID | None = None,
        provider_invoice_id: str | None = None,
        for_update: bool,
    ) -> Payment:
        query = select(Payment).where(Payment.provider == PaymentProvider.CRYPTOBOT.value)
        if payment_id is not None:
            query = query.where(Payment.id == payment_id)
        elif provider_invoice_id is not None:
            query = query.where(Payment.provider_invoice_id == provider_invoice_id)
        if for_update:
            query = query.with_for_update()
        result = await self._session.execute(query)
        payment = result.scalar_one_or_none()
        if payment is None:
            raise AppError("CryptoBot payment not found", code="payment_not_found", status_code=404)
        return payment

    async def _has_balance_deposit(self, payment_id: UUID) -> bool:
        result = await self._session.execute(
            select(BalanceTransaction.id)
            .where(
                BalanceTransaction.payment_id == payment_id,
                BalanceTransaction.type == BalanceTransactionType.DEPOSIT.value,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    def _validate_invoice(self, payment: Payment, invoice: dict[str, object]) -> None:
        provider_invoice_id = invoice_provider_id(invoice)
        if provider_invoice_id != payment.provider_invoice_id:
            raise AppError(
                "CryptoBot invoice id does not match local payment",
                code="cryptobot_invoice_mismatch",
                status_code=400,
            )
        amount = invoice_amount(invoice)
        if amount is None or amount != payment.amount_usd.quantize(Decimal("0.01")):
            raise AppError(
                "CryptoBot invoice amount does not match local payment",
                code="cryptobot_amount_mismatch",
                status_code=400,
            )
        asset = invoice_asset(invoice)
        expected_asset = (
            _payment_provider_currency(payment) or self._settings.payment_provider_currency
        )
        if asset is not None and asset.upper() != expected_asset.upper():
            raise AppError(
                "CryptoBot invoice asset does not match local payment",
                code="cryptobot_asset_mismatch",
                status_code=400,
            )


async def _notify_payment_success(
    *,
    telegram_id: int,
    amount: Decimal,
    available: Decimal,
) -> tuple[bool, str | None]:
    try:
        sent = await TelegramNotificationService().send_message(
            telegram_id=telegram_id,
            message=(
                "✅ Баланс пополнен.\n\n"
                f"Зачислено: ${amount.quantize(Decimal('0.01'))}\n"
                f"Текущий баланс: ${available.quantize(Decimal('0.01'))}"
            ),
        )
        return sent, None if sent else "Telegram returned ok=false"
    except Exception as exc:
        logger.warning(
            "Telegram reconciliation notification failed telegram_id=%s error=%s",
            telegram_id,
            exc.__class__.__name__,
        )
        return False, f"Telegram notification failed: {exc.__class__.__name__}"


def _validate_payment_package(payment: Payment) -> None:
    PaymentPackageService().validate_package_amount(payment.amount_usd.quantize(Decimal("0.01")))


def _payment_provider_currency(payment: Payment) -> str | None:
    metadata = payment.raw_payload if isinstance(payment.raw_payload, dict) else {}
    value = _optional_str(metadata.get("provider_currency"))
    if value is not None:
        return value
    nested = metadata.get("ultronlab_metadata")
    if isinstance(nested, dict):
        return _optional_str(nested.get("provider_currency"))
    return None


def _merge_payment_payload(
    existing: dict[str, object] | None,
    update: dict[str, object],
) -> dict[str, object]:
    base = dict(existing) if isinstance(existing, dict) else {}
    base.update(update)
    return base


def _reconciliation_payload(invoice: dict[str, object]) -> dict[str, object]:
    return {
        "reconciliation": {
            "verified_invoice": invoice,
            "at": datetime.now(UTC).isoformat(),
        }
    }


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
