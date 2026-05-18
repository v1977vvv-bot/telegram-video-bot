from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.payment import Payment
from backend.app.models.user import User
from backend.app.repositories.users import UserRepository
from backend.app.schemas.payments import (
    CreatePaymentInvoiceRequest,
    PaymentInvoiceResponse,
    PaymentPackageResponse,
    PaymentPackagesResponse,
    PaymentWebhookResponse,
)
from backend.app.services.balances import BalanceService
from backend.app.services.cryptomus import CryptomusService
from backend.app.services.payment_packages import PaymentPackageService
from backend.app.services.payment_providers.cryptobot import (
    CryptoBotPayClient,
    extract_invoice_from_update,
    invoice_amount,
    invoice_asset,
    invoice_provider_id,
    invoice_status,
)
from backend.app.services.payment_providers.cryptomus import CryptomusPaymentProvider
from backend.app.services.telegram_notify import TelegramNotificationService
from shared.app.config import get_settings
from shared.app.database import get_session
from shared.app.enums import PaymentProvider, PaymentStatus
from shared.app.exceptions import AppError
from shared.app.logging import get_logger

router = APIRouter(prefix="/payments", tags=["payments"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
logger = get_logger(__name__)


@router.get("/packages", response_model=PaymentPackagesResponse)
async def get_payment_packages() -> PaymentPackagesResponse:
    settings = get_settings()
    package_service = PaymentPackageService(settings)
    packages = package_service.get_payment_packages()
    return PaymentPackagesResponse(
        packages_enabled=settings.payment_packages_enabled,
        custom_amount_enabled=settings.payment_custom_amount_enabled,
        payment_provider=settings.payment_provider_normalized,
        display_currency=settings.payment_display_currency,
        provider_currency=settings.payment_provider_currency,
        packages=[
            PaymentPackageResponse(
                amount_usd=package.amount_usd,
                display_label=package.display_label,
                provider_currency=package.provider_currency,
                provider_amount=package.provider_amount,
            )
            for package in packages
        ],
    )


@router.post("/invoices", response_model=PaymentInvoiceResponse)
async def create_payment_invoice(
    payload: CreatePaymentInvoiceRequest,
    session: SessionDep,
) -> PaymentInvoiceResponse:
    settings = get_settings()
    provider = settings.payment_provider_normalized
    if provider == PaymentProvider.MANUAL.value:
        raise AppError(
            "Автоматическое пополнение временно недоступно. Напишите в поддержку.",
            code="payment_provider_manual",
            status_code=503,
        )
    if provider == PaymentProvider.CRYPTOBOT.value:
        return await _create_provider_invoice(
            payload=payload,
            session=session,
            provider=PaymentProvider.CRYPTOBOT.value,
        )
    if provider == PaymentProvider.CRYPTOMUS.value:
        return await _create_provider_invoice(
            payload=payload,
            session=session,
            provider=PaymentProvider.CRYPTOMUS.value,
        )
    raise AppError("Unsupported payment provider", code="unsupported_payment_provider")


@router.post("/cryptobot/invoices", response_model=PaymentInvoiceResponse)
async def create_cryptobot_invoice(
    payload: CreatePaymentInvoiceRequest,
    session: SessionDep,
) -> PaymentInvoiceResponse:
    return await _create_provider_invoice(
        payload=payload,
        session=session,
        provider=PaymentProvider.CRYPTOBOT.value,
    )


@router.post("/cryptomus/invoices", response_model=PaymentInvoiceResponse)
async def create_cryptomus_invoice(
    payload: CreatePaymentInvoiceRequest,
    session: SessionDep,
) -> PaymentInvoiceResponse:
    return await _create_provider_invoice(
        payload=payload,
        session=session,
        provider=PaymentProvider.CRYPTOMUS.value,
    )


@router.post("/cryptobot/webhook", response_model=PaymentWebhookResponse)
async def handle_cryptobot_webhook(
    request: Request,
    session: SessionDep,
) -> PaymentWebhookResponse:
    settings = get_settings()
    body = await request.body()
    client = CryptoBotPayClient(settings)
    signature = request.headers.get("crypto-pay-api-signature")
    if not client.verify_webhook_signature(body, signature):
        raise AppError("Invalid CryptoBot signature", code="invalid_signature", status_code=403)

    try:
        payload = await request.json()
    except ValueError as exc:
        raise AppError("Invalid webhook payload", code="invalid_webhook", status_code=400) from exc
    if not isinstance(payload, dict):
        raise AppError("Invalid webhook payload", code="invalid_webhook", status_code=400)

    webhook_invoice = extract_invoice_from_update(payload)
    if webhook_invoice is None:
        return PaymentWebhookResponse(ok=True)
    provider_invoice_id = invoice_provider_id(webhook_invoice)
    if provider_invoice_id is None:
        raise AppError("Webhook has no invoice id", code="invalid_webhook", status_code=400)

    verified_invoice = await client.get_invoice(provider_invoice_id)
    if verified_invoice is None:
        logger.warning("CryptoBot webhook invoice not found invoice_id=%s", provider_invoice_id)
        return PaymentWebhookResponse(ok=True)

    verified_status = invoice_status(verified_invoice)
    notification: tuple[int, Decimal, Decimal] | None = None
    async with session.begin():
        payment = await _get_payment_by_provider_invoice(
            session,
            provider=PaymentProvider.CRYPTOBOT.value,
            provider_invoice_id=provider_invoice_id,
        )
        if payment is None:
            logger.warning("CryptoBot webhook payment not found invoice_id=%s", provider_invoice_id)
            return PaymentWebhookResponse(ok=True)
        _validate_payment_package_record(payment)
        _validate_cryptobot_verified_invoice(
            payment,
            verified_invoice,
            settings.payment_provider_currency,
        )
        previous_status = payment.status
        payment.raw_payload = _merge_payment_payload(
            payment.raw_payload,
            {"webhook": payload, "verified_invoice": verified_invoice},
        )

        if verified_status == "paid":
            if previous_status != PaymentStatus.PAID.value:
                account = await BalanceService(session).add_balance_in_transaction(
                    user_id=payment.user_id,
                    amount_usd=payment.amount_usd,
                    reason="CryptoBot package top-up",
                    related_payment_id=payment.id,
                )
                payment.paid_at = _parse_datetime(_optional_str(verified_invoice.get("paid_at")))
                payment.paid_at = payment.paid_at or datetime.now(UTC)
                user = await session.get(User, payment.user_id)
                if user is not None:
                    notification = (user.telegram_id, payment.amount_usd, account.available_usd)
            payment.status = PaymentStatus.PAID.value
        elif verified_status == "expired":
            payment.status = PaymentStatus.EXPIRED.value

        payment_id = payment.id
        status = payment.status

    if notification is not None:
        telegram_id, amount, available = notification
        await _notify_payment_success(telegram_id=telegram_id, amount=amount, available=available)

    return PaymentWebhookResponse(ok=True, payment_id=payment_id, status=status)


@router.post("/cryptomus/webhook", response_model=PaymentWebhookResponse)
async def handle_cryptomus_webhook(
    request: Request,
    session: SessionDep,
) -> PaymentWebhookResponse:
    settings = get_settings()
    if not settings.cryptomus_enabled:
        raise AppError(
            "Cryptomus payments are disabled",
            code="cryptomus_disabled",
            status_code=403,
        )
    payload = await request.json()
    if not isinstance(payload, dict):
        raise AppError("Invalid webhook payload", code="invalid_webhook", status_code=400)
    if not CryptomusService(settings).verify_webhook_payload(payload):
        raise AppError("Invalid Cryptomus signature", code="invalid_signature", status_code=403)

    provider_invoice_id = _optional_str(payload.get("uuid"))
    order_id = _optional_str(payload.get("order_id"))
    incoming_status = _optional_str(payload.get("status")) or _optional_str(
        payload.get("payment_status")
    )
    if provider_invoice_id is None and order_id is None:
        raise AppError("Webhook has no payment identifier", code="invalid_webhook", status_code=400)

    async with session.begin():
        payment = await _get_cryptomus_payment_for_webhook(
            session,
            provider_invoice_id=provider_invoice_id,
            order_id=order_id,
        )
        if payment is None:
            raise AppError("Payment not found", code="payment_not_found", status_code=404)
        _validate_payment_package_record(payment)
        provider_status = _map_cryptomus_status(incoming_status)
        previous_status = payment.status
        payment.raw_payload = _merge_payment_payload(payment.raw_payload, {"webhook": payload})

        if provider_status in {PaymentStatus.PAID.value, PaymentStatus.PAID_OVER.value}:
            if previous_status not in {PaymentStatus.PAID.value, PaymentStatus.PAID_OVER.value}:
                await BalanceService(session).add_balance_in_transaction(
                    user_id=payment.user_id,
                    amount_usd=payment.amount_usd,
                    reason="Cryptomus package top-up",
                    related_payment_id=payment.id,
                )
                payment.paid_at = datetime.now(UTC)
            payment.status = provider_status
        elif provider_status is not None:
            payment.status = provider_status

    return PaymentWebhookResponse(ok=True, payment_id=payment.id, status=payment.status)


async def _create_provider_invoice(
    *,
    payload: CreatePaymentInvoiceRequest,
    session: AsyncSession,
    provider: str,
) -> PaymentInvoiceResponse:
    settings = get_settings()
    if provider == PaymentProvider.CRYPTOMUS.value and not settings.cryptomus_enabled:
        raise AppError(
            "Cryptomus payments are disabled",
            code="cryptomus_disabled",
            status_code=403,
        )
    if provider == PaymentProvider.CRYPTOBOT.value and not settings.cryptobot_pay_enabled:
        raise AppError(
            "CryptoBot payments are disabled",
            code="cryptobot_disabled",
            status_code=403,
        )

    package_service = PaymentPackageService(settings)
    amount_usd = package_service.validate_package_amount(payload.amount_usd)
    provider_amount = package_service.provider_amount_for_usd(amount_usd)

    async with session.begin():
        user = await UserRepository().get_by_telegram_id(session, payload.telegram_id)
        if user is None:
            raise AppError("User not found", code="user_not_found", status_code=404)
        payment = Payment(
            user_id=user.id,
            provider=provider,
            amount_usd=amount_usd,
            currency=settings.payment_display_currency,
            status=PaymentStatus.PENDING.value,
            raw_payload={
                "package_amount_usd": str(amount_usd),
                "display_currency": settings.payment_display_currency,
                "provider_currency": settings.payment_provider_currency,
                "provider_amount": str(provider_amount),
            },
        )
        session.add(payment)
        await session.flush()
        payment_id = payment.id
        user_id = user.id

    try:
        if provider == PaymentProvider.CRYPTOBOT.value:
            invoice = await CryptoBotPayClient(settings).create_invoice(
                payment_id=payment_id,
                user_id=user_id,
                amount_usd=amount_usd,
                provider_amount=provider_amount,
                description="Sync AI balance top-up",
                metadata={
                    "display_currency": settings.payment_display_currency,
                    "provider_currency": settings.payment_provider_currency,
                },
            )
        else:
            invoice = await CryptomusPaymentProvider(settings).create_invoice(
                payment_id=payment_id,
                user_id=user_id,
                amount_usd=amount_usd,
                provider_amount=provider_amount,
            )
    except Exception:
        async with session.begin():
            payment = await session.get(Payment, payment_id)
            if payment is not None:
                payment.status = PaymentStatus.FAILED.value
        raise

    async with session.begin():
        payment = await session.get(Payment, payment_id)
        if payment is None:
            raise AppError("Payment not found", code="payment_not_found", status_code=404)
        payment.provider_invoice_id = invoice.provider_invoice_id
        payment.raw_payload = invoice.raw

    return PaymentInvoiceResponse(
        provider=invoice.provider,
        payment_id=payment_id,
        amount_usd=amount_usd,
        display_currency=settings.payment_display_currency,
        provider_currency=invoice.provider_asset,
        provider_amount=invoice.provider_amount,
        payment_url=invoice.payment_url,
        expires_at=invoice.expires_at,
        status=PaymentStatus.PENDING.value,
    )


async def _get_payment_by_provider_invoice(
    session: AsyncSession,
    *,
    provider: str,
    provider_invoice_id: str,
) -> Payment | None:
    result = await session.execute(
        select(Payment)
        .where(
            Payment.provider == provider,
            Payment.provider_invoice_id == provider_invoice_id,
        )
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def _get_cryptomus_payment_for_webhook(
    session: AsyncSession,
    *,
    provider_invoice_id: str | None,
    order_id: str | None,
) -> Payment | None:
    conditions = [Payment.provider == PaymentProvider.CRYPTOMUS.value]
    identifiers = []
    if provider_invoice_id is not None:
        identifiers.append(Payment.provider_invoice_id == provider_invoice_id)
    payment_id = _payment_id_from_order_id(order_id)
    if payment_id is not None:
        identifiers.append(Payment.id == payment_id)
    if not identifiers:
        return None
    result = await session.execute(
        select(Payment).where(and_(*conditions), or_(*identifiers)).with_for_update()
    )
    return result.scalar_one_or_none()


def _payment_id_from_order_id(order_id: str | None) -> UUID | None:
    if not order_id or not order_id.startswith("payment-"):
        return None
    try:
        return UUID(hex=order_id.removeprefix("payment-"))
    except ValueError:
        return None


def _map_cryptomus_status(status: str | None) -> str | None:
    if status in {"paid", "paid_over"}:
        return PaymentStatus.PAID_OVER.value if status == "paid_over" else PaymentStatus.PAID.value
    if status in {"cancel"}:
        return PaymentStatus.CANCELLED.value
    if status in {"expired"}:
        return PaymentStatus.EXPIRED.value
    if status in {"fail", "wrong_amount", "system_fail", "refund_fail"}:
        return PaymentStatus.FAILED.value
    return None


def _validate_payment_package_record(payment: Payment) -> None:
    amount = payment.amount_usd.quantize(Decimal("0.01"))
    PaymentPackageService().validate_package_amount(amount)
    metadata = payment.raw_payload if isinstance(payment.raw_payload, dict) else {}
    nested_metadata = metadata.get("ultronlab_metadata")
    if isinstance(nested_metadata, dict):
        metadata = nested_metadata
    package_amount_raw = metadata.get("package_amount_usd") if isinstance(metadata, dict) else None
    if package_amount_raw is None:
        return
    try:
        package_amount = Decimal(str(package_amount_raw)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise AppError(
            "Invalid payment package metadata",
            code="payment_metadata_invalid",
            status_code=400,
        ) from exc
    if package_amount != amount:
        raise AppError(
            "Payment package metadata does not match payment amount",
            code="payment_metadata_mismatch",
            status_code=400,
        )


def _validate_cryptobot_verified_invoice(
    payment: Payment,
    invoice: dict[str, object],
    expected_asset: str,
) -> None:
    amount = invoice_amount(invoice)
    if amount is None or amount != payment.amount_usd.quantize(Decimal("0.01")):
        raise AppError(
            "CryptoBot invoice amount does not match local payment",
            code="cryptobot_amount_mismatch",
            status_code=400,
        )
    asset = invoice_asset(invoice)
    if asset is not None and asset.upper() != expected_asset.upper():
        raise AppError(
            "CryptoBot invoice asset does not match local payment",
            code="cryptobot_asset_mismatch",
            status_code=400,
        )


def _merge_payment_payload(
    existing: dict[str, object] | None,
    update: dict[str, object],
) -> dict[str, object]:
    base = dict(existing) if isinstance(existing, dict) else {}
    base.update(update)
    return base


async def _notify_payment_success(
    *,
    telegram_id: int,
    amount: Decimal,
    available: Decimal,
) -> None:
    try:
        await TelegramNotificationService().send_message(
            telegram_id=telegram_id,
            message=(
                "✅ Баланс пополнен\n\n"
                f"Зачислено: ${amount.quantize(Decimal('0.01'))}\n"
                f"Текущий баланс: ${available.quantize(Decimal('0.0001'))}"
            ),
        )
    except Exception:
        logger.warning("Telegram payment notification failed telegram_id=%s", telegram_id)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
