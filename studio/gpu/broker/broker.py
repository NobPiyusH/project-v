"""Provider-neutral GPU broker (docs/plans/m3-gpu-router.md). Port of GpuBroker.

acquire() walks the configured provider order and rents one GPU for a Dream Short from the cheapest (live price) provider that
is configured, inside its wallet cap, in stock at a live price within the watchdog limits, and that actually creates. Every
attempt writes a RoutingDecision; every rental ends in release(), which terminates and books a CostEntry. The boot command is
always wrapped with the in-pod guard and a lease is opened, so the watchdog covers whatever this class fails to clean up (rule 8).

The studio keeps ONE shared pod (owner, 20 Nov 2026: "1 pod only to run as many Shorts as I want"): any number of Shorts may use
it. Java's synchronized methods become one re-entrant lock; its REQUIRES_NEW transactions need nothing here because every
statement commits on its own.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from studio.agents.jfmt import fixed, jround
from studio.gpu.broker import community, support
from studio.gpu.broker.config import BrokerConfig
from studio.gpu.broker.provider import (
    CreateRequest,
    GpuProvider,
    GpuSpec,
    Instance,
    InstanceStatus,
    PlanUnavailableError,
    Preflight,
    Quote,
    State,
)
from studio.gpu.broker.records import BrokerRecords
from studio.gpu.broker.wallet import DAILY_CAP, VIDEO_CAP, GpuWallet
from studio.gpu.events import PodEvents
from studio.gpu.leases import PgPodLeases
from studio.gpu.pods import GpuPodLimits, Pod
from studio.gpu.watchdog import PodWatchdog

log = logging.getLogger(__name__)

# Providers whose quote key carries a per-offer id (Vast: "gpu|offerId|country|machine"); a pick there is the GPU type only.
MARKETPLACES = frozenset({"vast"})
CANARY_MAX_ATTEMPTS = 3
DEFAULT_CANARY_MAX_USD = 0.60


class NoGpuAvailableError(RuntimeError):
    """Nothing in the chain can serve the job; non-retryable, the step waits for the owner (rule 2b)."""


@dataclass(frozen=True)
class Rental:
    """short_id is the Short that rented the shared pod first."""

    short_id: uuid.UUID
    instance: Instance
    created_at: datetime


@dataclass(frozen=True)
class Pick:
    """How the owner wants the GPU chosen for a Short: Auto (the studio picks) or Manual (the owner's 'provider|gpu' key)."""

    mode: str = "auto"
    choice: str | None = None

    def manual(self) -> bool:
        return self.mode.lower() == "manual"


AUTO = Pick()


@dataclass
class Option:
    """One rentable (or not) option, for the owner's list. key is what a manual pick sends back."""

    provider: str
    gpu: str | None
    key: str | None
    stock: str
    hourly_usd: float
    available: bool
    why: str | None


class PodState(Enum):
    OPEN = "OPEN"  # still there (or its status is unreadable right now): carry on
    GONE = "GONE"  # the provider says it no longer exists: rent another
    NONE = "NONE"  # no open rental: the studio released it, never rent again for it


@dataclass
class _Cand:
    id: str
    p: GpuProvider | None
    q: Quote
    why: str | None
    row: dict[str, Any]

    def ok(self) -> bool:
        return self.why is None

    def key(self) -> str:
        return f"{self.id}|{self.q.gpu}"

    def type_key(self) -> str | None:
        """What a Manual pick stores: provider + GPU type (a marketplace's offer id is dropped: offers vanish in seconds)."""
        return pick_key(self.key()) if self.q.available else None


def pick_key(key: str | None) -> str | None:
    """The stable form of an option key: for a marketplace 'vast|RTX 4090|123|US|m5' becomes 'vast|RTX 4090'; every other key is unchanged."""
    if key is None:
        return None
    parts = key.split("|")
    return f"{parts[0]}|{parts[1]}" if len(parts) > 2 and parts[0] in MARKETPLACES else key


def _cand(id_: str, p: GpuProvider | None, q: Quote, why: str | None) -> _Cand:
    row: dict[str, Any] = {"provider": id_}
    if q.available:
        row["stock"], row["gpu"], row["hourlyUsd"] = q.stock, q.gpu, q.hourly_usd
        if q.detail and q.detail.strip():
            row["detail"] = q.detail  # e.g. Vast: the host's country and machine id
    if why is not None:
        row["skipped"] = why
    return _Cand(id_, p, q, why, row)


class GpuBroker:
    def __init__(
        self, providers: list[GpuProvider], config: BrokerConfig, wallet: GpuWallet, records: BrokerRecords, leases: PgPodLeases,
        events: PodEvents, limits: GpuPodLimits, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        prefs: Any = None, auth: support.WorkerAuth | None = None, states: Any = None, boot_timings: Any = None,
        cloud_preference: str = community.SECURE, boot_probe: community.BootProbe | None = None,
        attempt_limits: community.AttemptLimits | None = None, sleep: Callable[[float], None] = time.sleep,
    ):
        self._providers = {p.provider(): p for p in providers}
        self._config, self._wallet, self._records, self._leases, self._events = config, wallet, records, leases, events
        self._limits, self._clock = limits, clock
        self._prefs = prefs or support.NoPreferences()
        self._auth = auth or support.WorkerAuth()
        self._states = states or support.AllProven()
        self._timings = boot_timings or support.NoTimings()
        self._lock = threading.RLock()
        self._shared: Rental | None = None  # the one shared pod; changed only under the lock, read lock-free
        self._users: dict[uuid.UUID, datetime] = {}  # the Shorts using the shared pod, and since when
        self._pending_carry: list[uuid.UUID] = []  # Shorts on a pod retired as never-ready: they move to the next rental
        self._used_seconds: dict[uuid.UUID, int] = {}  # seconds used by Shorts that detached from the current pod
        self._canary_running: str | None = None
        # POD_CLOUD_PREFERENCE=community_then_secure needs a probe to look at the pod's boot; without one it stays 'secure'
        wants = community.normalise_preference(cloud_preference) == community.COMMUNITY_THEN_SECURE
        if wants and boot_probe is None:
            log.warning("GPU broker: POD_CLOUD_PREFERENCE=community_then_secure but no boot probe is wired; using today's behaviour")
        self._community = wants and boot_probe is not None
        self._probe, self._attempt_limits, self._sleep = boot_probe, attempt_limits or community.AttemptLimits(), sleep

    # ------------------------------------------------------------------------------------------------ boot timing

    def boot_timing(self, short_id: uuid.UUID) -> dict[str, Any] | None:
        """The boot timeline of the Short's current pod: steps in seconds since the rent, and each weight download."""
        r = self.open(short_id)
        row = self._timings.get(r.instance.id) if r else None
        return support.summary(row) if row else None

    def report_boot(self, short_id: uuid.UUID, timing: dict[str, Any] | None) -> None:
        """The worker's own timeline (from its ready /health), stored with the rental and logged. Best effort."""
        r = self.open(short_id)
        if r is None or timing is None:
            return
        try:
            self._timings.worker_report(r.instance.id, timing)
            row = self._timings.get(r.instance.id)
            if row:
                log.info("GPU boot timing %s %s: %s", row.provider, row.pod_id, support.summary(row))
        except Exception as e:  # noqa: BLE001
            log.warning("GPU broker: could not store the boot timing of %s: %s", r.instance.id, e)

    def _note_rented(self, provider: str, inst: Instance, image: str, at: datetime) -> None:
        try:
            self._timings.rented(inst.id, provider, image, at)
        except Exception as e:  # noqa: BLE001
            log.warning("GPU broker: boot timing not started for %s: %s", inst.id, e)

    # ------------------------------------------------------------------------------------------------ recovery

    def recover(self) -> None:
        """Rebuilds the open rentals from the unreleased leases after a control-plane restart, so release() still terminates the
        pod and books its cost, the wallet counts it, and the next acquire reuses it instead of renting a second pod."""
        with self._lock:
            try:
                held = self._leases.open_for_shorts()
            except Exception as e:  # noqa: BLE001
                log.warning("GPU broker: could not read open leases at startup (%s); the watchdog still covers every studio pod", e)
                return
            for lease in held:
                p = self._providers.get(lease.provider)
                if p is None:
                    log.warning("GPU broker: open lease %s belongs to unknown provider %s; left to the watchdog", lease.pod_id, lease.provider)
                    continue
                inst = Instance(lease.provider, lease.pod_id, lease.name, lease.hourly_usd, lease.gpu,
                                self._auth.decorate(lease.provider, lease.name, p.endpoint_for(lease.pod_id, self._config.port)))
                r = Rental(lease.dream_short_id, inst, lease.created_at)
                if self._shared is not None:
                    # Older builds rented one pod per Short; only ONE shared pod is kept. The others end now, booked to their Short.
                    log.warning("GPU broker: second open pod %s at startup; ending it (one shared pod only)", lease.pod_id)
                    self._end_pod(r, {lease.dream_short_id: 1}, "extra pod ended at startup: only one shared pod is kept")
                    continue
                self._shared = r
                attached = self._safe_attached(lease.pod_id)
                for u in attached or [lease.dream_short_id]:
                    self._users[u] = self._clock()
                try:
                    self._leases.heartbeat(lease.pod_id)
                except Exception as e:  # noqa: BLE001
                    log.warning("GPU broker: heartbeat of recovered lease %s failed: %s", lease.pod_id, e)
                log.info("GPU broker: recovered the shared pod %s %s for %d Short(s) after restart", lease.provider, lease.pod_id, len(self._users))

    def _safe_attached(self, pod_id: str) -> list[uuid.UUID]:
        try:
            return self._leases.attached(pod_id)
        except Exception:  # noqa: BLE001
            return []

    # ------------------------------------------------------------------------------------------------ state

    def open(self, short_id: uuid.UUID) -> Rental | None:
        """The shared pod, when this Short is using it."""
        r = self._shared
        return r if r is not None and short_id in self._users else None

    def _leased_elsewhere(self, short_id: uuid.UUID) -> Rental | None:
        """The Short's open pod as the lease table sees it, for a process that did not rent it. The control worker rents (acquire runs in its
        process, its memory holds the rental) while the API answers the media worker's heartbeat from ITS OWN broker, which knows nothing of
        it: it said NONE and the worker aborted the step with 'the studio released this Short's GPU'. Read-only: nothing is adopted."""
        try:
            for lease in self._leases.open_for_shorts():
                users = self._safe_attached(lease.pod_id)
                if short_id in users or (not users and lease.dream_short_id == short_id):
                    p = self._providers.get(lease.provider)
                    if p is None:
                        return None
                    inst = Instance(lease.provider, lease.pod_id, lease.name, lease.hourly_usd, lease.gpu,
                                    self._auth.decorate(lease.provider, lease.name, p.endpoint_for(lease.pod_id, self._config.port)))
                    return Rental(lease.dream_short_id, inst, lease.created_at)
        except Exception as e:  # noqa: BLE001
            log.warning("GPU broker: could not read the lease table for Short %s: %s", short_id, e)
        return None

    def has_open_pod(self) -> bool:
        """Whether the shared pod is on (rented by any Short): a new Short then starts without a boot."""
        return self._shared is not None

    def open_short_ids(self) -> list[uuid.UUID]:
        return [] if self._shared is None else list(self._users)

    def last_activity(self, short_id: uuid.UUID) -> datetime | None:
        """When the Short's pod was last used: the newest lease heartbeat, else the time it was rented."""
        r = self.open(short_id)
        if r is None:
            return None
        lease = self._leases.find(r.instance.id)
        beat = lease.heartbeat_at if lease else None
        return beat if beat is not None and beat > r.created_at else r.created_at

    # ------------------------------------------------------------------------------------------------ candidates

    def _spec(self) -> GpuSpec:
        c = self._config
        return GpuSpec(c.gpu_preference, c.min_vram_gb, c.min_ram_gb, c.disk_gb)

    def _candidates(self, spec: GpuSpec, hours: float | None = None) -> list[_Cand]:
        """Every provider's every option with the reason it can or cannot be rented now. Read-only."""
        hours = self._config.expected_hours if hours is None else hours
        out: list[_Cand] = []
        for id_ in self._config.order:
            p = self._providers.get(id_)
            if p is None:
                out.append(_cand(id_, None, Quote.unavailable(id_, ""), "no adapter"))
                continue
            if not p.configured():
                out.append(_cand(id_, p, Quote.unavailable(id_, ""), "not configured (no key in .env, or adapter not built)"))
                continue
            if not self._states.proven(id_):
                out.append(_cand(id_, p, Quote.unavailable(id_, ""),
                                 "not proven yet: no worker has run on it. Run its preflight, then the owner-approved canary"
                                 f" (POST /api/gpu/broker/providers/{id_}/canary?approve=true)"))
                continue
            try:
                quotes = p.quotes(spec)
                if not quotes:
                    out.append(_cand(id_, p, Quote.unavailable(id_, ""), f"no stock: {p.quote(spec).detail}"))
                    continue
            except Exception as e:  # noqa: BLE001
                out.append(_cand(id_, p, Quote.unavailable(id_, ""), f"quote failed: {e}"))
                continue
            for q in quotes:
                why = self._wallet.refusal(id_, q.hourly_usd * hours)
                if why is None:
                    try:
                        PodWatchdog(p, self._leases, self._events, self._limits, self._clock).check_can_start(q.hourly_usd)
                    except Exception as e:  # noqa: BLE001
                        why = str(e)
                out.append(_cand(id_, p, q, why))
        return out

    def options(self) -> list[Option]:
        """What the owner can choose from, rentable options first, cheapest first."""
        # Marketplace offers collapse to one row per GPU type (the cheapest rentable offer of it, else the cheapest blocked one)
        by_type: dict[str, _Cand] = {}
        rows: list[_Cand] = []
        for c in self._candidates(self._spec()):
            if c.q.available and c.id in MARKETPLACES:
                k = c.type_key()
                a = by_type.get(k)
                if a is None:
                    by_type[k] = c
                elif a.ok() != c.ok():
                    by_type[k] = a if a.ok() else c
                else:
                    by_type[k] = a if a.q.hourly_usd <= c.q.hourly_usd else c
            else:
                rows.append(c)
        rows += by_type.values()
        opts = [
            Option(c.id, (c.type_key()[len(c.id) + 1 :] if c.id in MARKETPLACES else c.q.gpu) if c.q.available else None,
                   c.type_key(), c.q.stock, c.q.hourly_usd, c.ok(), c.why)
            for c in rows
        ]
        return sorted(opts, key=lambda o: (not o.available, o.hourly_usd))

    def cheapest_quote(self) -> Quote | None:
        """Cheapest live quote that could be rented right now (configured, in stock, proven, inside its wallet); read-only."""
        spec, best = self._spec(), None
        for id_ in self._config.order:
            p = self._providers.get(id_)
            if p is None or not p.configured() or not self._states.proven(id_):
                continue
            try:
                q = p.quote(spec)
                if q.available and self._wallet.refusal(id_, q.hourly_usd * self._config.expected_hours) is None \
                        and (best is None or q.hourly_usd < best.hourly_usd):
                    best = q
            except Exception as e:  # noqa: BLE001
                log.warning("GPU broker: quote from %s failed: %s", id_, e)
        return best

    # ------------------------------------------------------------------------------------------------ acquire

    def acquire(self, short_id: uuid.UUID, capability: str, image: str, worker_argv: list[str], env: dict[str, str],
                volume_id: str | None, pick: Pick = AUTO, expected_hours: float = 0) -> Rental:
        """Rents (or returns the open) GPU for this Short. worker_argv is the pod's job command, wrapped here with the guard.
        expected_hours: the rental's expected hours for this Short (0 or less = the config's). Raises NoGpuAvailableError when no
        provider in the chain can serve it (the step then waits for the owner)."""
        with self._lock:
            hours = expected_hours if expected_hours > 0 else self._config.expected_hours
            if self._canary_running is not None and self._shared is None:
                raise NoGpuAvailableError(f"The {self._canary_running} canary rental is running (a few minutes); "
                                          "nothing else is rented meanwhile. Press Retry shortly.")
            # ONE shared pod: when it is on, every Short uses it (a manual GPU pick applies only when no pod is on).
            existing, carried = self._shared, []
            if existing is not None:
                if not self._gone(existing):
                    # The pod is on and costs by the minute: a video already at its own cap gets no more of it (running time counts as reserved)
                    why = self._wallet.video_refusal(short_id, 0.0, self.running_usd(), True)
                    if why is not None:
                        msg = f"{why} The shared pod stays on for the others."
                        self._records.decision(short_id, capability, [{"provider": existing.instance.provider, "skipped": why}], None, msg)
                        raise NoGpuAvailableError(msg)
                    existing = self._with_endpoint(existing)
                    self._attach(short_id)
                    self._leases.heartbeat(existing.instance.id)
                    return existing
                log.warning("GPU broker: the shared pod %s is gone (self-stopped or killed); ending it and renting again", existing.instance.id)
                carried = list(self._users)  # the Shorts that were using it move to the new pod
                self._end_shared("pod gone before reuse (self-stopped or killed)")
            allc = self._candidates(self._spec(), hours)
            for c in allc:  # the video's own cap, on top of the wallet and the daily cap: spent (failed attempts included) + this rental
                if c.ok() and c.q.available:
                    vwhy = self._wallet.video_refusal(short_id, c.q.hourly_usd * hours)
                    if vwhy is not None:
                        c.why = c.row["skipped"] = vwhy
            considered = [c.row for c in allc]
            # Cheapest live price first (owner, 20 Nov 2026); config order only breaks ties (the sort is stable).
            eligible = sorted((c for c in allc if c.ok()), key=lambda c: c.q.hourly_usd)
            how = "cheapest eligible live price"
            if pick.manual():
                key = pick.choice
                if not key or not key.strip():
                    eligible = []
                    considered.append({"provider": "-", "skipped": "Manual mode: no GPU picked yet (GET /api/gpu/broker/options, then choose one)"})
                else:
                    # A pick is a GPU type: the cheapest live offer of it (an old stored offer-id pick falls back to its type)
                    typ = pick_key(key)
                    matches = [c for c in allc if c.q.available and typ == c.type_key()]
                    wanted = min(matches, key=lambda c: (not c.ok(), c.q.hourly_usd), default=None)
                    if wanted is None or not wanted.ok():
                        considered.append({"provider": "-", "skipped": f"your pick {key} is not available now"
                                           + ("" if wanted is None else f": {wanted.why}")})
                        eligible = []
                    else:
                        eligible = sorted((c for c in allc if c.ok() and typ == c.type_key()), key=lambda c: c.q.hourly_usd)
                        how = "your manual pick"
            elif eligible:
                cheapest = eligible[0].q.hourly_usd
                learned = self._prefs.learned_key()
                fav = next((c for c in eligible if pick_key(learned) == c.type_key()), None) if learned else None
                if fav is not None and fav.q.hourly_usd <= cheapest * support.MAX_PREMIUM:
                    eligible.remove(fav)
                    eligible.insert(0, fav)
                    how = "learned from your last picks (you keep choosing it over the cheapest)"
            unsafe = False
            attempts_made = 0
            if self._community:
                eligible = self._community_order(eligible)
            for e in eligible:
                if unsafe:
                    break
                id_, p, q, row = e.id, e.p, e.q, e.row
                on_community = self._community and id_ == "runpod" and community.is_community(q.gpu)
                if on_community and attempts_made > 0:
                    # every earlier attempt is already booked in the ledger the wallet reads: the cap counts failed attempts too
                    why = self._wallet.refusal(id_, q.hourly_usd * hours) or self._wallet.video_refusal(short_id, q.hourly_usd * hours)
                    if why is not None:
                        row["skipped"] = f"retry not started: {why}"
                        continue
                now = self._clock()
                name = f"{self._limits.prefix}{id_}-{str(short_id)[:8]}-{uuid.uuid4().hex[:4]}"
                argv = p.guard(worker_argv, self._limits.max_runtime_minutes, self._limits.idle_minutes, now)
                try:
                    inst = p.create(CreateRequest(name, image, self._config.disk_gb, argv, self._auth.env(id_, name, env),
                                                  self._config.port, volume_id, q))
                except Exception as ex:  # noqa: BLE001
                    # The call may have failed after the provider created the pod (timeout on the reply): look it up by its
                    # generated name and adopt it before any other create, or it would run unleased and unbooked.
                    try:
                        found = self._find(p, name, q)
                    except Exception as le:  # noqa: BLE001
                        row["skipped"] = (f"create failed ({ex}) and the pod list failed ({le}): "
                                          "not renting anywhere else until that is known")
                        log.warning("GPU broker: %s create failed for Short %s and the pod list failed too; stopping", id_, short_id)
                        unsafe = True
                        continue
                    if found is None:
                        row["skipped"] = f"create failed: {ex}"
                        log.warning("GPU broker: %s create failed for Short %s: %s", id_, short_id, ex)
                        continue
                    log.warning("GPU broker: %s create reported '%s' but pod %s exists; adopting it", id_, ex, found.id)
                    inst = found
                inst = Instance(inst.provider, inst.id, inst.name, inst.hourly_usd, inst.gpu,
                                self._auth.decorate(id_, inst.name, inst.endpoint))
                try:
                    self._leases.open(id_, inst.id, inst.name, f"Dream Short {short_id}", short_id, inst.hourly_usd, inst.gpu)
                except Exception as ex:  # noqa: BLE001 - never keep a pod we cannot lease
                    self._safe_terminate(p, inst.id)
                    row["skipped"] = f"lease failed, pod terminated: {ex}"
                    continue
                if on_community:
                    attempts_made += 1
                    outcome = self._boot_check(p, inst, short_id, row, attempts_made, now)
                    if outcome == "unsafe":
                        unsafe = True
                        continue
                    if outcome == "aborted":
                        continue
                row["chosen"] = True
                n_ok = sum(1 for c in allc if c.ok())
                reason = f"{id_}: {how}, ${fixed(inst.hourly_usd, 2)}/h ({n_ok} eligible)"
                try:
                    self._records.decision(short_id, capability, considered, id_, reason)
                except Exception as ex:  # noqa: BLE001 - the pod is rented and leased; a ledger hiccup must not strand it
                    log.warning("GPU broker: rented pod %s but its RoutingDecision failed: %s", inst.id, ex)
                self._note_rented(id_, inst, image, now)
                r = Rental(short_id, inst, now)
                self._shared = r
                self._attach(short_id)
                for u in carried:
                    self._attach(u)
                for u in self._pending_carry:
                    self._attach(u)
                self._pending_carry.clear()
                log.info("GPU broker: rented %s %s (%s, $%s/h live) for Short %s", id_, inst.id, inst.gpu, inst.hourly_usd, short_id)
                return r
            daily = next((str(r["skipped"]) for r in considered if r.get("skipped") and str(r["skipped"]).startswith(DAILY_CAP)), None)
            if daily is not None and not eligible and not unsafe:
                msg = (f"Daily GPU cap reached ({daily}). Nothing was rented; this Short waits for you. Press Retry"
                       " after 00:00 UTC, or raise pricing.gpuBroker.dailyCapUsd in config/providers.yaml.")
                self._records.decision(short_id, capability, considered, None, msg)
                raise NoGpuAvailableError(msg)
            vcap = next((str(r["skipped"]) for r in considered if r.get("skipped") and str(r["skipped"]).startswith(VIDEO_CAP)), None)
            if vcap is not None and not eligible and not unsafe:
                self._records.decision(short_id, capability, considered, None, vcap)
                raise NoGpuAvailableError(vcap)
            skipped = [f"{r['provider']}{'' if r.get('gpu') is None else ' ' + str(r['gpu'])} ({r['skipped']})"
                       for r in considered if r.get("skipped") is not None]
            reason = f"No GPU can run {capability} now: {skipped}"
            self._records.decision(short_id, capability, considered, None, reason)
            raise NoGpuAvailableError(reason)

    # ------------------------------------------------------------------------------------------------ community mode

    def _community_order(self, eligible: list[_Cand]) -> list[_Cand]:
        """Community mode: at most max_community_attempts RunPod Community tries (the cheapest offer, then another one, else the same
        again), then everything else in the usual cheapest-first order (RunPod Secure included). Never more Community tries."""
        comm = [c for c in eligible if c.id == "runpod" and community.is_community(c.q.gpu)]
        rest = [c for c in eligible if c not in comm]
        n = self._attempt_limits.max_community_attempts
        plan = [comm[min(i, len(comm) - 1)] for i in range(n)] if comm else []
        return plan + rest

    def _boot_check(self, p: GpuProvider, inst: Instance, short_id: uuid.UUID, row: dict[str, Any], n: int, started: datetime) -> str:
        """Watches a just-rented Community pod until decide() says keep or abort. Returns 'kept', 'aborted' (pod terminated, booked,
        lease released) or 'unsafe' (it could not be terminated: the caller must not rent anything else). Whatever goes wrong inside
        (even an exception or interrupt), the pod is terminated before this returns or the exception leaves."""
        limits, state, keep, ended = self._attempt_limits, community.AttemptState(), False, True
        decision = community.Decision(community.Verdict.WAIT, "", state)
        try:
            while True:
                elapsed = max(0.0, (self._clock() - started).total_seconds())
                text = ""
                try:
                    text = self._probe.observe(inst.id) if self._probe is not None else ""
                except Exception as ex:  # noqa: BLE001 - an unreadable log is 'nothing new'; the timeout still ends the attempt
                    log.warning("GPU broker: could not read the boot log of %s: %s", inst.id, ex)
                decision = community.decide(state, elapsed, text, community.parse_cuda(text), limits)
                state = decision.state
                if decision.verdict is not community.Verdict.WAIT:
                    break
                try:
                    self._leases.heartbeat(inst.id)
                except Exception:  # noqa: BLE001
                    pass
                self._sleep(limits.poll_s)
            keep = decision.verdict is community.Verdict.KEEP
        finally:
            if not keep:  # abort, or any exception on the way: the pod does not outlive this call
                spent = max(0.0, (self._clock() - started).total_seconds())
                why = decision.reason or "interrupted"
                ended = self._end_pod(Rental(short_id, inst, started), {short_id: 1}, f"community attempt {n} aborted: {why}")
                log.warning("GPU attempt %d (runpod COMMUNITY %s pod=%s $%s/h): ABORT after %d s: %s; terminated=%s",
                            n, inst.gpu, inst.id, fixed(inst.hourly_usd, 2), int(spent), why, ended)
        if keep:
            log.info("GPU attempt %d (runpod COMMUNITY %s pod=%s $%s/h): KEEP: %s", n, inst.gpu, inst.id, fixed(inst.hourly_usd, 2), decision.reason)
            return "kept"
        if not ended:
            row["skipped"] = f"aborted ({decision.reason}) but the pod could not be terminated: not renting anywhere else"
            return "unsafe"
        row["skipped"] = f"aborted attempt {n}: {decision.reason}"
        return "aborted"

    def _with_endpoint(self, r: Rental) -> Rental:
        """A rental rebuilt after a restart may not know its address yet (Vast maps the port late): ask the provider again."""
        i = r.instance
        p = self._providers.get(i.provider)
        if i.endpoint is not None or p is None:
            return r
        ep = self._auth.decorate(i.provider, i.name, p.endpoint_for(i.id, self._config.port))
        if ep is None:
            return r
        fixed_r = Rental(r.short_id, Instance(i.provider, i.id, i.name, i.hourly_usd, i.gpu, ep), r.created_at)
        self._shared = fixed_r
        return fixed_r

    # ------------------------------------------------------------------------------------------------ money views

    def running_usd(self) -> float:
        """What the pod that is on now has cost since it was rented (not booked until it ends), USD. 0 when none is on."""
        r = self._shared
        if r is None:
            return 0.0
        return max(0.0, int((self._clock() - r.created_at).total_seconds()) / 3600.0) * r.instance.hourly_usd

    def today(self) -> dict[str, Any]:
        """Today's GPU spend (UTC) vs the daily cap, for the dashboard: booked + the running pod so far."""
        booked, running = self._wallet.spent_today_usd(), self.running_usd()
        return {"spentUsd": jround((booked + running) * 100) / 100.0, "bookedUsd": jround(booked * 100) / 100.0,
                "runningUsd": jround(running * 100) / 100.0, "capUsd": self._wallet.daily_cap_usd()}

    # ------------------------------------------------------------------------------------------------ pod status

    def heartbeat(self, short_id: uuid.UUID) -> None:
        """Keeps the lease warm without any provider call (the worker pings this while it waits or runs jobs)."""
        r = self.open(short_id) or self._leased_elsewhere(short_id)
        if r is not None:
            self._leases.heartbeat(r.instance.id)

    def pod_state(self, short_id: uuid.UUID) -> PodState:
        """One provider status call: lets a worker waiting for a pod notice within a minute that it was deleted."""
        r = self.open(short_id) or self._leased_elsewhere(short_id)
        if r is None:
            return PodState.NONE
        return PodState.GONE if self._gone(r) else PodState.OPEN

    def status(self, short_id: uuid.UUID) -> InstanceStatus:
        """Readiness of the Short's pod (the worker's own /health is the final truth); also keeps the lease warm."""
        r = self.open(short_id)
        if r is None:
            return InstanceStatus(State.GONE, None, "no open rental")
        st = self._providers[r.instance.provider].status(r.instance.id)
        if st.state != State.GONE:  # a gone pod must not look alive: release books up to the last real beat
            self._leases.heartbeat(r.instance.id)
        return st

    def _gone(self, r: Rental) -> bool:
        """True when the provider says the pod no longer exists. An unreadable status is not 'gone'."""
        p = self._providers.get(r.instance.provider)
        if p is None:
            return False
        try:
            state = p.status(r.instance.id).state
            if state == State.RUNNING:
                try:
                    self._timings.running(r.instance.id, self._clock())
                except Exception:  # noqa: BLE001 - a timing row never decides whether a pod is alive
                    pass
            return state == State.GONE
        except Exception as e:  # noqa: BLE001
            log.warning("GPU broker: status of %s unreadable (%s); keeping the rental", r.instance.id, e)
            return False

    def _find(self, p: GpuProvider, name: str, q: Quote) -> Instance | None:
        """The studio pod with this generated name, if the provider has it (a create whose reply was lost)."""
        for pod in p.list_pods():
            if name == pod.name and pod.status.upper() != "TERMINATED":
                hourly = pod.cost_per_hr_usd if pod.cost_per_hr_usd > 0 else q.hourly_usd
                return Instance(p.provider(), pod.id, name, hourly, (q.gpu or "").split("|")[0], p.endpoint_for(pod.id, self._config.port))
        return None

    # ------------------------------------------------------------------------------------------------ release

    def release(self, short_id: uuid.UUID, why: str) -> None:
        """This Short stops using the shared pod. The pod itself ends (terminated, booked, lease released) when the last Short lets
        go; while others still use it, it stays on. Safe to call twice."""
        with self._lock:
            r = self._shared
            since = self._users.pop(short_id, None)
            if r is None or since is None:
                return
            self._used_seconds[short_id] = self._used_seconds.get(short_id, 0) + max(1, int((self._clock() - since).total_seconds()))
            self._persist_detach(r.instance.id, short_id)
            if not self._users:
                self._end_shared(why)
            else:
                log.info("GPU broker: Short %s let go of the shared pod %s (%s); it stays on for %d other Short(s)",
                         short_id, r.instance.id, why, len(self._users))

    def retire_unready(self, url: str | None, why: str) -> bool:
        """A media worker waited the whole ready limit (or saw the boot fail) on the shared pod at url: that pod is useless, so it is
        terminated and booked now, and the Shorts on it move to the next rental. Does nothing when the shared pod is already another
        one. Returns True when the pod was retired."""
        with self._lock:
            r = self._shared
            if r is None or url is None or url.strip().rstrip("/") != (r.instance.endpoint or "").rstrip("/"):
                return False
            log.warning("GPU broker: pod %s never became ready (%s); terminating it and renting another", r.instance.id, why)
            self._pending_carry.extend(self._users)
            self._end_shared(f"never became ready: {why}")
            return True

    def release_all(self, why: str) -> None:
        """The owner's 'Turn off GPU': ends the shared pod now, for every Short that uses it. Safe to call with no pod."""
        with self._lock:
            self._end_shared(why)

    def _attach(self, short_id: uuid.UUID) -> None:
        r = self._shared
        if r is not None and short_id not in self._users:
            self._users[short_id] = self._clock()
            try:
                self._leases.attach(r.instance.id, short_id)
            except Exception as e:  # noqa: BLE001
                log.warning("GPU broker: could not record Short %s on pod %s: %s", short_id, r.instance.id, e)

    def _persist_detach(self, pod_id: str, short_id: uuid.UUID) -> None:
        try:
            self._leases.detach(pod_id, short_id)
        except Exception as e:  # noqa: BLE001
            log.warning("GPU broker: could not record Short %s leaving pod %s: %s", short_id, pod_id, e)

    def _end_shared(self, why: str) -> None:
        """Ends the shared pod for everyone: the cost is split by how long each Short used it."""
        r = self._shared
        if r is None:
            return
        now = self._clock()
        weights = dict(self._used_seconds)
        for u, since in self._users.items():
            weights[u] = weights.get(u, 0) + max(1, int((now - since).total_seconds()))
        for u in list(self._users):
            self._persist_detach(r.instance.id, u)
        self._shared = None
        self._users.clear()
        self._used_seconds.clear()
        self._end_pod(r, weights, why)

    def _end_pod(self, r: Rental, weights: dict[uuid.UUID, int], why: str) -> bool:
        """Terminates a pod, books its time (split by weights) and releases its lease. True when the terminate call went through."""
        p = self._providers.get(r.instance.provider)
        end = self._clock()
        if self._gone(r):  # deleted or self-stopped earlier: it stopped billing at about its last heartbeat, not now
            lease = self._leases.find(r.instance.id)
            beat = lease.heartbeat_at if lease else None
            if beat is not None and r.created_at < beat < end:
                end = beat
        gone = self._safe_terminate(p, r.instance.id)
        hours = max(0.0, int((end - r.created_at).total_seconds()) / 3600.0)
        try:
            self._book_rental(r, hours, weights, why)
            self._leases.release(r.instance.id)
        except Exception as e:  # noqa: BLE001
            log.warning("GPU broker: rental %s ended but its ledger row failed: %s", r.instance.id, e)
        if not gone:
            log.warning("GPU broker: could not terminate %s; lease released, the watchdog will", r.instance.id)
        return gone

    def _book_rental(self, r: Rental, hours: float, weights: dict[uuid.UUID, int], why: str) -> None:
        note = f"GPU rental {r.instance.id} ({r.instance.gpu}) ended: {why}"
        w = weights or {r.short_id: 1}
        if len(w) == 1:
            self._records.cost(next(iter(w)), r.instance.provider, hours, r.instance.hourly_usd, note)
            return
        total = sum(w.values())
        for short, secs in w.items():
            share = secs / total if total > 0 else 1.0 / len(w)
            self._records.cost(short, r.instance.provider, hours * share, r.instance.hourly_usd,
                               f"{note} ({jround(share * 100)}% of the shared pod)")

    @staticmethod
    def _safe_terminate(p: GpuProvider | None, pod_id: str) -> bool:
        try:
            if p is None:
                raise RuntimeError("no adapter")
            p.terminate(pod_id)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("GPU broker: terminate %s failed: %s", pod_id, e)
            return False

    # ------------------------------------------------------------------------------------------------ pre-check and canary

    def provider_status(self, id_: str) -> dict[str, Any]:
        """One provider's state for the GPU API: configured / preflight / proven."""
        p, st = self._providers.get(id_), self._states.get(id_)
        return {
            "provider": id_, "configured": p is not None and p.configured(), "preflightOk": st.preflight_ok,
            "preflightAt": None if st.preflight_at is None else support.iso_z(st.preflight_at), "preflightNote": st.preflight_note,
            "proven": st.proven, "provenNote": st.proven_note, "monthlyCapUsd": self._config.cap_usd(id_),
            "inAutoSearch": p is not None and p.configured() and st.proven and self._config.cap_usd(id_) > 0,
        }

    def provider_statuses(self) -> list[dict[str, Any]]:
        return [self.provider_status(i) for i in self._config.order if i in self._providers]

    def preflight(self, id_: str, image: str, worker_argv: list[str], env: dict[str, str], volume_id: str | None) -> Preflight:
        """Free pre-check of one provider with the real create request (never rents). The result is stored for the GPU API."""
        p = self._providers.get(id_)
        if p is None:
            return Preflight(False, [f"no adapter for '{id_}'"])
        try:
            name = f"{self._limits.prefix}{id_}-preflight"
            argv = p.guard(worker_argv, self._limits.max_runtime_minutes, self._limits.idle_minutes, self._clock())
            probe = CreateRequest(name, image, self._config.disk_gb, argv, self._auth.env(id_, name, env), self._config.port, volume_id, None)
            r = p.preflight(self._spec(), probe)
        except Exception as e:  # noqa: BLE001
            r = Preflight(False, [f"preflight failed: {e}"])
        try:
            self._states.record_preflight(id_, r.ok, "; ".join(r.checks))
        except Exception as e:  # noqa: BLE001
            log.warning("GPU broker: could not store the preflight of %s: %s", id_, e)
        return r

    def canary(self, id_: str, image: str, worker_argv: list[str], env: dict[str, str], volume_id: str | None,
               health: Callable[[str], str | None], timeout_seconds: int, poll_millis: int,
               max_usd: float = DEFAULT_CANARY_MAX_USD) -> dict[str, Any]:
        """ONE tiny owner-approved rental on id_. Rents the provider's cheapest GPU, waits for the worker's health check, always
        terminates and books it, and sets proven=true only when the worker answered. Refused while any pod is on. Leased without a
        Short, so the watchdog covers it if this method dies. max_usd: the owner-approved cap: a plan whose price for the whole
        canary run would exceed it is never rented."""
        p = self._providers.get(id_)
        out: dict[str, Any] = {"provider": id_, "proven": False}
        pre = self.preflight(id_, image, worker_argv, env, volume_id)
        out["preflight"] = pre.checks
        if p is None or not p.configured() or not pre.ok:
            out["result"] = "not run: preflight failed, nothing was rented"
            return out
        canary_hours = (timeout_seconds + 120) / 3600.0
        with self._lock:
            if self._shared is not None or self._canary_running is not None:
                out["result"] = "not run: a pod or another canary is on; turn the GPU off first"
                return out
            plans = sorted(p.quotes(self._spec()), key=lambda q: q.hourly_usd)[:CANARY_MAX_ATTEMPTS]
            if not plans:
                out["result"] = "not run: no GPU in stock, nothing was rented"
                return out
            q = plans[0]
            cost = q.hourly_usd * canary_hours
            if cost > max_usd:
                out["result"] = (f"not run: the cheapest plan ({q.gpu}, ${fixed(q.hourly_usd, 2)}/h) would cost ${fixed(cost, 2)} for a "
                                 f"{timeout_seconds + 120} s canary, over the approved cap of ${fixed(max_usd, 2)}")
                return out
            why = self._wallet.refusal(id_, cost)
            if why is not None:
                out["result"] = f"not run: {why}"
                return out
            self._canary_running = id_
            name = f"{self._limits.prefix}{id_}-canary-{uuid.uuid4().hex[:4]}"
        start = self._clock()
        inst: Instance | None = None
        failure: str | None = None
        attempts: list[str] = []
        out["attempts"] = attempts
        try:
            argv = p.guard(worker_argv, max(10, timeout_seconds // 60 + 5), self._limits.idle_minutes, start)
            for q in plans:
                cost = q.hourly_usd * canary_hours
                if cost > max_usd:
                    attempts.append(f"{q.gpu} ${q.hourly_usd}/h: skipped, ${fixed(cost, 2)} is over the cap of ${fixed(max_usd, 2)}")
                    failure = f"create failed: no plan left within the approved cap after: {'; '.join(attempts)}"
                    break
                try:
                    inst = p.create(CreateRequest(name, image, self._config.disk_gb, argv, self._auth.env(id_, name, env),
                                                  self._config.port, volume_id, q))
                    attempts.append(f"{q.gpu} ${q.hourly_usd}/h: created")
                    failure = None
                    break
                except PlanUnavailableError as ex:
                    attempts.append(f"{q.gpu} ${q.hourly_usd}/h: not available ({ex})")
                    failure = f"create failed: every plan tried was unavailable: {'; '.join(attempts)}"
                except Exception as ex:  # noqa: BLE001
                    attempts.append(f"{q.gpu} ${q.hourly_usd}/h: failed ({ex})")
                    failure = f"create failed: {ex}"
                    try:
                        inst = self._find(p, name, q)  # a pod whose create reply was lost is adopted, so it is terminated below
                    except Exception:  # noqa: BLE001
                        failure += f" (and the pod list failed: the watchdog ends any pod named {name})"
                    break  # only "plan not available" moves on to the next plan
            if inst is not None:
                try:
                    self._leases.open(id_, inst.id, inst.name, "canary")
                except Exception as ex:  # noqa: BLE001
                    log.warning("GPU broker: canary lease failed: %s", ex)
                out["gpu"], out["hourlyUsd"] = inst.gpu, inst.hourly_usd
                if failure is None:
                    failure = self._wait_healthy(p, inst, health, timeout_seconds, poll_millis)
        finally:
            if inst is not None:
                ended = self._safe_terminate(p, inst.id)
                hours = max(1 / 3600.0, int((self._clock() - start).total_seconds()) / 3600.0)
                try:
                    self._records.cost(None, id_, hours, inst.hourly_usd, f"canary rental {inst.id} ({inst.gpu})")
                    self._leases.release(inst.id)
                except Exception as e:  # noqa: BLE001
                    log.warning("GPU broker: canary %s ended but its ledger row failed: %s", inst.id, e)
                out["terminated"], out["hours"] = ended, hours
            self._canary_running = None
        if failure is None and inst is not None:
            self._states.mark_proven(id_, f"canary ok {support.iso_z(self._clock())} on {inst.gpu} ${inst.hourly_usd}/h")
            out["proven"] = True
            out["result"] = "worker healthy; pod terminated; provider marked proven"
        else:
            out["result"] = f"failed, provider stays unproven: {failure}"
        return out

    def _wait_healthy(self, p: GpuProvider, inst: Instance, health: Callable[[str], str | None], timeout_seconds: int,
                      poll_millis: int) -> str | None:
        """None when the worker answered its health check in time, else why not."""
        ep = self._auth.decorate(inst.provider, inst.name, inst.endpoint)
        deadline = time.time() + timeout_seconds
        last = "no address yet"
        while time.time() < deadline:
            if ep is None:
                ep = self._auth.decorate(inst.provider, inst.name, p.endpoint_for(inst.id, self._config.port))
            if ep is not None:
                last = health(ep)
                if last is None:
                    return None
            time.sleep(poll_millis / 1000.0)
        return f"worker never became healthy within {timeout_seconds} s: {last}"


__all__ = ["AUTO", "GpuBroker", "NoGpuAvailableError", "Option", "Pick", "Pod", "PodState", "Rental", "pick_key"]
