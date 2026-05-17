from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.payment import Payment
from backend.app.repositories.users import UserRepository
from backend.app.schemas.payments import (
    CreatePaymentInvoiceRequest,
    CryptomusWebhookResponse,
    PaymentInvoiceResponse,
    PaymentPackageResponse,
    PaymentPackagesResponse,
)
from backend.app.services.balances import BalanceService
from backend.app.services.cryptomus import CryptomusService
from backend.app.services.payment_packages import PaymentPackageService
from shared.app.config import get_settings
from shared.app.database import get_session
from shared.app.enums import PaymentStatus
from shared.app.exceptions import AppError

router = APIRouter(prefix="/payments", tags=["payments"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/packages", response_model=PaymentPackagesResponse)
async def get_payment_packages() -> PaymentPackagesResponse:
    settings = get_settings()
    package_service = PaymentPackageService(settings)
    packages = package_service.get_payment_packages()
    return PaymentPackagesResponse(
        packages_enabled=settings.payment_packages_enabled,
        custom_amount_enabled=settings.payment_custom_amount_enabled,
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


@router.post("/cryptomus/invoices", response_model=PaymentInvoiceResponse)
async def create_cryptomus_invoice(
    payload: CreatePaymentInvoiceRequest,
    session: SessionDep,
) -> PaymentInvoiceResponse:
    settings = get_settings()
    package_service = PaymentPackageService(settings)
    amount_usd = package_service.validate_package_amount(payload.amount_usd)
    provider_amount = package_service.provider_amount_for_usd(amount_usd)

    async with session.begin():
        user = await UserRepository().get_by_telegram_id(session, payload.telegram_id)
        if user is None:
            raise AppError("User not found", code="user_not_found", status_code=404)
        payment = Payment(
            user_id=user.id,
            provider="cryptomus",
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
        invoice = await CryptomusService(settings).create_invoice(
            payment_id=payment_id,
            user_id=user_id,
            amount_usd=amount_usd,
            provider_amount=provider_amount,
            display_currency=settings.payment_display_currency,
            provider_currency=settings.payment_provider_currency,
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
        payment.raw_payload = invoice.raw_payload

    return PaymentInvoiceResponse(
        payment_id=payment_id,
        amount_usd=amount_usd,
        display_currency=settings.payment_display_currency,
        provider_currency=settings.payment_provider_currency,
        provider_amount=provider_amount,
        payment_url=invoice.payment_url,
        status=PaymentStatus.PENDING.value,
    )


@router.post("/cryptomus/webhook", response_model=CryptomusWebhookResponse)
async def handle_cryptomus_webhook(
    request: Request,
    session: SessionDep,
) -> CryptomusWebhookResponse:
    settings = get_settings()
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
        payment = await _get_payment_for_webhook(
            session,
            provider_invoice_id=provider_invoice_id,
            order_id=order_id,
        )
        if payment is None:
            raise AppError("Payment not found", code="payment_not_found", status_code=404)
        _validate_payment_package_record(payment)
        provider_status = _map_cryptomus_status(incoming_status)
        previous_status = payment.status
        payment.raw_payload = payload

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

    return CryptomusWebhookResponse(ok=True, payment_id=payment.id, status=payment.status)


async def _get_payment_for_webhook(
    session: AsyncSession,
    *,
    provider_invoice_id: str | None,
    order_id: str | None,
) -> Payment | None:
    conditions = []
    if provider_invoice_id is not None:
        conditions.append(Payment.provider_invoice_id == provider_invoice_id)
    payment_id = _payment_id_from_order_id(order_id)
    if payment_id is not None:
        conditions.append(Payment.id == payment_id)
    if not conditions:
        return None
    result = await session.execute(select(Payment).where(or_(*conditions)).with_for_update())
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


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
