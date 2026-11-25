"""The Video path's door to the GPU broker. Port of VideoGpu.

Shares the Dream pod door (`PodGpu`: same pod worker, warm image, daily cap, per-rental estimate, Auto pick) instead of copying it. The Dream
package is ported in S6, so the door is a protocol here: tests use a fake, S6 supplies the real one. Off unless `provider-draft /
provider-final / provider-keyframes` is `broker`; the defaults (wan / omni / Gemini + Z-Image) are unchanged.

A Video id plays the role of the Short id: it is the lease key; the rental is booked on the Video's cost ledger when it ends. The pod is
let go right after each batch's save; a batch that failed leaves it to `release_idle`.

One-pod mode (VIDEO_ONE_POD, off by default, AUTO stages only): the pod rented for KEYFRAMES is sized and stocked for the keyframes AND the draft
clips (`span_seconds`, Fun Camera prefetch), kept after the keyframes are saved (`keep`) and reused by DRAFT_GEN of the same video (`session`
consumes the keep). A kept pod is never left alone: the workflow lets it go (`release`) before any human wait, failure, cancel or the end of
DRAFT_GEN, and `release_stale_holds` (every 30 s) ends one whose next stage has not started within `hold_idle_minutes`.
"""

from __future__ import annotations

import logging
import math
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from studio.media.types import ClipJob

log = logging.getLogger(__name__)

POD = "gpu_pod"  # the routing/provider id of the rented-pod route (config/providers.yaml)
DRAFT = "draft"
FINAL = "final"
REFERENCE_PIXELS = 480.0 * 832.0  # the size the pod's seconds-per-81-frames figure was measured at (Dream Shorts); scaling is an estimate
CAMERA_SECONDS_PER_81 = 220.0  # Fun Camera 720x1280, 81 frames on a 4090: about 3.5 min a beat (motion block 25 Nov 2026; an estimate, not a quote)
CAMERA_SETUP_SECONDS = 360.0  # the camera job fetches the Fun Camera experts, LoRAs and text encoder (about 38 GB) before its first beat
PIXEL_EXPONENT = 1.75  # Modal H100 measured 30 s (480p) vs 133 s (720p) per 81 frames: 2.3x the pixels took 4.4x the time. Not measured on a pod.


@dataclass(frozen=True)
class PodSession:
    url: str
    ready_timeout_seconds: int


class PodGpu(Protocol):
    def enabled(self) -> bool: ...

    def clip_seconds(self, video_id: uuid.UUID, equivalent_frames: int) -> float: ...

    def picture_seconds(self, video_id: uuid.UUID, pictures: int) -> float: ...

    def inr(self, seconds: float) -> Decimal: ...

    def hold(self, video_id: uuid.UUID) -> None: ...

    def unhold(self, video_id: uuid.UUID) -> None: ...

    def session(self, video_id: uuid.UUID, hours: float) -> PodSession: ...

    def heartbeat(self, video_id: uuid.UUID) -> object: ...  # a PodState: .name == "OPEN" while the pod is there

    def today(self) -> dict: ...  # {"spentUsd", "capUsd", ...}: booked rentals + the running pod vs the daily cap

    def release(self, video_id: uuid.UUID, why: str) -> None: ...

    def last_activity(self, video_id: uuid.UUID) -> datetime | None: ...


def _norm(s: str | None) -> str:
    return "" if s is None else s.strip().lower()


class VideoGpu:
    def __init__(self, gpu: PodGpu, draft: str = "wan", final: str = "omni", keyframes: str = "default", idle_minutes: int = 5,
                 one_pod: bool = False, hold_idle_minutes: int = 5, camera_prefetch: bool = True):
        self.gpu = gpu
        self.one_pod = one_pod
        self.hold_idle_minutes = max(1, hold_idle_minutes)
        self.camera_prefetch = camera_prefetch  # the pod also downloads the Fun Camera group while the keyframes run
        self._draft, self._final, self._keyframes = _norm(draft), _norm(final), _norm(keyframes)
        self.idle_minutes = max(2, idle_minutes)
        self._holds: dict[uuid.UUID, datetime] = {}
        self._span: set[uuid.UUID] = set()  # videos whose pod was rented for the keyframes AND the draft clips (only these may be kept)
        self._kept: dict[uuid.UUID, datetime] = {}  # videos whose pod waits, on purpose, for their next stage: since when
        self._starting: dict[uuid.UUID, Future] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(thread_name_prefix="video-gpu-start")
        # How long one call waits for the pod; kept under the 5-minute activity limit. A test can shorten it.
        self.start_wait_seconds = 240.0

    def clips(self, kind: str) -> bool:
        """Clips of this kind (draft | final) go to the broker's pod."""
        return (self._draft if kind == DRAFT else self._final) == "broker"

    def keyframes(self) -> bool:
        """Keyframes (FLUX.2 klein) go to the broker's pod."""
        return self._keyframes == "broker"

    def _require_broker(self) -> None:
        if not self.gpu.enabled():
            raise RuntimeError("projectv.video.provider-*=broker needs the GPU broker on: set projectv.dream.gpu-provider=broker")

    def clip_seconds(self, video_id: uuid.UUID, jobs: list[ClipJob]) -> float:
        """Pod seconds for these clips (config time per 81 frames; the pod start-up only when no pod is open). The configured seconds
        were measured at 480x832: scale up for larger clips so 720p is not under-estimated."""
        plain = [j for j in jobs if not j.camera]
        equivalent = sum(j.num_frames * max(1.0, math.pow(j.width * j.height / REFERENCE_PIXELS, PIXEL_EXPONENT)) for j in plain)
        seconds = self.gpu.clip_seconds(video_id, math.ceil(equivalent))
        cams = [j for j in jobs if j.camera]
        if cams:  # measured per beat, not scaled from the SVI figure
            seconds += CAMERA_SETUP_SECONDS + sum(CAMERA_SECONDS_PER_81 * j.num_frames / 81.0 for j in cams)
        return seconds

    def picture_seconds(self, video_id: uuid.UUID, pictures: int) -> float:
        return self.gpu.picture_seconds(video_id, pictures)

    def inr(self, seconds: float) -> Decimal:
        """Rupees at the cheapest live price for this many pod seconds."""
        return self.gpu.inr(seconds)

    def one_pod_ready(self) -> bool:
        """One-pod mode is on and both the keyframes and the draft clips go to the broker's pod (the only case where a pod can be reused)."""
        return self.one_pod and self.keyframes() and self.clips(DRAFT)

    def span_seconds(self, video_id: uuid.UUID, pictures: int, shots: int) -> float:
        """Pod seconds for the keyframes plus the draft clips of `shots` shots on ONE pod: one boot, the pictures, then the clips (an upper
        estimate: every clip counted as a camera clip when camera presets are on). The wallet reserve and the daily cap are checked on this."""
        pictures_s = self.gpu.picture_seconds(video_id, pictures)
        if self.camera_prefetch:
            return pictures_s + CAMERA_SETUP_SECONDS + shots * CAMERA_SECONDS_PER_81
        return pictures_s + self.gpu.clip_seconds(video_id, 81 * shots)

    def session(self, video_id: uuid.UUID, pod_seconds: float, span_seconds: float | None = None) -> PodSession:
        """Opens (or joins) the shared pod for this Video. Raises when no provider can serve or the daily cap would be exceeded. The start
        runs in the background and survives an activity timeout: a call that waits `start_wait_seconds` without the pod being ready
        raises, and the Temporal retry joins the same start instead of renting a second pod."""
        self._require_broker()
        with self._lock:
            was_kept = self._kept.pop(video_id, None) is not None  # the stage this pod waited for has started: it is in use again
        if was_kept:
            self._check_cap_while_kept(video_id)
        self.gpu.hold(video_id)
        with self._lock:
            self._holds[video_id] = datetime.now(UTC)
            start = self._starting.get(video_id)
            if start is None:
                start = self._starting[video_id] = self._pool.submit(self._open, video_id, pod_seconds, span_seconds)
        try:
            session = start.result(timeout=self.start_wait_seconds)
        except FutureTimeout as e:
            raise RuntimeError("The GPU pod is still starting (a start takes 8-18 min); this step retries and joins it") from e
        except Exception:
            with self._lock:
                if self._starting.get(video_id) is start:
                    del self._starting[video_id]
                self._holds.pop(video_id, None)
                self._span.discard(video_id)
            self.gpu.unhold(video_id)
            raise
        with self._lock:
            if self._starting.get(video_id) is start:
                del self._starting[video_id]
            self._holds[video_id] = datetime.now(UTC)
        return session

    def _open(self, video_id: uuid.UUID, pod_seconds: float, span_seconds: float | None) -> PodSession:
        """Rents the pod. In one-pod mode the first try is sized for the whole span and carries the Fun Camera group; if the broker refuses that
        (daily cap or wallet too small for the span), it tries the single-stage size and the pod is then NOT kept."""
        if span_seconds is not None and self.one_pod:
            try:
                s = self.gpu.session(video_id, span_seconds / 3600.0, prefetch=("camera",) if self.camera_prefetch else ())
                with self._lock:
                    self._span.add(video_id)
                return s
            except Exception as e:  # noqa: BLE001 - the plain single-stage rental below decides whether anything can be rented at all
                log.warning("Video %s: one-pod rental refused (%s); trying a single-stage rental", video_id, e)
        return self.gpu.session(video_id, pod_seconds / 3600.0)

    def _check_cap_while_kept(self, video_id: uuid.UUID) -> None:
        """The stage after a hold is about to reuse the pod: if today's GPU spend (booked + this running pod) has reached the daily cap, end
        the pod now and refuse (the step waits for the owner), rather than run on past the cap."""
        try:
            t = self.gpu.today()
            cap, spent = float(t.get("capUsd") or 0), float(t.get("spentUsd") or 0)
        except Exception as e:  # noqa: BLE001 - an unreadable figure must not strand the stage; the lease, kill-timer and watchdog still cover the pod
            log.warning("Video %s: could not read the daily GPU figures (%s); carrying on", video_id, e)
            return
        if cap > 0 and spent >= cap:
            self.release(video_id, "daily GPU cap reached while the pod was held")
            raise RuntimeError(f"Daily GPU cap reached (${spent:.2f} of ${cap:.2f}); the held pod was ended and nothing more is rented today."
                               " Press Retry after 00:00 UTC, or raise pricing.gpuBroker.dailyCapUsd in config/providers.yaml.")

    def keep(self, video_id: uuid.UUID) -> bool:
        """The keyframes are saved and the next stage will reuse this pod: keep it. True only in one-pod mode, for a pod rented for the whole
        span that the provider still has; the lease is touched so the watchdog sees it alive. False: the caller must release it."""
        if not self.one_pod:
            return False
        with self._lock:
            if video_id not in self._holds or video_id not in self._span:
                return False
        try:
            state = self.gpu.heartbeat(video_id)
        except Exception as e:  # noqa: BLE001
            log.warning("Video %s: pod not kept, its state could not be read (%s)", video_id, e)
            return False
        if getattr(state, "name", state) != "OPEN":
            return False
        now = datetime.now(UTC)
        with self._lock:
            self._kept[video_id] = now
            self._holds[video_id] = now
        log.info("Video %s keeps its GPU pod for the next stage (ends by itself after %d min if that stage does not start)",
                 video_id, self.hold_idle_minutes)
        return True

    def touch(self, video_id: uuid.UUID) -> bool:
        """A stage between the keyframes and the clips has started: the hold's idle clock restarts and the lease is touched. True if held."""
        with self._lock:
            if video_id not in self._kept:
                return False
        try:
            self.gpu.heartbeat(video_id)
        except Exception as e:  # noqa: BLE001
            log.warning("Video %s: lease touch failed (%s)", video_id, e)
        with self._lock:
            if video_id not in self._kept:
                return False
            self._kept[video_id] = datetime.now(UTC)
        return True

    def release_stale_holds(self, now: datetime) -> None:
        """Run every 30 s: a pod held for a next stage that did not start within `hold_idle_minutes` is ended (workflow stuck, worker offline,
        owner paused the video, control plane restarted)."""
        with self._lock:
            kept = dict(self._kept)
        for vid, since in kept.items():
            try:
                if (now - since).total_seconds() >= self.hold_idle_minutes * 60:
                    log.warning("Video %s: its held GPU pod waited %d min for the next stage: ending it", vid, self.hold_idle_minutes)
                    self.release(vid, f"one-pod hold: next stage did not start within {self.hold_idle_minutes} min")
            except Exception as e:  # noqa: BLE001 - one video's failed release must not stop the others
                log.warning("Releasing the held pod of Video %s failed: %s", vid, e)

    def release(self, video_id: uuid.UUID, why: str) -> None:
        """Lets go of the pod (it ends when nobody else uses it) and books this Video's share on its ledger. Safe to repeat."""
        with self._lock:
            self._kept.pop(video_id, None)
            self._span.discard(video_id)
            if self._holds.pop(video_id, None) is None:
                return
        self.gpu.unhold(video_id)
        self.gpu.release(video_id, why)

    def release_idle(self, now: datetime) -> None:
        """Run every minute: a Video that holds the pod but has not used it for a while (a failed batch waiting for Retry) lets go."""
        with self._lock:
            held = dict(self._holds)
            starting = dict(self._starting)
            kept = set(self._kept)
        for vid, since in held.items():
            if vid in kept:
                continue  # a deliberate hold: release_stale_holds owns it
            in_progress = starting.get(vid)
            if in_progress is not None and not in_progress.done():
                continue  # a pod start in progress is not idle (it would end the rental before it is ready)
            try:
                last = self.gpu.last_activity(vid) or since
                if (now - last).total_seconds() // 60 >= self.idle_minutes:
                    log.info("Video %s holds the GPU pod but used it for %d min: ending its share", vid, self.idle_minutes)
                    self.release(vid, f"video GPU batch idle for {self.idle_minutes} min")
            except Exception as e:  # noqa: BLE001 - one video's failed check must not stop the others
                log.warning("GPU idle check for Video %s failed: %s", vid, e)
