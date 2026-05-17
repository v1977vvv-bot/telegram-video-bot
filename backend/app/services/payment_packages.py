from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from shared.app.config import Settings, get_settings
from shared.app.exceptions import AppError

MONEY_2 = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class PaymentPackage:
    amount_usd: Decimal
    display_label: str
    provider_currency: str
    provider_amount: Decimal


class PaymentPackageService:
    """Fixed top-up package policy for MVP payments."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def get_payment_packages(self) -> list[PaymentPackage]:
        self.validate_settings()
        return [
            PaymentPackage(
                amount_usd=amount,
                display_label=self.format_package_label(amount),
                provider_currency=self._settings.payment_provider_currency,
                provider_amount=self.provider_amount_for_usd(amount),
            )
            for amount in self._settings.payment_package_amounts_usd
        ]

    def validate_package_amount(self, amount_usd: Decimal) -> Decimal:
        self.validate_settings()
        amount = amount_usd.quantize(MONEY_2)
        if amount not in self._settings.payment_package_amounts_usd:
            allowed = ", ".join(
                self.format_package_label(item) for item in self.get_package_amounts()
            )
            raise AppError(
                f"Сейчас доступны только фиксированные пакеты пополнения: {allowed}.",
                code="payment_package_not_allowed",
                status_code=400,
            )
        return amount

    def get_package_amounts(self) -> list[Decimal]:
        return self._settings.payment_package_amounts_usd

    def format_package_label(self, amount_usd: Decimal) -> str:
        amount = amount_usd.quantize(MONEY_2)
        if amount == amount.to_integral_value():
            return f"${int(amount)}"
        return f"${amount}"

    def provider_amount_for_usd(self, amount_usd: Decimal) -> Decimal:
        return (amount_usd * self._settings.payment_usd_usdt_rate).quantize(MONEY_2)

    def validate_settings(self) -> None:
        if not self._settings.payment_packages_enabled:
            raise AppError("Payment packages are disabled", code="payment_packages_disabled")
        if self._settings.payment_custom_amount_enabled:
            raise AppError(
                "Custom top-up amounts are disabled for MVP",
                code="custom_payment_amount_disabled",
            )
        if self._settings.payment_display_currency.upper() != "USD":
            raise AppError("PAYMENT_DISPLAY_CURRENCY must be USD", code="payment_config_invalid")
        if self._settings.payment_provider_currency.upper() != "USDT":
            raise AppError(
                "PAYMENT_PROVIDER_CURRENCY must be USDT",
                code="payment_config_invalid",
            )
        if self._settings.payment_usd_usdt_rate != Decimal("1"):
            raise AppError("PAYMENT_USD_USDT_RATE must be 1", code="payment_config_invalid")
        _ = self._settings.payment_package_amounts_usd
