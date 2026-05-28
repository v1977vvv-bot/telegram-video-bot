from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace

from backend.app.services.pricing import PricingService


class VideoQualityPricingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = PricingService(
            SimpleNamespace(
                video_480_min_duration_seconds=5,
                video_480_min_price_usd=Decimal("0.15"),
                video_480_price_per_extra_second_usd=Decimal("0.015"),
                video_720_min_duration_seconds=5,
                video_720_min_price_usd=Decimal("0.30"),
                video_720_price_per_extra_second_usd=Decimal("0.030"),
            )
        )

    def test_480p_pricing(self) -> None:
        cases = {
            "1": Decimal("0.1500"),
            "5": Decimal("0.1500"),
            "6": Decimal("0.1650"),
            "7": Decimal("0.1800"),
            "60": Decimal("0.9750"),
        }
        for duration, expected in cases.items():
            with self.subTest(duration=duration):
                self.assertEqual(
                    self.service.calculate_job_price(
                        Decimal(duration),
                        quality_profile="480p",
                    ),
                    expected,
                )

    def test_720p_pricing(self) -> None:
        cases = {
            "1": Decimal("0.3000"),
            "5": Decimal("0.3000"),
            "6": Decimal("0.3300"),
            "7": Decimal("0.3600"),
            "60": Decimal("1.9500"),
        }
        for duration, expected in cases.items():
            with self.subTest(duration=duration):
                self.assertEqual(
                    self.service.calculate_job_price(
                        Decimal(duration),
                        quality_profile="720p",
                    ),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
