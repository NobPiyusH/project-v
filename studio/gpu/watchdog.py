"""Control-plane layer of the GPU safety net (CLAUDE.md rule 8). Port of PodWatchdog and PodWatchdogScheduler.

Each sweep lists the provider's pods and terminates every pod named with the studio prefix that is past the max lifetime,
has no lease after the grace period, has a released lease, or whose lease heartbeat is older than the idle limit. Pods
without the prefix are never touched. Dry-run only logs (and books the running cost). The in-pod layer (self_stop) covers the
laptop being off.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from studio.agents.jfmt import fixed
from studio.gpu.events import PodEvents
from studio.gpu.leases import PgPodLeases
from studio.gpu.pods import GpuPodLimits, Pod, PodApi

log = logging.getLogger(__name__)

ORPHAN_BOOK_MIN_SECONDS = 600


@dataclass
class Action:
    """One pod the sweep decided to terminate (or would, in dry-run)."""

    pod_id: str
    name: str
    reason: str
    done: bool
    error: str | None = None


@dataclass
class Report:
    """What the last sweep saw: served to the dashboard as the watchdog status."""

    at: datetime
    dry_run: bool
    configured: bool
    studio_pods: int
    studio_hourly_usd: float
    actions: list[Action] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat().replace("+00:00", "Z"), "dryRun": self.dry_run, "configured": self.configured,
            "studioPods": self.studio_pods, "studioHourlyUsd": self.studio_hourly_usd,
            "actions": [{"podId": a.pod_id, "name": a.name, "reason": a.reason, "done": a.done, "error": a.error} for a in self.actions],
            "warnings": self.warnings, "error": self.error,
        }


def _minutes(a: datetime, b: datetime) -> int:
    return int((b - a).total_seconds() / 60)  # Java Duration.toMinutes truncates toward zero


class PodWatchdog:
    def __init__(self, api: PodApi, leases: PgPodLeases, events: PodEvents, limits: GpuPodLimits,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._api, self._leases, self._events, self._limits, self._clock = api, leases, events, limits, clock
        self._last: Report | None = None
        self._last_sweep_error: str | None = None  # the same failure (e.g. an expired Vast 2FA session) is logged once, not every sweep

    def last(self) -> Report | None:
        return self._last

    def limits(self) -> GpuPodLimits:
        return self._limits

    def sweep(self) -> Report:
        """One sweep. Never raises: a failed sweep is reported and logged, and the next one tries again."""
        now = self._clock()
        try:
            r = self._do_sweep(now)
            self._last_sweep_error = None
        except Exception as e:  # noqa: BLE001 - the watchdog must survive anything
            if str(e) != self._last_sweep_error:  # nothing is destroyed on a failed list
                log.warning("GPU watchdog: could not list %s pods (%s); studio pods are unchecked until the next sweep "
                            "(their in-pod self-stop still applies)", self._api.provider(), e)
            self._last_sweep_error = str(e)
            r = Report(now, self._limits.dry_run, self._api.configured(), 0, 0, [], [], f"{type(e).__name__}: {e}")
        self._last = r
        return r

    def _studio_pods(self) -> list[Pod]:
        return [p for p in self._api.list_pods()
                if p.name and p.name.startswith(self._limits.prefix) and p.status.upper() != "TERMINATED"]

    def _do_sweep(self, now: datetime) -> Report:
        lim = self._limits
        if not self._api.configured():
            return Report(now, lim.dry_run, False, 0, 0)
        studio = self._studio_pods()
        hourly = sum(p.cost_per_hr_usd for p in studio)
        warnings: list[str] = []
        if len(studio) > lim.max_pods:
            warnings.append(f"{len(studio)} studio pods running, limit {lim.max_pods}")
        if hourly > lim.max_hourly_usd:
            warnings.append(f"studio pods bill ${fixed(hourly, 2)}/h, limit ${fixed(lim.max_hourly_usd, 2)}/h")
        for w in warnings:
            log.warning("GPU watchdog: %s", w)
        actions = []
        for pod in studio:
            why = self.reason(pod, now)
            if why is not None:
                actions.append(self._act(pod, why, now))
        return Report(now, lim.dry_run, True, len(studio), hourly, actions, warnings)

    def reason(self, pod: Pod, now: datetime) -> str | None:
        """Why this studio pod must go, or None if it may keep running."""
        lim = self._limits
        born = pod.created_at or pod.started_at
        if born is None:
            return "no creation time reported"
        age = _minutes(born, now)
        if age >= lim.max_runtime_minutes:
            return f"over max lifetime ({age} min, limit {lim.max_runtime_minutes})"
        lease = self._leases.find(pod.id)
        if lease is None:
            return f"no lease ({age} min old, grace {lim.lease_grace_minutes})" if age >= lim.lease_grace_minutes else None
        if lease.released_at is not None:
            return f"lease released at {lease.released_at.isoformat().replace('+00:00', 'Z')}"
        idle = age if lease.heartbeat_at is None else _minutes(lease.heartbeat_at, now)
        if idle >= lim.idle_minutes:
            return f"idle (no heartbeat for {idle} min, limit {lim.idle_minutes})"
        return None

    def _act(self, pod: Pod, reason: str, now: datetime) -> Action:
        provider = self._api.provider()
        if self._limits.dry_run:
            log.warning("GPU watchdog DRY RUN: would terminate %s pod %s (%s): %s", provider, pod.id, pod.name, reason)
            self._book_orphan(pod, reason, now)
            return Action(pod.id, pod.name, reason, False)
        try:
            self._api.terminate(pod.id)
        except Exception as e:  # noqa: BLE001
            log.warning("GPU watchdog: FAILED to terminate %s pod %s (%s): %s: %s", provider, pod.id, pod.name, reason, e)
            return Action(pod.id, pod.name, reason, False, str(e))
        log.warning("GPU watchdog: terminated %s pod %s (%s): %s", provider, pod.id, pod.name, reason)
        try:
            billed_from = pod.started_at or pod.created_at
            booked = self._events.last_orphan_booking(pod.id)  # dry-run sweeps already booked up to here
            if booked is not None and (billed_from is None or booked > billed_from):
                billed_from = booked
            hours = 0.0 if billed_from is None else max(0.0, int((now - billed_from).total_seconds()) / 3600.0)
            self._events.terminated(provider, pod, reason, hours, hours * pod.cost_per_hr_usd)
            self._leases.release(pod.id)
        except Exception as e:  # noqa: BLE001 - the pod is gone; a ledger hiccup must not hide that
            log.warning("GPU watchdog: pod %s terminated but its ledger/evidence row failed: %s", pod.id, e)
        return Action(pod.id, pod.name, reason, True)

    def _book_orphan(self, pod: Pod, reason: str, now: datetime) -> None:
        """Dry-run leaves the pod running, so its cost would never reach the ledger: book it in steps (rule 6)."""
        try:
            start = self._events.last_orphan_booking(pod.id) or pod.started_at or pod.created_at
            if start is None:
                return
            seconds = int((now - start).total_seconds())
            if seconds < ORPHAN_BOOK_MIN_SECONDS:
                return
            hours = seconds / 3600.0
            self._events.orphan_billed(self._api.provider(), pod, reason, hours, hours * pod.cost_per_hr_usd)
        except Exception as e:  # noqa: BLE001
            log.warning("GPU watchdog: could not book the running cost of orphan pod %s: %s", pod.id, e)

    def check_can_start(self, new_pod_hourly_usd: float) -> None:
        """The start-side guard: any code that creates a studio pod calls this first and must not create one if it raises.
        Refuses when the max pod count or the hourly spend limit would be passed, or the list call fails."""
        lim = self._limits
        if not self._api.configured():
            raise RuntimeError(f"no {self._api.provider()} API key configured")
        studio = self._studio_pods()
        if len(studio) + 1 > lim.max_pods:
            raise RuntimeError(f"refusing to start a pod: {len(studio)} studio pods already, limit {lim.max_pods} (projectv.gpu.max-pods)")
        hourly = sum(p.cost_per_hr_usd for p in studio) + new_pod_hourly_usd
        if hourly > lim.max_hourly_usd:
            raise RuntimeError(f"refusing to start a pod: studio pods would bill ${fixed(hourly, 2)}/h, "
                               f"limit ${fixed(lim.max_hourly_usd, 2)}/h (projectv.gpu.max-hourly-usd)")


class WatchdogLoop:
    """Runs the sweeps every poll_seconds (default 180) while the control plane is up, after an initial delay of 20 s.
    One watchdog per provider (RunPod, Vast.ai). A sweep never raises; the loop is belt and braces so the schedule cannot die."""

    def __init__(self, watchdogs: list[PodWatchdog], poll_seconds: float = 180, initial_delay: float = 20):
        self._watchdogs, self._poll, self._delay = watchdogs, poll_seconds, initial_delay
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        for w in self._watchdogs:
            try:
                w.sweep()
            except Exception as e:  # noqa: BLE001
                log.warning("GPU watchdog tick failed: %s", e)

    def start(self) -> None:
        def run() -> None:
            if self._stop.wait(self._delay):
                return
            while True:
                self.tick()
                if self._stop.wait(self._poll):
                    return

        self._thread = threading.Thread(target=run, name="gpu-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
