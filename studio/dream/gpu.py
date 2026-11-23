"""Dream Shorts' door to the GPU broker. Port of DreamGpu. Off unless the provider setting is "broker"; with the default "modal" nothing
here is called. One rented pod per Short: warmed at the writing step so the weights download overlaps it, reused for every picture and
clip, released when the GPU steps are over or the Short ends. The rental's whole time is booked as one cost entry when it is released.

Every pod rented here goes through GpuBroker.acquire(), which wraps the worker command with the self-stop guard and registers the
watchdog lease (rule 8 and the lease rule). Nothing in this module rents a pod any other way."""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from studio.agents.jfmt import jround
from studio.db import Db
from studio.dream.settings import RentalEstimate
from studio.gpu.broker import support as boot
from studio.gpu.broker.broker import AUTO, GpuBroker, Option, Pick, PodState, pick_key

log = logging.getLogger("studio.dream.gpu")

VOLUME_MOUNT = "/workspace"
WARM_ROOT = "/opt/projectv"  # where docker/pod/Dockerfile bakes ComfyUI (not under /workspace, which a provider volume may cover)


@dataclass(frozen=True)
class Session:
    """Where the Short's worker listens and how long to wait for it."""

    url: str | None
    ready_timeout_seconds: int


class PickRefusedError(ValueError):
    """A manual pick the studio refuses: 400 when the type is unknown, 409 (blocked) when it is known but blocked now."""

    def __init__(self, message: str, blocked: bool):
        super().__init__(message)
        self.blocked = blocked


class DreamGpu:
    def __init__(self, broker: GpuBroker, db: Db, prefs: Any, pricing: Any, provider: str = "modal", worker_file: str = "workers/pod/server.py",
                 image: str = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404", volume_id: str = "", warm_image: str = "",
                 ready_timeout_minutes: int = 30, port: int = 8000, seconds_per_picture: float = 10, seconds_per_81_frames: float = 76,
                 start_seconds: float = 900, worst_case_hourly_usd: float = 2.0, estimate: RentalEstimate | None = None):
        if provider.lower() not in ("modal", "broker"):
            raise ValueError(f"projectv.dream.gpu-provider must be modal or broker, not '{provider}'")
        self.broker, self.db, self.prefs, self.pricing = broker, db, prefs, pricing
        self._enabled = provider.lower() == "broker"
        self.worker_file = worker_file
        self.image = image
        self.warm_image = warm_image.strip() if warm_image and warm_image.strip() else None
        self.volume_id = volume_id or None
        self.ready_timeout_minutes = ready_timeout_minutes
        self.port = port
        self.seconds_per_picture, self.seconds_per_81_frames, self.start_seconds = seconds_per_picture, seconds_per_81_frames, start_seconds
        self.worst_case_hourly_usd = worst_case_hourly_usd
        self.estimate = estimate or RentalEstimate()
        # Video holders of the shared pod: Dream's "nobody waits" release must not end a pod a Video batch is using.
        self._external_holds: set[uuid.UUID] = set()
        self._lock = threading.Lock()

    def enabled(self) -> bool:
        return self._enabled

    # ---- estimates

    def rental_hours(self, dream_id: uuid.UUID) -> float:
        rows = self.db.rows("SELECT shot_count, picture_model, svi_scenes FROM dream_short WHERE id = %(d)s", {"d": dream_id})
        return self.estimate.hours_for_row(rows[0] if rows else None)

    def rental_estimate(self, dream_id: uuid.UUID) -> dict[str, Any]:
        h = self.rental_hours(dream_id)
        q = self.broker.cheapest_quote()
        hourly = q.hourly_usd if q is not None else self.worst_case_hourly_usd
        return {"hours": jround(h * 100) / 100.0, "usd": jround(h * hourly * 100) / 100.0, "hourlyUsd": hourly}

    def picture_seconds(self, dream_id: uuid.UUID, pictures: int) -> float:
        return pictures * self.seconds_per_picture + (0 if self.broker.has_open_pod() else self.start_seconds)

    def clip_seconds(self, dream_id: uuid.UUID, total_frames: int) -> float:
        return self.seconds_per_81_frames * total_frames / 81.0 + (0 if self.broker.has_open_pod() else self.start_seconds)

    def inr(self, seconds: float) -> Decimal:
        """Estimate in rupees at the cheapest live price right now (worst-case watchdog rate if nothing is quotable)."""
        q = self.broker.cheapest_quote()
        hourly = q.hourly_usd if q is not None else self.worst_case_hourly_usd
        return Decimal(repr(hourly * seconds / 3600.0 * self.pricing.fx())).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # ---- the broker's view

    def boot_timing(self, dream_id: uuid.UUID) -> dict[str, Any] | None:
        return self.broker.boot_timing(dream_id)

    def report_boot(self, dream_id: uuid.UUID, timing: dict[str, Any]) -> None:
        self.broker.report_boot(dream_id, timing)

    def today(self) -> dict[str, Any]:
        return self.broker.today()

    def is_open(self, dream_id: uuid.UUID) -> bool:
        return self.broker.open(dream_id) is not None

    def users_on_pod(self) -> int:
        return len(self.broker.open_short_ids())

    def last_activity(self, dream_id: uuid.UUID) -> datetime | None:
        return self.broker.last_activity(dream_id)

    def options(self) -> list[Option]:
        return self.broker.options()

    def provider_statuses(self) -> list[dict[str, Any]]:
        return self.broker.provider_statuses()

    # ---- renting

    def warm(self, dream_id: uuid.UUID) -> None:
        """Best effort at the writing step: start the pod so the download overlaps the writing. A failure is retried by the first GPU step."""
        if not self._enabled:
            return
        try:
            self.session(dream_id)
        except Exception as e:
            log.warning("GPU warm-up for Short %s failed (%s); the pictures step will try again", dream_id, e)

    def session(self, dream_id: uuid.UUID, expected_hours: float | None = None) -> Session:
        """Opens (or returns) the Short's pod. Raises when no provider can serve (the step then waits for the owner).
        expected_hours: a caller that knows its own rental length (the Video path: its id is a Video's, the pick is Auto)."""
        hours = self.rental_hours(dream_id) if expected_hours is None else expected_hours
        r = self.broker.acquire(dream_id, "video.i2v.dream", self.pod_image(), boot.boot_command(), self.pod_env(), self.volume_id,
                                self.pick(dream_id) if expected_hours is None else AUTO, hours)
        return Session(r.instance.endpoint, self.ready_timeout_minutes * 60)

    def hold(self, video_id: uuid.UUID) -> None:
        with self._lock:
            self._external_holds.add(video_id)

    def unhold(self, video_id: uuid.UUID) -> None:
        with self._lock:
            self._external_holds.discard(video_id)

    def pod_env(self) -> dict[str, str]:
        return boot.boot_env(self._worker(), self.port, "/workspace/data", VOLUME_MOUNT if self.volume_id else None,
                            WARM_ROOT if self.warm_image else None)

    def pod_image(self) -> str:
        return self.warm_image if self.warm_image is not None else self.image

    def pick(self, dream_id: uuid.UUID) -> Pick:
        rows = self.db.rows("SELECT gpu_mode, gpu_choice FROM dream_short WHERE id = %(d)s", {"d": dream_id})
        return AUTO if not rows else Pick(rows[0]["gpu_mode"], rows[0]["gpu_choice"])

    def set_pick(self, dream_id: uuid.UUID, mode: str, choice: str | None) -> None:
        """The owner sets Auto or Manual for a Short. A manual pick must be one of the options offered now, and is remembered as a
        preference (what he chose, and what was cheapest) so Auto can learn from it."""
        manual = (mode or "").lower() == "manual"
        if not manual and (mode or "").lower() != "auto":
            raise ValueError("mode must be auto or manual")
        if manual and (choice is None or not choice.strip()):
            raise PickRefusedError("manual mode needs a choice (an option key from the list)", False)
        if manual:
            type_key = pick_key(choice)
            opts = self.broker.options()
            chosen = next((o for o in opts if o.key is not None and o.key == type_key), None)
            if chosen is None:
                raise PickRefusedError(f"not an offered GPU option now: {choice} (GET /api/gpu/broker/options lists what can be picked)", False)
            if not chosen.available:
                raise PickRefusedError(f"{type_key} cannot be rented now: {chosen.why}", True)
            avail = [o for o in opts if o.available]
            cheapest = avail[0].key if avail else type_key
            self.prefs.record_choice(dream_id, type_key, cheapest, {"shortId": str(dream_id),
                                                                    "options": [f"{o.key} ${o.hourly_usd}" for o in avail]})
        self.db.execute("UPDATE dream_short SET gpu_mode = %(m)s, gpu_choice = %(c)s, updated_at = now() WHERE id = %(d)s",
                        {"m": "manual" if manual else "auto", "c": pick_key(choice) if manual else None, "d": dream_id})

    def heartbeat(self, dream_id: uuid.UUID) -> PodState:
        """Keeps the lease warm and says whether the pod still exists (the worker re-rents on GONE, stops on NONE)."""
        if not self._enabled:
            return PodState.OPEN
        self.broker.heartbeat(dream_id)
        return self.broker.pod_state(dream_id)

    def replace(self, dream_id: uuid.UUID, unready_url: str | None = None, why: str | None = None) -> Session:
        """A new pod for a running GPU step whose pod vanished. Only when the Short still holds a rental the provider reports gone."""
        if not self._enabled:
            raise RuntimeError("The GPU broker is off (projectv.dream.gpu-provider=modal)")
        if self.broker.pod_state(dream_id) == PodState.NONE:
            raise RuntimeError(f"Short {dream_id} has no open GPU rental (released); not renting again")
        if unready_url and unready_url.strip():
            self.broker.retire_unready(unready_url, why or "not ready in time")
        log.warning("GPU pod of Short %s vanished or never got ready during a GPU step; renting a replacement", dream_id)
        return self.session(dream_id)  # acquire() releases and books the gone rental, then rents again

    def release(self, dream_id: uuid.UUID, why: str) -> None:
        if self._enabled:
            self.broker.release(dream_id, why)

    def release_all_unless_held(self, why: str) -> None:
        """Ends the shared pod for everyone, unless a Short using it has GPU power Manual (the owner keeps it on)."""
        if not self._enabled:
            return
        with self._lock:
            held = set(self._external_holds)
        if any(i in held or self.hold_manual(i) for i in self.broker.open_short_ids()):
            return
        log.info("GPU: ending the shared pod (%s)", why)
        self.broker.release_all(why)

    # ---- GPU power (owner, 20 Nov 2026)

    def hold_manual(self, dream_id: uuid.UUID) -> bool:
        rows = self.db.rows("SELECT gpu_hold FROM dream_short WHERE id = %(d)s", {"d": dream_id})
        return (rows[0]["gpu_hold"] if rows else "auto") == "manual"

    def idle_minutes(self, dream_id: uuid.UUID) -> int:
        rows = self.db.rows("SELECT gpu_idle_minutes FROM dream_short WHERE id = %(d)s", {"d": dream_id})
        return 10 if not rows or rows[0]["gpu_idle_minutes"] is None else int(rows[0]["gpu_idle_minutes"])

    def set_hold(self, dream_id: uuid.UUID, hold: str, idle_minutes: int | None) -> None:
        if hold is None or hold.lower() not in ("auto", "manual"):
            raise ValueError("hold must be auto or manual")
        self.db.execute("UPDATE dream_short SET gpu_hold = %(h)s, gpu_idle_minutes = COALESCE(%(i)s, gpu_idle_minutes), updated_at = now() "
                        "WHERE id = %(d)s", {"h": hold.lower(), "i": None if idle_minutes is None else max(5, min(30, idle_minutes)),
                                             "d": dream_id})

    def turn_off(self, dream_id: uuid.UUID) -> None:
        """The owner's "Turn off GPU": ends the ONE shared pod now, for every Short that uses it."""
        if not self._enabled or not self.is_open(dream_id):
            raise RuntimeError("This Short is not using a rented GPU, so there is nothing to turn off")
        self.broker.release_all("turned off by the owner")

    def off_in_seconds(self, dream_id: uuid.UUID, now: datetime) -> int | None:
        """Seconds until a Manual-hold pod turns itself off for being idle; None when this Short holds no pod or is on Auto."""
        if not self._enabled or not self.hold_manual(dream_id):
            return None
        last = self.broker.last_activity(dream_id)
        if last is None:
            return None
        return max(0, self.idle_minutes(dream_id) * 60 - int((now - last).total_seconds()))

    def release_idle_manual(self, now: datetime) -> None:
        """A Manual-hold pod that nothing used for its idle minutes is turned off (rule 8). The periodic caller is S7 wiring (every 30 s)."""
        if not self._enabled:
            return
        for i in self.broker.open_short_ids():
            try:
                left = self.off_in_seconds(i, now)  # None when on Auto: never touched here
                if left is not None and left == 0:
                    minutes = self.idle_minutes(i)
                    log.info("GPU power Manual: pod of Short %s idle for %s min, turning it off", i, minutes)
                    self.broker.release(i, f"manual hold: idle for {minutes} min")
            except Exception as e:
                log.warning("GPU idle check for Short %s failed: %s", i, e)

    # ---- provider checks (no rental unless the owner approved the canary)

    def preflight(self, provider: str):
        return self.broker.preflight(provider, self.pod_image(), boot.boot_command(), self.pod_env(), self.volume_id)

    def canary(self, provider: str, max_usd: float) -> dict[str, Any]:
        """The owner-approved canary: one tiny rental of the provider's cheapest GPU that must pass the worker's /health, then ends."""
        return self.broker.canary(provider, self.pod_image(), boot.boot_command(), self.pod_env(), self.volume_id, worker_health,
                                  20 * 60, 10_000, max_usd)

    def _worker(self) -> bytes:
        tries = [Path(self.worker_file), Path("workers/pod/server.py"), Path("../workers/pod/server.py"),
                 Path(__file__).resolve().parents[2] / "workers" / "pod" / "server.py"]
        for p in tries:
            if p.is_file():
                return p.read_bytes()
        raise RuntimeError(f"GPU pod worker file not found: {self.worker_file} (projectv.gpu.pod-worker-file)")


def worker_health(endpoint: str) -> str | None:
    """None = the worker answered /health ready; else why not. The address carries the token as http://worker:TOKEN@host:port."""
    import httpx

    try:
        u = urlparse(endpoint)
        token = u.password
        host = u.hostname or ""
        url = f"{u.scheme}://{host}{':' + str(u.port) if u.port else ''}/health"
        r = httpx.get(url, headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=10)
        body = r.text.replace(" ", "")
        return None if r.status_code == 200 and '"ready":true' in body else f"HTTP {r.status_code} {r.text[:120]}"
    except Exception as e:
        return type(e).__name__
