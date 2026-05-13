from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from shared.app.config import Settings, get_settings


class PricingService:
    """Central place for generation pricing rules."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def estimate_generation_price(self, duration_seconds: Decimal) -> Decimal:
        return self.calculate_job_price(duration_seconds)

    def calculate_job_price(self, duration_seconds: Decimal) -> Decimal:
        raw_price = duration_seconds * self._settings.pricing_price_per_second_usd
        price = max(raw_price, self._settings.pricing_min_job_price_usd)
        return price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    def calculate_segment_price(self, duration_seconds: Decimal) -> Decimal:
        raw_price = duration_seconds * self._settings.pricing_price_per_second_usd
        return raw_price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
