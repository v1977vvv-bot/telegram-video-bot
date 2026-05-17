from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from shared.app.config import Settings, get_settings

MONEY_QUANT = Decimal("0.0001")


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

    def parse_gpu_hourly_costs(self) -> dict[str, Decimal]:
        return self._settings.runpod_gpu_hourly_costs_map

    def calculate_runpod_cost_usd(
        self,
        *,
        gpu_type: str | None,
        started_at: datetime,
        ended_at: datetime,
        min_billing_seconds: int | None = None,
    ) -> Decimal:
        runtime_seconds = self.runtime_seconds(started_at=started_at, ended_at=ended_at)
        configured_min_billing_seconds = (
            self._settings.runpod_cost_min_billing_seconds
            if min_billing_seconds is None
            else min_billing_seconds
        )
        billable_seconds = max(runtime_seconds, max(configured_min_billing_seconds, 0))
        hourly_cost = self.get_gpu_hourly_cost(gpu_type)
        cost = hourly_cost * Decimal(billable_seconds) / Decimal("3600")
        return cost.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

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
