"""The periodic jobs Java ran with @Scheduled: the shared pod's "nobody waits" check (every 30 s), the Manual-hold idle release (every 30 s) and
the autopilot tick (every minute). Each job runs on its own daemon thread; one bad round never stops the schedule (Java caught and logged too).

Started only when STUDIO_JOBS=true (default off): with Java still live, two control planes must never both release pods or start autopilot runs."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

log = logging.getLogger("studio.jobs")


class Periodic:
    def __init__(self, name: str, fn: Callable[[], None], every_seconds: float, initial_delay: float = 0):
        self.name, self.fn, self.every, self.initial = name, fn, every_seconds, initial_delay
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.runs = 0

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name=f"job-{self.name}", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        if self._stop.wait(self.initial):
            return
        while True:
            try:
                self.fn()
            except Exception as e:  # never let one bad round stop the schedule
                log.warning("%s failed: %s", self.name, e)
            self.runs += 1
            if self._stop.wait(self.every):
                return


def dream_jobs(ctx, initial_delay: float = 30, gpu_every: float = 30, autopilot_every: float = 60) -> list[Periodic]:
    """The three Java schedules. The GPU ones only exist when the broker route is on (otherwise there is no pod to release)."""
    jobs: list[Periodic] = []
    if ctx.gpu is not None and ctx.gpu.enabled():
        jobs.append(Periodic("gpu-nobody-waits", ctx.dreams.release_gpu_if_nobody_waits_scheduled, gpu_every, 2 * initial_delay))
        jobs.append(Periodic("gpu-idle-manual", lambda: ctx.gpu.release_idle_manual(datetime.now(UTC)), gpu_every, 2 * initial_delay))
    jobs.append(Periodic("autopilot-tick", ctx.autopilot.tick, autopilot_every, initial_delay))
    return jobs
