"""Hard monthly caps on paid spend (HANDOVER §11.5). Google Cloud budgets only alert; this actually stops. Port of BudgetGuard.

The month is the calendar month in UTC.
"""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from studio.db import Db
from studio.ledger.pricing import Pricing

GEMINI = "gemini_api"
CLAUDE = "anthropic"
AICREDITS = "aicredits"
GPU_PREFIX = "modal_"


class BudgetExceededError(RuntimeError):
    """A budget refusal. Workflows must not retry it (list its name in the activity's non-retryable types): the stage waits for the owner."""


class VideoBudgetExceededError(BudgetExceededError):
    """The per-video cap refused a paid call or a pod rental. A subclass, so every handler that holds a stage for the owner on a budget
    stop holds it for this too; callers that would fall back to another paid route (AICredits) must not, since that spends more on the
    same video."""


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

    def require(self, estimate_inr: Decimal, what: str, video_id: uuid.UUID | None = None) -> None:
        """Refuses a Gemini-billed job over the monthly cap and, when video_id is given, over that video's own cap."""
        if video_id is not None:
            self.require_video(video_id, estimate_inr, what)
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

    def require_gpu(self, estimate_inr: Decimal, what: str, video_id: uuid.UUID | None = None) -> None:
        if video_id is not None:
            self.require_video(video_id, estimate_inr, what)
        remaining = self.gpu_remaining()
        if remaining < estimate_inr:
            raise BudgetExceededError(
                f"Monthly GPU cap of ₹{_whole(self._pricing.gpu_monthly_cap_inr())} would be exceeded by {what} "
                f"(≈₹{_whole(estimate_inr)}; ₹{_whole(max(remaining, Decimal(0)))} left). "
                "Raise modal.monthlyCapUsd in config/providers.yaml to allow paid GPU time."
            )

    # ---- one video's own cap (owner, 25 Nov 2026). The id is a Video or a Dream Short: both book their cost against it.

    def video_cap_inr(self, video_id: uuid.UUID) -> Decimal:
        """This video's cap: its own budget_inr when set, else pricing.perVideo.defaultCapInr. 0 = no cap."""
        for table in ("video", "dream_short"):
            rows = self._db.rows(f"SELECT budget_inr FROM {table} WHERE id = %(v)s", {"v": video_id})  # noqa: S608 - fixed table names
            if rows:
                own = rows[0]["budget_inr"]
                return Decimal(own) if own is not None else self._pricing.video_default_cap_inr()
        return self._pricing.video_default_cap_inr()

    def video_spent_inr(self, video_id: uuid.UUID) -> Decimal:
        """Everything booked on the video, failed attempts and aborted pods included (they are CostEntry rows like any other)."""
        row = self._db.rows(
            "SELECT COALESCE(sum(amount_inr), 0) AS spent FROM cost_entry WHERE video_id = %(v)s OR dream_short_id = %(v)s", {"v": video_id}
        )[0]
        return Decimal(row["spent"])

    def video_budget(self, video_id: uuid.UUID, reserved_inr: Decimal = Decimal(0)) -> dict[str, Any]:
        """The video's budget for its API read model. remainingInr is None when the video has no cap."""
        cap, spent = self.video_cap_inr(video_id), self.video_spent_inr(video_id)
        return {"capInr": cap if cap > 0 else None, "spentInr": spent, "reservedInr": reserved_inr,
                "remainingInr": max(Decimal(0), cap - spent - reserved_inr) if cap > 0 else None}

    def video_refusal(self, video_id: uuid.UUID, estimate_inr: Decimal, what: str, reserved_inr: Decimal = Decimal(0),
                      strict: bool = False) -> str | None:
        """None when spent + reserved + estimate fits the video's cap, else the reason. A free job (estimate 0) is never refused,
        unless strict: then a video already at its cap is refused whatever the estimate (more time on a pod it is already using)."""
        cap = self.video_cap_inr(video_id)
        if cap <= 0 or (estimate_inr <= 0 and not strict):
            return None
        spent = self.video_spent_inr(video_id)
        if spent + reserved_inr + estimate_inr <= cap:
            return None
        reserved = f" + ₹{_whole(reserved_inr)} reserved" if reserved_inr > 0 else ""
        return (f"Video cap of ₹{_whole(cap)} would be exceeded by {what} (≈₹{_whole(estimate_inr)}; ₹{_whole(spent)} spent{reserved}, "
                f"₹{_whole(max(Decimal(0), cap - spent - reserved_inr))} left). The stage is on hold: raise this video's budget, "
                "then Retry, or Continue without it.")

    def require_video(self, video_id: uuid.UUID, estimate_inr: Decimal, what: str, reserved_inr: Decimal = Decimal(0)) -> None:
        why = self.video_refusal(video_id, estimate_inr, what, reserved_inr)
        if why is not None:
            raise VideoBudgetExceededError(why)

    def set_video_budget(self, video_id: uuid.UUID, budget_inr: Decimal | None) -> bool:
        """Sets (or with None clears back to the default) a video's own cap. True when a Video or Dream Short with that id exists."""
        for table in ("video", "dream_short"):
            rows = self._db.rows(
                f"UPDATE {table} SET budget_inr = %(b)s WHERE id = %(v)s RETURNING id", {"b": budget_inr, "v": video_id}  # noqa: S608
            )
            if rows:
                return True
        return False

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
