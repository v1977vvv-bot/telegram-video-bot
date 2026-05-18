from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import httpx

from backend.app.services.payment_providers.base import PaymentInvoiceResult
from shared.app.config import Settings, get_settings
from shared.app.enums import PaymentProvider
from shared.app.exceptions import AppError
from shared.app.logging import get_logger

logger = get_logger(__name__)


class CryptoBotPayClient:
    """Async client for Telegram Crypto Bot / Crypto Pay invoices."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._base_url = self._settings.cryptobot_pay_api_base_url.rstrip("/")

    async def create_invoice(
        self,
        *,
        payment_id: UUID,
        user_id: UUID,
        amount_usd: Decimal,
        provider_amount: Decimal,
        description: str,
        metadata: dict[str, object],
    ) -> PaymentInvoiceResult:
        self._ensure_configured()
        invoice_payload = {
            "payment_id": str(payment_id),
            "user_id": str(user_id),
            "package_amount_usd": str(amount_usd),
            **metadata,
        }
        payload: dict[str, object] = {
            "asset": self._settings.cryptobot_pay_asset.upper(),
            "amount": _amount_string(provider_amount),
            "description": description[:1024],
            "payload": json.dumps(invoice_payload, ensure_ascii=False, separators=(",", ":")),
            "allow_comments": self._settings.cryptobot_pay_allow_comments,
            "allow_anonymous": self._settings.cryptobot_pay_allow_anonymous,
            "expires_in": self._settings.cryptobot_pay_expires_in_seconds,
        }
        data = await self._post("createInvoice", payload)
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, dict):
            raise AppError(
                "CryptoBot returned an invalid invoice response",
                code="cryptobot_invalid_response",
                status_code=502,
            )

        provider_invoice_id = _string_or_int(result.get("invoice_id"))
        payment_url = _first_str(
            result,
            "bot_invoice_url",
            "mini_app_invoice_url",
            "web_app_invoice_url",
            "pay_url",
        )
        if provider_invoice_id is None or payment_url is None:
            raise AppError(
                "CryptoBot returned an incomplete invoice response",
                code="cryptobot_invalid_response",
                status_code=502,
            )

        asset = _optional_str(result.get("asset")) or self._settings.cryptobot_pay_asset.upper()
        provider_amount_result = _decimal_or_default(result.get("amount"), provider_amount)
        expires_at = _parse_datetime(_optional_str(result.get("expiration_date")))
        raw_payload = dict(data)
        raw_payload["ultronlab_metadata"] = {
            **invoice_payload,
            "provider": PaymentProvider.CRYPTOBOT.value,
            "provider_currency": asset,
            "provider_amount": str(provider_amount_result),
            "payment_url": payment_url,
        }
        return PaymentInvoiceResult(
            provider=PaymentProvider.CRYPTOBOT.value,
            provider_invoice_id=provider_invoice_id,
            amount_usd=amount_usd,
            provider_asset=asset,
            provider_amount=provider_amount_result,
            payment_url=payment_url,
            expires_at=expires_at,
            raw=raw_payload,
        )

    async def get_invoice(self, provider_invoice_id: str) -> dict[str, Any] | None:
        self._ensure_configured()
        data = await self._post("getInvoices", {"invoice_ids": provider_invoice_id, "count": 1})
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, dict):
            items = result.get("items")
            if isinstance(items, list):
                if not items:
                    return None
                item = items[0]
                return item if isinstance(item, dict) else None
            return result
        if isinstance(result, list) and result:
            item = result[0]
            return item if isinstance(item, dict) else None
        return None

    async def _post(self, method: str, payload: dict[str, object]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(30.0, connect=5.0),
            ) as client:
                response = await client.post(f"/{method}", json=payload, headers=self._headers())
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            logger.warning(
                "CryptoBot API request failed method=%s error=%s",
                method,
                exc.__class__.__name__,
            )
            raise AppError(
                "Не удалось создать счёт CryptoBot. Попробуйте позже.",
                code="cryptobot_request_failed",
                status_code=502,
            ) from exc
        except ValueError as exc:
            raise AppError(
                "CryptoBot returned invalid JSON",
                code="cryptobot_invalid_response",
                status_code=502,
            ) from exc

        if not isinstance(data, dict) or data.get("ok") is not True:
            error = data.get("error") if isinstance(data, dict) else None
            logger.warning(
                "CryptoBot rejected request method=%s provider_error=%s",
                method,
                str(error)[:200] if error is not None else None,
            )
            raise AppError(
                "CryptoBot rejected the invoice request",
                code="cryptobot_request_rejected",
                status_code=502,
            )
        return data

    def verify_webhook_signature(self, body: bytes, signature: str | None) -> bool:
        if not signature:
            return False
        token = self._settings.cryptobot_pay_api_token.strip()
        if not token:
            return False
        secret = hashlib.sha256(token.encode()).digest()
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def _headers(self) -> dict[str, str]:
        return {"Crypto-Pay-API-Token": self._settings.cryptobot_pay_api_token}

    def _ensure_configured(self) -> None:
        if not self._settings.cryptobot_pay_enabled:
            raise AppError("CryptoBot payments are disabled", code="cryptobot_disabled")
        if not self._settings.cryptobot_pay_configured:
            raise AppError("CryptoBot is not configured", code="cryptobot_not_configured")


def extract_invoice_from_update(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("update_type") != "invoice_paid":
        return None
    invoice = payload.get("payload")
    return invoice if isinstance(invoice, dict) else None


def invoice_provider_id(invoice: dict[str, Any]) -> str | None:
    return _string_or_int(invoice.get("invoice_id"))


def invoice_status(invoice: dict[str, Any]) -> str | None:
    return _optional_str(invoice.get("status"))


def invoice_asset(invoice: dict[str, Any]) -> str | None:
    return (
        _optional_str(invoice.get("asset"))
        or _optional_str(invoice.get("paid_asset"))
        or _optional_str(invoice.get("fiat"))
    )


def invoice_amount(invoice: dict[str, Any]) -> Decimal | None:
    for key in ("amount", "paid_amount"):
        value = invoice.get(key)
        if value is not None:
            try:
                return Decimal(str(value)).quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                return None
    return None


def _amount_string(amount: Decimal) -> str:
    return str(amount.quantize(Decimal("0.01")))


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_or_int(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, int):
        return str(value)
    return None


def _first_str(data: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _decimal_or_default(value: object, default: Decimal) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return default


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
