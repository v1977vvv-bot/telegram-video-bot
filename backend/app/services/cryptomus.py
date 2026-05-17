from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import httpx

from shared.app.config import Settings, get_settings
from shared.app.exceptions import AppError


@dataclass(frozen=True, slots=True)
class CryptomusInvoice:
    provider_invoice_id: str
    order_id: str
    payment_url: str
    raw_payload: dict[str, object]


class CryptomusService:
    """Cryptomus billing boundary for fixed USDT top-up packages."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._base_url = "https://api.cryptomus.com"

    async def create_invoice(
        self,
        *,
        payment_id: UUID,
        user_id: UUID,
        amount_usd: Decimal,
        provider_amount: Decimal,
        display_currency: str,
        provider_currency: str,
    ) -> CryptomusInvoice:
        self._ensure_configured()
        order_id = f"payment-{payment_id.hex}"
        callback_url = (
            f"{self._settings.backend_public_url.rstrip('/')}" "/api/v1/payments/cryptomus/webhook"
        )
        metadata = {
            "payment_id": str(payment_id),
            "user_id": str(user_id),
            "package_amount_usd": str(amount_usd),
            "display_currency": display_currency,
            "provider_currency": provider_currency,
        }
        payload: dict[str, object] = {
            "amount": str(amount_usd),
            "currency": display_currency,
            "to_currency": provider_currency,
            "order_id": order_id,
            "url_callback": callback_url,
            "additional_data": json.dumps(metadata, separators=(",", ":")),
            "is_payment_multiple": False,
        }
        body = _json_bytes(payload)
        async with httpx.AsyncClient(base_url=self._base_url, timeout=30.0) as client:
            response = await client.post(
                "/v1/payment",
                content=body,
                headers=self._headers(body),
            )
        try:
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AppError(
                "Не удалось создать счёт Cryptomus. Попробуйте позже.",
                code="cryptomus_invoice_failed",
                status_code=502,
            ) from exc

        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, dict):
            result = data if isinstance(data, dict) else {}
        provider_invoice_id = result.get("uuid") or result.get("payment_uuid")
        payment_url = result.get("url") or result.get("payment_url")
        if not isinstance(provider_invoice_id, str) or not isinstance(payment_url, str):
            raise AppError(
                "Cryptomus returned an invalid invoice response",
                code="cryptomus_invalid_response",
                status_code=502,
            )
        raw_payload = dict(data) if isinstance(data, dict) else {"result": result}
        raw_payload["ultronlab_metadata"] = {
            **metadata,
            "provider_amount": str(provider_amount),
        }
        return CryptomusInvoice(
            provider_invoice_id=provider_invoice_id,
            order_id=order_id,
            payment_url=payment_url,
            raw_payload=raw_payload,
        )

    def verify_webhook_payload(self, payload: dict[str, object]) -> bool:
        sign = payload.get("sign")
        if not isinstance(sign, str) or not sign:
            return False
        unsigned_payload = {key: value for key, value in payload.items() if key != "sign"}
        expected_signatures = {
            self._sign_json_payload(unsigned_payload, escape_slashes=False),
            self._sign_json_payload(unsigned_payload, escape_slashes=True),
        }
        return any(hmac.compare_digest(expected, sign) for expected in expected_signatures)

    def _ensure_configured(self) -> None:
        if (
            not self._settings.cryptomus_merchant_id.strip()
            or self._settings.cryptomus_merchant_id == "change_me"
            or not self._settings.cryptomus_api_key.strip()
            or self._settings.cryptomus_api_key == "change_me"
        ):
            raise AppError("Cryptomus is not configured", code="cryptomus_not_configured")

    def _headers(self, body: bytes) -> dict[str, str]:
        return {
            "merchant": self._settings.cryptomus_merchant_id,
            "sign": self._sign_bytes(body),
            "Content-Type": "application/json",
        }

    def _sign_json_payload(self, payload: dict[str, object], *, escape_slashes: bool) -> str:
        body = _json_bytes(payload)
        if escape_slashes:
            body = body.replace(b"/", b"\\/")
        return self._sign_bytes(body)

    def _sign_bytes(self, body: bytes) -> str:
        encoded_body = base64.b64encode(body).decode()
        payload = f"{encoded_body}{self._settings.cryptomus_api_key}".encode()
        return hashlib.md5(payload).hexdigest()  # noqa: S324 - Cryptomus API requires MD5.


def _json_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
