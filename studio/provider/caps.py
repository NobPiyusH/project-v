"""Per-provider spend limits (`providerCaps` in config/providers.yaml).

The ledger keys cost_entry by the BILLING account (Lyria and Gemini images are both recorded as `gemini_api`), so a cap
names the ledger rows it counts: `ledgerProvider` plus optional `unitType` / `noteLike` filters. `check_provider_budget`
is the one function the router calls before a paid call; it raises ProviderCapError (a BudgetExceededError, so Temporal
does not retry it and the stage waits for the owner, rule 2b). The per-VIDEO cap on all spend lives in studio/ledger.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import yaml

from studio.ledger.budget_guard import BudgetExceededError

log = logging.getLogger("projectv.provider.caps")


class ProviderCapError(BudgetExceededError):
    """A provider's own cap would be broken; the stage holds for the owner."""


@dataclass(frozen=True)
class ProviderCap:
    provider: str
    ledger_provider: str
    daily_inr: Decimal | None = None       # None = no limit (free tiers: still logged)
    per_video_inr: Decimal | None = None
    unit_type: str | None = None
    note_like: tuple[str, ...] = ()
    stages: tuple[str, ...] = ()           # empty = any stage may use it
    idempotent: bool = False               # may a failed call be retried (provider has an idempotency key / call is free)
    free: bool = False

    @property
    def limited(self) -> bool:
        return self.daily_inr is not None or self.per_video_inr is not None


class SpendSource(Protocol):
    def spent(self, cap: ProviderCap, since: datetime | None, scope_id: uuid.UUID | None) -> Decimal:
        """Sum of the ledger rows this cap counts; since=None means all time; scope_id restricts to one video/Short."""


class DbSpend:
    def __init__(self, db: Any):
        self._db = db

    def spent(self, cap: ProviderCap, since: datetime | None, scope_id: uuid.UUID | None) -> Decimal:
        where, args = ["provider_id = %(p)s"], {"p": cap.ledger_provider}
        if cap.unit_type:
            where.append("unit_type = %(u)s")
            args["u"] = cap.unit_type
        if cap.note_like:
            where.append("(" + " OR ".join(f"note ILIKE %(n{i})s" for i in range(len(cap.note_like))) + ")")
            args.update({f"n{i}": s for i, s in enumerate(cap.note_like)})
        if since is not None:
            where.append("ts >= %(since)s")
            args["since"] = since
        if scope_id is not None:
            where.append("(video_id = %(s)s OR dream_short_id = %(s)s)")
            args["s"] = scope_id
        row = self._db.rows(f"SELECT COALESCE(sum(amount_inr), 0) AS spent FROM cost_entry WHERE {' AND '.join(where)}", args)[0]
        return Decimal(row["spent"])


def _limit(m: Mapping[str, Any], inr_key: str, usd_key: str, fx: Decimal) -> Decimal | None:
    if m.get(inr_key) is not None:
        return Decimal(str(m[inr_key]))
    if m.get(usd_key) is not None:
        return Decimal(str(m[usd_key])) * fx
    return None


def load_caps(config_dir: str | Path) -> dict[str, ProviderCap]:
    with open(Path(config_dir) / "providers.yaml", encoding="utf-8") as f:
        root = yaml.safe_load(f) or {}
    fx = Decimal(str((root.get("pricing") or {}).get("fxInrPerUsd", 94)))
    out: dict[str, ProviderCap] = {}
    for pid, m in (root.get("providerCaps") or {}).items():
        m = m or {}
        like = m.get("noteLike") or []
        out[pid] = ProviderCap(
            provider=pid, ledger_provider=str(m.get("ledgerProvider", pid)),
            daily_inr=_limit(m, "dailyInr", "dailyUsd", fx), per_video_inr=_limit(m, "perVideoInr", "perVideoUsd", fx),
            unit_type=m.get("unitType"), note_like=tuple([like] if isinstance(like, str) else like),
            stages=tuple(m.get("stages") or ()), idempotent=bool(m.get("idempotent", False)), free=bool(m.get("free", False)))
    return out


def _utc_midnight(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass
class ProviderCaps:
    caps: dict[str, ProviderCap]
    spend: SpendSource
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    def refusal(self, provider: str, estimate_inr: Decimal, *, stage: str | None = None,
                scope_id: uuid.UUID | None = None) -> str | None:
        """Why this call must not run, or None when it may. A provider with no entry is not capped here (the startup check warns)."""
        cap = self.caps.get(provider)
        if cap is None:
            return None
        if cap.stages and stage is not None and stage not in cap.stages:
            return f"{provider} is not allowed for stage {stage} (providerCaps.{provider}.stages: {', '.join(cap.stages)})"
        if cap.daily_inr is not None:
            spent = self.spend.spent(cap, _utc_midnight(self.clock()), None)
            if spent + estimate_inr > cap.daily_inr:
                return (f"{provider} daily cap of ₹{cap.daily_inr:.0f} would be broken (₹{spent:.2f} spent today UTC, "
                        f"this call ≈₹{estimate_inr:.2f}); resets at 00:00 UTC or raise providerCaps.{provider}.dailyInr")
        if cap.per_video_inr is not None and scope_id is not None:
            spent = self.spend.spent(cap, None, scope_id)
            if spent + estimate_inr > cap.per_video_inr:
                return (f"{provider} per-video cap of ₹{cap.per_video_inr:.0f} would be broken (₹{spent:.2f} already spent on this "
                        f"video, this call ≈₹{estimate_inr:.2f}); raise providerCaps.{provider}.perVideoInr")
        if not cap.limited:
            log.info("provider %s uncapped (free tier): ≈₹%s for stage %s", provider, estimate_inr, stage)
        return None


_default: ProviderCaps | None = None


def configure(caps: ProviderCaps | None) -> None:
    """Install (or clear) the process-wide instance the router and workers consult."""
    global _default
    _default = caps


def check_provider_budget(provider: str, estimate_inr: Decimal, *, stage: str | None = None,
                          scope_id: uuid.UUID | None = None, caps: ProviderCaps | None = None) -> None:
    """Raise ProviderCapError when `provider` may not spend `estimate_inr` now. No-op when no caps are configured."""
    c = caps or _default
    if c is None:
        return
    why = c.refusal(provider, estimate_inr, stage=stage, scope_id=scope_id)
    if why is not None:
        log.warning("provider cap refusal: %s", why)
        raise ProviderCapError(why + ". The stage waits for you: raise the cap or switch it to Manual.")


def keys_without_cap(config_dir: str | Path, env: Mapping[str, str] = os.environ) -> list[str]:
    """Providers whose secret_ref key is set in the environment but that have no providerCaps entry (names only, never keys)."""
    with open(Path(config_dir) / "providers.yaml", encoding="utf-8") as f:
        root = yaml.safe_load(f) or {}
    capped = set((root.get("providerCaps") or {}).keys())
    return [p["id"] for p in root.get("providers") or []
            if p.get("secret_ref") and env.get(p["secret_ref"]) and p["id"] not in capped and p.get("enabled", True)]


def warn_uncapped(config_dir: str | Path, env: Mapping[str, str] = os.environ) -> list[str]:
    missing = keys_without_cap(config_dir, env)
    for pid in missing:
        log.warning("provider %s has an API key configured but no providerCaps entry: it is uncapped. Add one to config/providers.yaml", pid)
    return missing


def build_caps(db: Any, config_dir: str | Path) -> ProviderCaps:
    """The caps the studio runs with; also installed process-wide for check_provider_budget and warns about uncapped keys."""
    caps = ProviderCaps(load_caps(config_dir), DbSpend(db))
    configure(caps)
    warn_uncapped(config_dir)
    return caps
