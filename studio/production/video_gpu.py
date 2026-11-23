"""The Video path's door to the GPU broker. Port of VideoGpu.

Shares the Dream pod door (`PodGpu`: same pod worker, warm image, daily cap, per-rental estimate, Auto pick) instead of copying it. The Dream
package is ported in S6, so the door is a protocol here: tests use a fake, S6 supplies the real one. Off unless `provider-draft /
provider-final / provider-keyframes` is `broker`; the defaults (wan / omni / Gemini + Z-Image) are unchanged.

A Video id plays the role of the Short id: it is the lease key; the rental is booked on the Video's cost ledger when it ends. The pod is
let go right after each batch's save; a batch that failed leaves it to `release_idle`.
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

    def release(self, video_id: uuid.UUID, why: str) -> None: ...

    def last_activity(self, video_id: uuid.UUID) -> datetime | None: ...


def _norm(s: str | None) -> str:
    return "" if s is None else s.strip().lower()


class VideoGpu:
    def __init__(self, gpu: PodGpu, draft: str = "wan", final: str = "omni", keyframes: str = "default", idle_minutes: int = 5):
        self.gpu = gpu
        self._draft, self._final, self._keyframes = _norm(draft), _norm(final), _norm(keyframes)
        self.idle_minutes = max(2, idle_minutes)
        self._holds: dict[uuid.UUID, datetime] = {}
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
        equivalent = sum(j.num_frames * max(1.0, math.pow(j.width * j.height / REFERENCE_PIXELS, PIXEL_EXPONENT)) for j in jobs)
        return self.gpu.clip_seconds(video_id, math.ceil(equivalent))

    def picture_seconds(self, video_id: uuid.UUID, pictures: int) -> float:
        return self.gpu.picture_seconds(video_id, pictures)

    def inr(self, seconds: float) -> Decimal:
        """Rupees at the cheapest live price for this many pod seconds."""
        return self.gpu.inr(seconds)

    def session(self, video_id: uuid.UUID, pod_seconds: float) -> PodSession:
        """Opens (or joins) the shared pod for this Video. Raises when no provider can serve or the daily cap would be exceeded. The start
        runs in the background and survives an activity timeout: a call that waits `start_wait_seconds` without the pod being ready
        raises, and the Temporal retry joins the same start instead of renting a second pod."""
        self._require_broker()
        self.gpu.hold(video_id)
        with self._lock:
            self._holds[video_id] = datetime.now(UTC)
            start = self._starting.get(video_id)
            if start is None:
                start = self._starting[video_id] = self._pool.submit(self.gpu.session, video_id, pod_seconds / 3600.0)
        try:
            session = start.result(timeout=self.start_wait_seconds)
        except FutureTimeout as e:
            raise RuntimeError("The GPU pod is still starting (a start takes 8-18 min); this step retries and joins it") from e
        except Exception:
            with self._lock:
                if self._starting.get(video_id) is start:
                    del self._starting[video_id]
                self._holds.pop(video_id, None)
            self.gpu.unhold(video_id)
            raise
        with self._lock:
            if self._starting.get(video_id) is start:
                del self._starting[video_id]
            self._holds[video_id] = datetime.now(UTC)
        return session

    def release(self, video_id: uuid.UUID, why: str) -> None:
        """Lets go of the pod (it ends when nobody else uses it) and books this Video's share on its ledger. Safe to repeat."""
        with self._lock:
            if self._holds.pop(video_id, None) is None:
                return
        self.gpu.unhold(video_id)
        self.gpu.release(video_id, why)

    def release_idle(self, now: datetime) -> None:
        """Run every minute: a Video that holds the pod but has not used it for a while (a failed batch waiting for Retry) lets go."""
        with self._lock:
            held = dict(self._holds)
            starting = dict(self._starting)
        for vid, since in held.items():
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
