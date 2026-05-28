from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from shared.app.config import Settings, get_settings
from shared.app.enums import VideoQuality


class PricingService:
    """Central place for generation pricing rules."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def estimate_generation_price(
        self,
        duration_seconds: Decimal,
        *,
        quality_profile: str = VideoQuality.P480.value,
    ) -> Decimal:
        return self.calculate_job_price(duration_seconds, quality_profile=quality_profile)

    def calculate_job_price(
        self,
        duration_seconds: Decimal,
        *,
        quality_profile: str = VideoQuality.P480.value,
    ) -> Decimal:
        profile = self.normalize_quality_profile(quality_profile)
        whole_seconds = max(
            int(duration_seconds.to_integral_value(rounding=ROUND_CEILING)),
            0,
        )
        if profile == VideoQuality.P720.value:
            min_seconds = max(self._settings.video_720_min_duration_seconds, 0)
            min_price = self._settings.video_720_min_price_usd
            extra_second_price = self._settings.video_720_price_per_extra_second_usd
        else:
            min_seconds = max(self._settings.video_480_min_duration_seconds, 0)
            min_price = self._settings.video_480_min_price_usd
            extra_second_price = self._settings.video_480_price_per_extra_second_usd

        if whole_seconds <= min_seconds:
            price = min_price
        else:
            price = min_price + Decimal(whole_seconds - min_seconds) * extra_second_price
        return price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    def calculate_segment_price(
        self,
        duration_seconds: Decimal,
        *,
        quality_profile: str = VideoQuality.P480.value,
    ) -> Decimal:
        profile = self.normalize_quality_profile(quality_profile)
        if profile == VideoQuality.P720.value:
            per_second = self._settings.video_720_price_per_extra_second_usd
        else:
            per_second = self._settings.video_480_price_per_extra_second_usd
        raw_price = duration_seconds * per_second
        return raw_price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    def normalize_quality_profile(self, quality_profile: str | None) -> str:
        value = (quality_profile or VideoQuality.P480.value).strip().lower()
        if value in {VideoQuality.P480.value, VideoQuality.P720.value}:
            return value
        return VideoQuality.P480.value
