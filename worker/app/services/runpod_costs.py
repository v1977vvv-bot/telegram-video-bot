from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from shared.app.config import Settings, get_settings

MONEY_QUANT = Decimal("0.0001")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunPodCostEstimate:
    cloud_type: str | None
    gpu_type: str | None
    runtime_seconds: int
    billable_seconds: int
    hourly_cost_usd: Decimal
    runtime_cost_usd: Decimal
    startup_surcharge_usd: Decimal
    total_cost_usd: Decimal


class RunPodCostService:
    """Estimated RunPod infrastructure cost calculator.

    This does not call RunPod billing APIs. It uses configured hourly prices and
    local runtime intervals only.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def get_gpu_hourly_cost(self, gpu_type: str | None) -> Decimal:
        if gpu_type:
            configured = self.parse_gpu_hourly_costs().get(gpu_type.strip())
            if configured is not None:
                return configured
        return self._settings.runpod_default_hourly_cost_usd

    def get_cloud_gpu_hourly_cost(
        self,
        *,
        cloud_type: str | None,
        gpu_type: str | None,
    ) -> Decimal:
        normalized_cloud = (cloud_type or "").strip().upper()
        if normalized_cloud == "SECURE":
            configured = self._settings.runpod_secure_gpu_price_per_hour
            if configured is not None:
                return configured
        elif normalized_cloud == "COMMUNITY":
            configured = self._settings.runpod_community_gpu_price_per_hour
            if configured is not None:
                return configured

        logger.warning(
            "RunPod cloud-specific pricing is not configured cloud_type=%s gpu_type=%s",
            cloud_type,
            gpu_type,
        )
        return self.get_gpu_hourly_cost(gpu_type)

    def get_startup_surcharge(self, *, cloud_type: str | None) -> Decimal:
        normalized_cloud = (cloud_type or "").strip().upper()
        if normalized_cloud == "COMMUNITY":
            return self._settings.runpod_community_cold_start_surcharge
        if normalized_cloud == "SECURE":
            return self._settings.runpod_secure_startup_surcharge
        return Decimal("0")

    def parse_gpu_hourly_costs(self) -> dict[str, Decimal]:
        return self._settings.runpod_gpu_hourly_costs_map

    def calculate_runpod_cost_usd(
        self,
        *,
        gpu_type: str | None,
        started_at: datetime,
        ended_at: datetime,
        min_billing_seconds: int | None = None,
        cloud_type: str | None = None,
        include_startup_surcharge: bool = True,
    ) -> Decimal:
        estimate = self.estimate_runpod_cost_usd(
            cloud_type=cloud_type,
            gpu_type=gpu_type,
            started_at=started_at,
            ended_at=ended_at,
            min_billing_seconds=min_billing_seconds,
            include_startup_surcharge=include_startup_surcharge,
        )
        return estimate.total_cost_usd

    def estimate_runpod_cost_usd(
        self,
        *,
        cloud_type: str | None,
        gpu_type: str | None,
        started_at: datetime,
        ended_at: datetime,
        min_billing_seconds: int | None = None,
        include_startup_surcharge: bool = True,
    ) -> RunPodCostEstimate:
        runtime_seconds = self.runtime_seconds(started_at=started_at, ended_at=ended_at)
        configured_min_billing_seconds = (
            self._settings.runpod_cost_min_billing_seconds
            if min_billing_seconds is None
            else min_billing_seconds
        )
        billable_seconds = max(runtime_seconds, max(configured_min_billing_seconds, 0))
        hourly_cost = self.get_cloud_gpu_hourly_cost(cloud_type=cloud_type, gpu_type=gpu_type)
        runtime_cost = (hourly_cost * Decimal(billable_seconds) / Decimal("3600")).quantize(
            MONEY_QUANT,
            rounding=ROUND_HALF_UP,
        )
        startup_surcharge = (
            self.get_startup_surcharge(cloud_type=cloud_type)
            if include_startup_surcharge
            else Decimal("0")
        ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        total_cost = (runtime_cost + startup_surcharge).quantize(
            MONEY_QUANT,
            rounding=ROUND_HALF_UP,
        )
        return RunPodCostEstimate(
            cloud_type=cloud_type,
            gpu_type=gpu_type,
            runtime_seconds=runtime_seconds,
            billable_seconds=billable_seconds,
            hourly_cost_usd=hourly_cost.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP),
            runtime_cost_usd=runtime_cost,
            startup_surcharge_usd=startup_surcharge,
            total_cost_usd=total_cost,
        )

    def runtime_seconds(self, *, started_at: datetime, ended_at: datetime) -> int:
        runtime = max((ended_at - started_at).total_seconds(), 0)
        mode = self._settings.runpod_cost_rounding_mode.strip().lower()
        if mode != "up_to_second":
            mode = "up_to_second"
        if mode == "up_to_second":
            whole_seconds = int(runtime)
            return whole_seconds if runtime == whole_seconds else whole_seconds + 1
        whole_seconds = int(runtime)
        return whole_seconds if runtime == whole_seconds else whole_seconds + 1


def calculate_gross_margin(
    *,
    price_usd: Decimal | None,
    cost_usd: Decimal | None,
) -> tuple[Decimal | None, Decimal | None]:
    if price_usd is None or cost_usd is None:
        return None, None
    margin = (price_usd - cost_usd).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    if price_usd <= Decimal("0"):
        return margin, None
    percent = (margin / price_usd * Decimal("100")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return margin, percent
