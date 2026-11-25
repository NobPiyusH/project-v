"""M3a router core (docs/plans/M3-router.md): ONE place that decides which provider runs a paid job, and why. Port of Router.

1. candidates come from the stage (each with its own cost estimate for this job);
2. filter: in the registry, enabled, has the capability, not cooling down after failures, fits its budget bucket;
3. choose: the owner's preferred provider when eligible; otherwise only providers reaching the job's quality floor
   compete, scored min(quality, enough) - lambda x cost (lambda grows as that provider's budget runs low); when none
   reaches the floor, the best-scoring one is a noted fallback;
4. none eligible: the job is deferred with a non-retryable budget failure, so the stage waits for the owner;
5. every decision is logged in routing_decision (CLAUDE.md rule 6).

Runs inside activities only (workflow determinism).
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from studio.db import Db
from studio.ledger import BudgetExceededError, BudgetGuard, Pricing
from studio.provider.caps import ProviderCaps

MAX_COOLDOWN_SEC = 30 * 60


@dataclass
class Candidate:
    provider_id: str
    estimate_inr: Decimal


@dataclass
class Choice:
    provider_id: str
    reason: str
    decision_id: uuid.UUID | None  # None when not logged (an estimate preview)
    considered: list[dict[str, Any]]


def _round_half_up(x: float, places: int) -> float:
    """Java Math.round semantics (half up), not Python's banker's rounding."""
    f = 10**places
    return math.floor(x * f + 0.5) / f


def _int_of(o: Any, default: int) -> int:
    if o is None:
        return default
    try:
        return int(str(o).strip())
    except ValueError:
        return default


def _scale(x: Decimal, places: int) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(type(o))


class Router:
    def __init__(self, db: Db, budget: BudgetGuard, pricing: Pricing, caps: ProviderCaps | None = None):
        self._caps = caps  # per-provider spend limits (config providerCaps); None = not checked
        self._db = db
        self._budget = budget
        self._pricing = pricing

    def score(self, quality: int, cost_inr: Decimal, bucket: str, enough: float) -> float:
        """min(quality, enough) - lambda x cost; lambda = lambdaPerInr / max(0.1, share of the budget left)."""
        lam = self._pricing.routing_lambda_per_inr() / max(0.1, self._budget.remaining_share(bucket))
        return min(quality, enough) - lam * float(cost_inr)

    def choose(
        self,
        video_id: uuid.UUID | None,
        stage: str,
        capability: str,
        candidates: list[Candidate],
        preferred: str | None,
        log: bool,
        scope_id: uuid.UUID | None = None,
    ) -> Choice:
        considered: list[dict[str, Any]] = []
        floor, enough = self._pricing.routing_quality(capability)
        best = fallback = pref = None
        best_score = fallback_score = -math.inf
        for c in candidates:
            row: dict[str, Any] = {"provider": c.provider_id, "estimateInr": _scale(c.estimate_inr, 2)}
            why = self.ineligible(c, capability, stage=stage, scope_id=scope_id or video_id, video_id=video_id)
            p = self._provider(c.provider_id)
            quality = 0 if p is None else _int_of(p["quality"], 5)
            row["quality"] = quality
            row["eligible"] = why is None
            if why is not None:
                row["why"] = why
            else:
                if c.provider_id == preferred:
                    pref = c
                s = self.score(quality, c.estimate_inr, str(p["budget"]), enough)
                row["score"] = _round_half_up(s, 2)
                if quality >= floor:
                    if s > best_score:
                        best, best_score = c, s
                else:
                    row["belowFloor"] = floor
                    if s > fallback_score:
                        fallback, fallback_score = c, s
            considered.append(row)
        chosen = pref or best or fallback
        if chosen is None:
            reason = f"No provider can run {capability} now: " + "; ".join(f"{r['provider']} ({r['why']})" for r in considered)
        elif chosen is pref:
            reason = f"{chosen.provider_id}: the owner's preference, and it fits its budget"
        else:
            reason = chosen.provider_id + (
                f": best score at or above the quality floor ({int(floor)})"
                if chosen is best
                else f": fallback BELOW the quality floor ({int(floor)}), nothing better fits"
            )
            if preferred is not None:
                why_not = next((str(r.get("why")) for r in considered if r["provider"] == preferred), "not a candidate")
                reason += f" (preferred {preferred} is not available: {why_not})"
        decision_id = self._log(video_id, stage, capability, considered, chosen, reason) if log else None
        if chosen is None:
            # deferred: the same non-retryable failure as a budget stop, so the stage waits for the owner
            raise BudgetExceededError(f"{reason}. Raise a cap, wait for a cooldown, or switch this stage to Manual.")
        return Choice(chosen.provider_id, reason, decision_id, considered)

    def ineligible(
        self, c: Candidate, capability: str, stage: str | None = None, scope_id: uuid.UUID | None = None, video_id: uuid.UUID | None = None
    ) -> str | None:
        """Why this candidate cannot run the job now, or None when it can. video_id: the video that would pay for it (its own cap counts)."""
        p = self._provider(c.provider_id)
        if p is None:
            return "not in the provider registry"
        if p["enabled"] is not True:
            return "disabled"
        if p["owner_disabled"] is True:
            return "switched off by the owner"
        if capability not in json.loads(p["capabilities"]):
            return f"can't do {capability}"
        cooling = self.cooldown_until(c.provider_id)
        if cooling is not None and cooling > datetime.now(UTC):
            return f"cooling down after failures until {cooling.isoformat().replace('+00:00', 'Z')}"
        bucket = str(p["budget"])
        if bucket == "gemini":
            fits = self._budget.can_spend(c.estimate_inr)
        elif bucket == "gpu":
            fits = self._budget.gpu_remaining() >= c.estimate_inr
        elif bucket == "claude":
            fits = self._budget.can_spend_claude(c.estimate_inr)
        else:
            fits = True  # free / local / manual
        if not fits:
            return f"≈₹{_scale(c.estimate_inr, 0)} would break the monthly {bucket} cap"
        if self._caps is not None:  # the provider's own daily / per-video limit (providerCaps)
            why = self._caps.refusal(c.provider_id, c.estimate_inr, stage=stage, scope_id=scope_id)
            if why is not None:
                return why
        if video_id is not None:  # a free / local / manual route estimates ₹0, which the video cap never refuses
            return self._budget.video_refusal(video_id, c.estimate_inr, f"{capability} on {c.provider_id}")
        return None

    # ---- health

    def report_failure(self, provider_id: str, error: str | None) -> None:
        """A provider failed (not the job's own fault): cool it down, doubling up to 30 min."""
        fails = self._db.rows(
            """
            INSERT INTO provider_health (provider_id, fails, last_error) VALUES (%(p)s, 1, %(e)s)
            ON CONFLICT (provider_id) DO UPDATE SET fails = provider_health.fails + 1, last_error = %(e)s, updated_at = now()
            RETURNING fails
            """,
            {"p": provider_id, "e": None if error is None else error[:500]},
        )[0]["fails"]
        sec = min(MAX_COOLDOWN_SEC, 60 << min(fails - 1, 10))
        self._db.execute(
            "UPDATE provider_health SET cooldown_until = %(u)s WHERE provider_id = %(p)s",
            {"u": datetime.now(UTC) + timedelta(seconds=sec), "p": provider_id},
        )

    def report_batch(self, provider_id: str, ok: int, failed: int, sample_error: str | None) -> None:
        """A finished batch: any success clears the cooldown; a batch where EVERY job failed cools it down.

        Each statement commits on its own (autocommit), so it is kept even when the caller then fails and rolls back.
        """
        if ok > 0:
            self.report_success(provider_id)
        elif failed > 0:
            self.report_failure(provider_id, sample_error)

    def report_success(self, provider_id: str) -> None:
        self._db.execute(
            "UPDATE provider_health SET fails = 0, cooldown_until = NULL, updated_at = now() "
            "WHERE provider_id = %(p)s AND fails > 0",
            {"p": provider_id},
        )

    def cooldown_until(self, provider_id: str) -> datetime | None:
        rows = self._db.rows("SELECT cooldown_until FROM provider_health WHERE provider_id = %(p)s", {"p": provider_id})
        return rows[0]["cooldown_until"] if rows else None

    # ---- owner controls (M3b)

    def set_owner_disabled(self, provider_id: str, off: bool) -> None:
        rows = self._db.rows(
            "UPDATE provider SET owner_disabled = %(off)s, updated_at = now() WHERE id = %(p)s RETURNING id",
            {"off": off, "p": provider_id},
        )
        if not rows:
            raise ValueError(f"Unknown provider {provider_id}")

    def reset_health(self, provider_id: str) -> None:
        self._db.execute("DELETE FROM provider_health WHERE provider_id = %(p)s", {"p": provider_id})

    def providers(self) -> list[dict[str, Any]]:
        """The router's providers (those with a quality tier) with health and this month's decision counts."""
        return self._db.rows(
            """
            SELECT p.id, p.name, p.kind, p.enabled, p.owner_disabled, p.capabilities_json::text AS capabilities,
                   (p.config_json->>'quality')::int AS quality, COALESCE(p.config_json->>'budget', 'none') AS budget,
                   h.fails, h.cooldown_until, h.last_error,
                   (SELECT count(*) FROM routing_decision d WHERE d.chosen_provider_id = p.id
                      AND d.created_at >= date_trunc('month', now())) AS chosen_this_month
            FROM provider p LEFT JOIN provider_health h ON h.provider_id = p.id
            WHERE p.config_json->>'quality' IS NOT NULL ORDER BY p.config_json->>'budget', p.id
            """
        )

    def budgets(self) -> list[dict[str, Any]]:
        """Each budget bucket the router scores against: share left (what makes lambda grow)."""
        out = []
        for b in ("gemini", "gpu", "claude"):
            share = self._budget.remaining_share(b)
            lam = self._pricing.routing_lambda_per_inr() / max(0.1, share)
            out.append({"bucket": b, "shareLeft": _round_half_up(share, 3), "lambda": _round_half_up(lam, 4)})
        return out

    def decisions(self, video_id: uuid.UUID) -> list[dict[str, Any]]:
        """Decisions of one video, newest first (for the dashboard's 'why this provider?')."""
        return self._db.rows(
            """
            SELECT id, stage, capability, chosen_provider_id, reason_text, candidates_json::text AS candidates,
                   overridden_by_owner, created_at
            FROM routing_decision WHERE video_id = %(v)s ORDER BY created_at DESC
            """,
            {"v": video_id},
        )

    def record_external(
        self, video_id: uuid.UUID | None, stage: str, capability: str, chosen: str | None, reason: str,
        candidates: list[dict[str, Any]] | None,
    ) -> uuid.UUID:
        """A decision made elsewhere (the media worker: voice engine, laptop vs cloud smoothing), logged the same way."""
        return self._insert_decision(video_id, stage, capability, candidates or [], chosen, reason)

    # ---- internals

    def _provider(self, provider_id: str) -> dict[str, Any] | None:
        rows = self._db.rows(
            """
            SELECT enabled, owner_disabled, capabilities_json::text AS capabilities, config_json->>'quality' AS quality,
                   COALESCE(config_json->>'budget', 'none') AS budget
            FROM provider WHERE id = %(id)s
            """,
            {"id": provider_id},
        )
        return rows[0] if rows else None

    def _log(self, video_id, stage, capability, considered, chosen: Candidate | None, reason: str) -> uuid.UUID:
        return self._insert_decision(video_id, stage, capability, considered, chosen.provider_id if chosen else None, reason)

    def _insert_decision(self, video_id, stage, capability, candidates, chosen: str | None, reason: str) -> uuid.UUID:
        decision_id = uuid.uuid4()
        self._db.execute(
            """
            INSERT INTO routing_decision (id, video_id, stage, capability, candidates_json, chosen_provider_id, reason_text)
            VALUES (%(id)s, %(v)s, %(s)s, %(c)s, CAST(%(cands)s AS jsonb), %(chosen)s, %(r)s)
            """,
            {"id": decision_id, "v": video_id, "s": stage, "c": capability,
             "cands": json.dumps(candidates, default=_json_default), "chosen": chosen, "r": reason},
        )
        return decision_id
