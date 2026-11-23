"""Budget wallet for rented GPUs, kept in our own cost ledger: spent = this month's cost_entry rows of the provider. A provider
with no cap in config (or 0) is blocked. Amounts are USD; the ledger stores INR, converted by fx. Port of GpuWallet."""

from __future__ import annotations

from collections.abc import Callable

from studio.agents.jfmt import fixed
from studio.db import Db
from studio.gpu.broker.config import BrokerConfig
from studio.ledger import Pricing

DAILY_CAP = "daily GPU cap"  # a daily-cap refusal starts with this text (the broker turns it into the Short's message)


class GpuWallet:
    def __init__(self, config: BrokerConfig, spent_usd_this_month: Callable[[str], float],
                 spent_usd_today: Callable[[], float] = lambda: 0.0):
        self._config, self._month, self._today = config, spent_usd_this_month, spent_usd_today

    @staticmethod
    def from_db(config: BrokerConfig, db: Db, pricing: Pricing) -> GpuWallet:
        def month(provider: str) -> float:
            row = db.rows(
                "SELECT COALESCE(sum(amount_inr), 0) AS s FROM cost_entry WHERE provider_id = %(p)s AND ts >= date_trunc('month', now())",
                {"p": provider},
            )[0]
            return float(row["s"]) / pricing.fx()

        def today() -> float:
            # Every GPU the studio paid for today (UTC): the broker's rented pods and Modal's GPU seconds.
            ids = list(dict.fromkeys([*config.order, *config.monthly_cap_usd])) or ["-"]
            row = db.rows(
                """
                SELECT COALESCE(sum(amount_inr), 0) AS s FROM cost_entry
                WHERE (provider_id = ANY(%(ps)s) OR provider_id LIKE 'modal%%')
                  AND ts >= (date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC')
                """,
                {"ps": ids},
            )[0]
            return float(row["s"]) / pricing.fx()

        return GpuWallet(config, month, today)

    def spent_today_usd(self) -> float:
        """Booked GPU spend today (UTC), USD."""
        return self._today()

    def daily_cap_usd(self) -> float:
        return self._config.daily_cap_usd

    def daily_refusal(self, estimate_usd: float, running_usd: float) -> str | None:
        """None when a rental of about estimate_usd fits under today's cap, else the reason. running_usd: what the pod that is
        on now has cost so far (not booked yet)."""
        cap = self._config.daily_cap_usd
        if cap <= 0:
            return None
        spent = self.spent_today_usd() + max(0.0, running_usd)
        if spent + estimate_usd > cap:
            return (f"{DAILY_CAP}: ${fixed(spent, 2)} spent today (UTC) of the ${fixed(cap, 2)} cap, "
                    f"this rental needs about ${fixed(estimate_usd, 2)}")
        return None

    def remaining_usd(self, provider: str) -> float:
        left = self._config.cap_usd(provider) - self._month(provider)
        if self._config.total_cap_usd > 0:
            total_spent = sum(self._month(p) for p in self._config.monthly_cap_usd)
            left = min(left, self._config.total_cap_usd - total_spent)
        return left

    def refusal(self, provider: str, estimate_usd: float) -> str | None:
        """None when the estimate fits, else the reason (shown in the routing decision)."""
        cap = self._config.cap_usd(provider)
        if cap <= 0:
            return f"blocked: no monthly cap set (pricing.gpuBroker.providers.{provider}.monthlyCapUsd is 0)"
        left = self.remaining_usd(provider)
        if left < estimate_usd:
            return f"wallet: ${fixed(max(0.0, left), 2)} left of the ${fixed(cap, 2)} cap, this rental needs about ${fixed(estimate_usd, 2)}"
        return self.daily_refusal(estimate_usd, 0.0)  # a new rental only starts when no pod is on, so nothing runs unbooked
