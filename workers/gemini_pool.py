"""Gemini multi-project failover (owner requirement, 18 Nov 2026).

Components (spec names → here): ProjectRegistry/AccountManager → `load_projects`; ErrorClassifier → `classify`;
CooldownManager → `Health`; FailoverManager + RequestExecutor → `execute`.

Config (.env; keys never logged):
    GEMINI_PROJECTS=brother:proj-id-1:1,main:gen-lang-client-0560809315:2     alias:project-id:priority (low first)
    GEMINI_KEY_BROTHER=...  GEMINI_KEY_MAIN=...  (extra keys of the SAME project: GEMINI_KEY_MAIN_2=...)
Without GEMINI_PROJECTS, GEMINI_API_KEY is the single project "default" (old behaviour).

Guardrails (spec C): failover only for temporary limits — RPM / TPM bursts and temporary server errors. Daily
quota (RPD), spend caps / billing restrictions, invalid keys, permission errors and suspensions put that project in
cooldown or disable it but NEVER fail over, so they are not circumvented. Timeouts (outcome unknown, may already be
billed) are not retried on another project (spec G). Keys of one project are one quota pool (spec B).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("projectv.gemini_pool")

RPM, TPM, RPD, SPEND, INVALID_KEY, PERMISSION, TEMPORARY, TIMEOUT, OTHER = (
    "rpm", "tpm", "rpd", "spend", "invalid_key", "permission", "temporary", "timeout", "other")
FAILOVER = {RPM, TPM, TEMPORARY}           # the only classes that may move to another project
MAX_FAILOVERS = 3                          # per request: no retry storms
BREAKER_THRESHOLD, BREAKER_OPEN_SEC = 5, 300
STATE_FILE = Path(__file__).resolve().parents[1] / ".run" / "gemini_health.json"


@dataclass
class Project:
    alias: str
    project_id: str
    priority: int
    keys: list[str] = field(repr=False)


def load_projects(env=os.environ) -> list[Project]:
    """Registry from env; keys of the same project id are merged into ONE pool (never independent quotas)."""
    spec = (env.get("GEMINI_PROJECTS") or "").strip()
    if not spec:
        key = env.get("GEMINI_API_KEY")
        return [Project("default", "default", 1, [key])] if key else []
    by_id: dict[str, Project] = {}
    for item in [x.strip() for x in spec.split(",") if x.strip()]:
        alias, project_id, prio = (item.split(":") + ["", "", "100"])[:3]
        a = alias.strip().upper()
        keys = [env[k] for k in sorted(env) if (k == f"GEMINI_KEY_{a}" or k.startswith(f"GEMINI_KEY_{a}_")) and env[k]]
        if not keys:
            log.warning("Gemini project %s has no key configured; skipped", alias)
            continue
        pid = project_id.strip() or alias
        if pid in by_id:  # same project listed twice: one pool
            by_id[pid].keys += [k for k in keys if k not in by_id[pid].keys]
            by_id[pid].priority = min(by_id[pid].priority, int(prio or 100))
        else:
            by_id[pid] = Project(alias.strip(), pid, int(prio or 100), keys)
    return sorted(by_id.values(), key=lambda p: p.priority)


def _details(e: Exception) -> str:
    parts = [str(e)]
    for attr in ("details", "response_json"):
        v = getattr(e, attr, None)
        if v:
            parts.append(json.dumps(v, default=str) if not isinstance(v, str) else v)
    return " ".join(parts)


def classify(e: Exception) -> tuple[str, float | None]:
    """(class, server retry delay in seconds or None) from the actual Gemini error."""
    text = _details(e)
    low = text.lower()
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    m = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s", text)
    delay = float(m.group(1)) if m else None
    if isinstance(e, TimeoutError) or "timed out" in low or "timeout" in low or code == 504:
        return TIMEOUT, None
    if code == 400 and ("api_key_invalid" in low or "api key not valid" in low):
        return INVALID_KEY, None
    if any(s in low for s in ("billing", "spend", "prepay", "outstanding balance", "credits", "payment")):
        return SPEND, None
    if code in (401, 403) or "permission_denied" in low or "suspended" in low:
        return PERMISSION, None
    if code == 429:
        if re.search(r"perday|per_day|requests per day|daily", low):
            return RPD, None
        if re.search(r"token", low):
            return TPM, delay
        return RPM, delay
    if code in (500, 502, 503) or "unavailable" in low or "overloaded" in low:
        return TEMPORARY, delay
    return OTHER, None


def _next_pacific_midnight(now: float) -> float:
    """Gemini daily quotas reset at midnight Pacific time. Fixed UTC-8 (no tz database needed on Windows): never
    resets early; at most one hour late during US summer time."""
    pt = dt.datetime.fromtimestamp(now, dt.timezone(dt.timedelta(hours=-8)))
    return (pt + dt.timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0).timestamp()


class Health:
    """Per-project cooldowns + circuit breaker, thread-safe, persisted so restarts keep daily/billing blocks."""

    def __init__(self, path: Path | None = STATE_FILE, clock=time.time):
        self.lock = threading.Lock()
        self.clock = clock
        self.path = path
        self.state: dict[str, dict] = {}
        if path and path.exists():
            try:
                self.state = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self.state = {}

    def _save(self) -> None:
        if self.path:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.state), encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass

    def available(self, pid: str) -> bool:
        with self.lock:
            return self.clock() >= self.state.get(pid, {}).get("until", 0)

    def ok(self, pid: str) -> None:
        with self.lock:
            if pid in self.state:
                self.state[pid] = {"until": 0, "fails": 0}
                self._save()

    def fail(self, pid: str, cls: str, delay: float | None) -> float:
        """Applies the cooldown for this failure class; returns its length in seconds."""
        with self.lock:
            now = self.clock()
            s = self.state.setdefault(pid, {"until": 0, "fails": 0})
            s["fails"] = s.get("fails", 0) + 1
            if cls in (RPM, TPM):
                cool = delay if delay is not None else min(60.0, 2 ** min(s["fails"], 6)) * random.uniform(0.8, 1.2)
            elif cls == RPD:
                cool = _next_pacific_midnight(now) - now
            elif cls == SPEND:
                cool = 6 * 3600.0
            elif cls in (INVALID_KEY, PERMISSION):
                cool = 24 * 3600.0
            elif cls == TEMPORARY:
                cool = (BREAKER_OPEN_SEC if s["fails"] >= BREAKER_THRESHOLD
                        else (delay or min(30.0, 2 ** s["fails"])) * random.uniform(0.8, 1.2))
            else:
                cool = 0.0
            s["until"] = now + cool
            s["last"] = cls
            self._save()
            return cool


class NoEligibleProject(RuntimeError):
    """Structured: no project could serve the request (spec C.8)."""

    def __init__(self, what: str, outcomes: list[dict]):
        super().__init__(f"{what}: no eligible Gemini project ({outcomes})")
        self.outcomes = outcomes


_local = threading.local()
_projects: list[Project] | None = None
_health: Health | None = None
_init_lock = threading.Lock()


def projects() -> list[Project]:
    global _projects
    with _init_lock:
        if _projects is None:
            _projects = load_projects()
        return _projects


def health() -> Health:
    global _health
    with _init_lock:
        if _health is None:
            _health = Health()
        return _health


def current_key() -> str | None:
    """The key of the project the current request is running on (used by workers.common.gemini())."""
    return getattr(_local, "key", None)


def has_alternative() -> bool:
    return bool(getattr(_local, "alternative", False))


def execute(fn, what: str, *, pool: list[Project] | None = None, hp: Health | None = None):
    """Runs fn() on the first eligible project; on RPM/TPM/temporary errors applies the cooldown and retries the SAME
    fn (same model, config, prompt, parts) on the next eligible project. Other errors are raised unchanged."""
    pool = projects() if pool is None else pool
    hp = health() if hp is None else hp
    if not pool:  # nothing configured: the old single-client path (gemini() reports a missing key itself)
        return fn()
    outcomes, failovers = [], 0
    eligible = [p for p in pool if hp.available(p.project_id)]
    for i, p in enumerate(eligible):
        _local.key = p.keys[(int(time.time()) // 60) % len(p.keys)]  # keys of one project share its quota
        _local.project = p.project_id
        _local.alternative = i + 1 < len(eligible)
        try:
            result = fn()
            hp.ok(p.project_id)
            if outcomes:
                log.info("Gemini %s served by project %s after %d failover(s)", what, p.project_id, len(outcomes))
            return result
        except Exception as e:  # noqa: BLE001 - classified below
            cls, delay = classify(e)
            cool = hp.fail(p.project_id, cls, delay)
            outcomes.append({"project": p.project_id, "attempt": len(outcomes) + 1, "status": getattr(e, "code", None),
                             "class": cls, "cooldownSec": round(cool, 1)})
            log.warning("Gemini %s on project %s: %s (status %s), cooldown %.0fs", what, p.project_id, cls,
                        getattr(e, "code", None), cool)
            if cls not in FAILOVER:
                raise
            failovers += 1
            if failovers > MAX_FAILOVERS:
                break
        finally:
            _local.key = None
            _local.alternative = False
    raise NoEligibleProject(what, outcomes)
