"""Spend ledger for rented pods: a HARD cap across every pod of one job (the owner approves a total, not a rate).

Each pod RESERVES its worst case (rate x deadline minutes) before it is rented; a reservation that would push the committed
total over the cap raises CapExceeded and nothing is rented. When a pod ends, close() replaces the reservation with the actual
cost (rate x minutes it ran). Process-safe: several shards share one JSON file, guarded by a lock file.
All figures are estimates from rate x minutes, not provider bills.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path


class CapExceeded(Exception):
    """The reservation would exceed the approved total."""


class Ledger:
    def __init__(self, path: str | Path, cap_usd: float, clock=time.time):
        self.path = Path(path)
        self.cap = float(cap_usd)
        self.clock = clock

    # --- storage -----------------------------------------------------------
    @contextlib.contextmanager
    def _lock(self, wait_s: float = 10.0):
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        end = self.clock() + wait_s
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                with contextlib.suppress(OSError):
                    if self.clock() - lock.stat().st_mtime > 30:  # stale lock from a crashed process
                        lock.unlink()
                        continue
                if self.clock() > end:
                    raise TimeoutError("ledger lock busy") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                lock.unlink()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"cap": self.cap, "pods": {}, "fixed": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # --- accounting --------------------------------------------------------
    @staticmethod
    def _committed(data: dict) -> float:
        pods = sum(p["actual"] if p.get("actual") is not None else p["ceiling"] for p in data["pods"].values())
        return pods + sum(f["usd"] for f in data["fixed"])

    def committed(self) -> float:
        return self._committed(self._load())

    def remaining(self) -> float:
        return round(self.cap - self.committed(), 4)

    def add_fixed(self, label: str, usd: float) -> None:
        """A cost that is not a pod (keyframes from a hosted API, for example)."""
        with self._lock():
            data = self._load()
            data["fixed"].append({"label": label, "usd": float(usd)})
            self._save(data)

    def max_minutes(self, rate_per_h: float, want_min: float) -> int:
        """Largest deadline (<= want_min) whose worst case still fits under the cap; 0 when nothing fits."""
        room = self.remaining()
        if rate_per_h <= 0 or room <= 0:
            return 0
        return int(min(want_min, room / rate_per_h * 60))

    def reserve(self, pod_id: str, rate_per_h: float, deadline_min: float, label: str = "") -> float:
        ceiling = round(rate_per_h * deadline_min / 60, 4)
        with self._lock():
            data = self._load()
            if self._committed(data) + ceiling > self.cap + 1e-9:
                raise CapExceeded(
                    f"reserving ${ceiling:.2f} for {pod_id} would pass the ${self.cap:.2f} cap (committed ${self._committed(data):.2f})"
                )
            data["pods"][pod_id] = {
                "rate": rate_per_h,
                "deadline_min": deadline_min,
                "ceiling": ceiling,
                "started": self.clock(),
                "actual": None,
                "label": label,
            }
            self._save(data)
        return ceiling

    def close(self, pod_id: str, minutes: float | None = None) -> float:
        """The pod ended: book what it really cost. minutes=None uses the time since reserve()."""
        with self._lock():
            data = self._load()
            p = data["pods"].get(pod_id)
            if p is None:
                return 0.0
            used = minutes if minutes is not None else (self.clock() - p["started"]) / 60
            p["actual"] = round(min(p["rate"] * used / 60, p["ceiling"] * 1.5), 4)  # never trust a runaway clock
            self._save(data)
            return p["actual"]

    def summary(self) -> str:
        data = self._load()
        lines = [f"cap ${self.cap:.2f}, committed ${self._committed(data):.2f}"]
        for pid, p in data["pods"].items():
            state = f"actual ${p['actual']:.2f}" if p.get("actual") is not None else f"reserved ${p['ceiling']:.2f}"
            lines.append(f"  pod {pid} {p.get('label', '')}: {state}")
        lines += [f"  fixed {f['label']}: ${f['usd']:.2f}" for f in data["fixed"]]
        return "\n".join(lines)
