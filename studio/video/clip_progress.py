"""Live state of clip batches for the dashboard: per-clip progress reported by the media worker, and the owner's Stop. Port of ClipProgress.

In memory on purpose: it is a view of work in flight (the clips themselves are saved as they finish). A control plane restart forgets a
pending Stop; the worker then simply finishes the batch.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Snapshot:
    clips: list[dict[str, Any]]
    finished: bool
    at: datetime


class ClipProgress:
    def __init__(self) -> None:
        self._progress: dict[uuid.UUID, Snapshot] = {}
        self._stops: set[uuid.UUID] = set()
        self._lock = threading.Lock()

    def report(self, video_id: uuid.UUID, clips: list[dict[str, Any]] | None, finished: bool) -> None:
        with self._lock:
            self._progress[video_id] = Snapshot(list(clips or []), finished, datetime.now(UTC))
            if finished:
                self._stops.discard(video_id)

    def get(self, video_id: uuid.UUID) -> Snapshot | None:
        return self._progress.get(video_id)

    def request_stop(self, video_id: uuid.UUID) -> None:
        with self._lock:
            self._stops.add(video_id)

    def stop_requested(self, video_id: uuid.UUID) -> bool:
        return video_id in self._stops

    def start_batch(self, video_id: uuid.UUID) -> None:
        """A new batch starts clean (an old Stop must not cancel it)."""
        with self._lock:
            self._stops.discard(video_id)
            self._progress.pop(video_id, None)
