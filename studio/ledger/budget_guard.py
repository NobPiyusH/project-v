"""Hard monthly caps on paid spend (HANDOVER §11.5). Google Cloud budgets only alert; this actually stops. Port of BudgetGuard.

The month is the calendar month in UTC.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from studio.db import Db
from studio.ledger.pricing import Pricing

GEMINI = "gemini_api"
CLAUDE = "anthropic"
AICREDITS = "aicredits"
GPU_PREFIX = "modal_"


class BudgetExceededError(RuntimeError):
    """A budget refusal. Workflows must not retry it (list its name in the activity's non-retryable types): the stage waits for the owner."""


# Name for Temporal's non-retryable list (Java used the exception class name).
BUDGET_ERROR = BudgetExceededError.__name__


def _whole(x: Decimal) -> Decimal:
    return x.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


class BudgetGuard:
    def __init__(self, db: Db, pricing: Pricing):
        self._db = db
        self._pricing = pricing

    def _spent_this_month(self, prefix: str) -> Decimal:
        row = self._db.rows(
            """
            SELECT COALESCE(sum(amount_inr), 0) AS spent FROM cost_entry
            WHERE starts_with(provider_id, %(p)s)
              AND ts >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
            """,
            {"p": prefix},
        )[0]
        return Decimal(row["spent"])

    # ---- Gemini

    def gemini_spent_this_month(self) -> Decimal:
        return self._spent_this_month(GEMINI)

    def gemini_remaining(self) -> Decimal:
        return self._pricing.gemini_monthly_cap_inr() - self.gemini_spent_this_month()

    def can_spend(self, estimate_inr: Decimal) -> bool:
        return self.gemini_remaining() >= estimate_inr

    def require(self, estimate_inr: Decimal, what: str) -> None:
        if not self.can_spend(estimate_inr):
            raise BudgetExceededError(
                f"Monthly Gemini cap of ₹{self._pricing.gemini_monthly_cap_inr()} would be exceeded by {what} "
                f"(≈₹{estimate_inr}; ₹{max(self.gemini_remaining(), Decimal(0))} left). "
                "Raise the cap in config/providers.yaml or switch this stage to Manual."
            )

    # ---- Claude: owner-funded, its own monthly cap; over it, callers fall back to Gemini

    def claude_spent_this_month(self) -> Decimal:
        return self._spent_this_month(CLAUDE)

    def can_spend_claude(self, estimate_inr: Decimal) -> bool:
        return self._pricing.claude_monthly_cap_inr() - self.claude_spent_this_month() >= estimate_inr

    # ---- AICredits: errors-only text fallback with its own all-time cap (the prepaid wallet)

    def aicredits_spent(self) -> Decimal:
        row = self._db.rows(
            "SELECT COALESCE(sum(amount_inr), 0) AS spent FROM cost_entry WHERE provider_id = %(p)s", {"p": AICREDITS}
        )[0]
        return Decimal(row["spent"])

    def can_spend_aicredits(self, estimate_inr: Decimal) -> bool:
        return self._pricing.aicredits_cap_inr() - self.aicredits_spent() >= estimate_inr

    # ---- GPU (Modal): its own monthly cap, the free credit by default (owner rule: ask before spending money)

    def gpu_spent_this_month(self) -> Decimal:
        return self._spent_this_month(GPU_PREFIX)

    def gpu_remaining(self) -> Decimal:
        return self._pricing.gpu_monthly_cap_inr() - self.gpu_spent_this_month()

    def require_gpu(self, estimate_inr: Decimal, what: str) -> None:
        remaining = self.gpu_remaining()
        if remaining < estimate_inr:
            raise BudgetExceededError(
                f"Monthly GPU cap of ₹{_whole(self._pricing.gpu_monthly_cap_inr())} would be exceeded by {what} "
                f"(≈₹{_whole(estimate_inr)}; ₹{_whole(max(remaining, Decimal(0)))} left). "
                "Raise modal.monthlyCapUsd in config/providers.yaml to allow paid GPU time."
            )

    def remaining_share(self, bucket: str) -> float:
        """Share of a budget bucket (gemini | gpu | claude) still left this month, 0..1; 1 for unbudgeted buckets."""
        if bucket == "gemini":
            cap, left = self._pricing.gemini_monthly_cap_inr(), self.gemini_remaining()
        elif bucket == "gpu":
            cap, left = self._pricing.gpu_monthly_cap_inr(), self.gpu_remaining()
        elif bucket == "claude":
            cap = self._pricing.claude_monthly_cap_inr()
            left = cap - self.claude_spent_this_month()
        else:
            return 1.0
        if cap <= 0:
            return 0.0
        return max(0.0, min(1.0, float(left) / float(cap)))
