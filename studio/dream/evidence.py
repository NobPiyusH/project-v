"""The learning loop's memory, phase 1: what the owner decided, what failed, what each finished Short measured, what each paid
step was estimated to cost against what it did cost, and real GPU timings. Port of DreamEvidence."""

from __future__ import annotations

import json
import uuid
from typing import Any

from studio.agents.jfmt import fixed, jround
from studio.db import Db
from studio.dream.timeline import median

MIN_SAMPLES = 2  # fewer clips than this and the config's number stands
RECENT = 30  # only the most recent clips count: the GPU, the model or Modal can change


def _round(x: float) -> float:
    return jround(x * 100) / 100.0


class DreamEvidence:
    def __init__(self, db: Db):
        self.db = db

    def event(self, dream_id: uuid.UUID, step: str | None, kind: str, shot_ids: list[str] | None, notes: str | None,
              data: dict[str, Any] | None) -> None:
        self.db.execute(
            "INSERT INTO dream_event (id, dream_short_id, step, kind, shot_ids, notes, data) "
            "VALUES (%(id)s, %(d)s, %(step)s, %(kind)s, CAST(%(shots)s AS jsonb), %(notes)s, CAST(%(data)s AS jsonb))",
            {"id": uuid.uuid4(), "d": dream_id, "step": step, "kind": kind,
             "shots": json.dumps(shot_ids) if shot_ids else None, "notes": notes.strip() if notes and notes.strip() else None,
             "data": json.dumps(data) if data else None})

    def cost_check(self, dream_id: uuid.UUID, step: str, estimate_inr: float, actual_inr: float) -> None:
        self.event(dream_id, step, "cost", None, None, {"estimateInr": _round(estimate_inr), "actualInr": _round(actual_inr),
                                                        "ratio": _round(actual_inr / estimate_inr) if estimate_inr > 0 else 0})

    def events(self, dream_id: uuid.UUID) -> list[dict[str, Any]]:
        return self.db.rows("SELECT step, kind, shot_ids::text AS shot_ids, notes, data::text AS data, created_at "
                            "FROM dream_event WHERE dream_short_id = %(d)s ORDER BY created_at", {"d": dream_id})

    def save_metrics(self, dream_id: uuid.UUID, asset_id: uuid.UUID, look: str, metrics: dict[str, float] | None) -> None:
        if not metrics:
            return
        self.db.execute(
            "INSERT INTO dream_metric (asset_id, dream_short_id, look, metrics) VALUES (%(a)s, %(d)s, %(l)s, CAST(%(m)s AS jsonb)) "
            "ON CONFLICT (asset_id) DO UPDATE SET metrics = EXCLUDED.metrics",
            {"a": asset_id, "d": dream_id, "l": look, "m": json.dumps(metrics)})

    def metrics(self, dream_id: uuid.UUID) -> list[dict[str, Any]]:
        return self.db.rows("SELECT asset_id, look, metrics::text AS metrics, created_at FROM dream_metric "
                            "WHERE dream_short_id = %(d)s ORDER BY created_at DESC", {"d": dream_id})

    def gpu_timing(self, dream_id: uuid.UUID, kind: str, frames: int, seconds: float, first: bool) -> None:
        if seconds <= 0 or frames <= 0:
            return
        self.db.execute("INSERT INTO gpu_timing (id, kind, frames, seconds, first_in_batch, dream_short_id) "
                        "VALUES (%(id)s, %(k)s, %(f)s, %(s)s, %(first)s, %(d)s)",
                        {"id": uuid.uuid4(), "k": kind, "f": frames, "s": seconds, "first": first, "d": dream_id})

    def recalibrate(self, kind: str) -> list[str]:
        """Recompute <kind>.per81 (median GPU seconds per 81 frames, warm clips only) and <kind>.first_extra (median extra seconds
        of a batch's first clip) from the most recent clips. Returns what changed, for the log."""
        changed: list[str] = []
        rows = self.db.rows("SELECT frames, seconds, first_in_batch FROM gpu_timing WHERE kind = %(k)s ORDER BY created_at DESC LIMIT %(n)s",
                            {"k": kind, "n": RECENT})
        warm = [float(r["seconds"]) * 81 / r["frames"] for r in rows if not r["first_in_batch"]]
        if len(warm) < MIN_SAMPLES:
            return changed
        per81 = median(warm)
        self._save(f"{kind}.per81", per81, len(warm), f"median of the last {len(warm)} warm clips")
        changed.append(f"{kind}: {fixed(per81, 0)} s per 81 frames ({len(warm)} clips)")
        extra = [max(0.0, float(r["seconds"]) - per81 * r["frames"] / 81) for r in rows if r["first_in_batch"]]
        if extra:
            e = median(extra)
            self._save(f"{kind}.first_extra", e, len(extra), f"median warm-up of the last {len(extra)} first clips")
            changed.append(f"{kind}: first clip +{fixed(e, 0)} s ({len(extra)} batches)")
        return changed

    def calibrated(self, key: str) -> float | None:
        rows = self.db.rows("SELECT value FROM calibration WHERE key = %(k)s", {"k": key})
        return None if not rows else float(rows[0]["value"])

    def calibration(self) -> list[dict[str, Any]]:
        return self.db.rows("SELECT key, value, samples, source, updated_at FROM calibration ORDER BY key")

    def _save(self, key: str, value: float, samples: int, source: str) -> None:
        self.db.execute("INSERT INTO calibration (key, value, samples, source) VALUES (%(k)s, %(v)s, %(n)s, %(s)s) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, samples = EXCLUDED.samples, "
                        "source = EXCLUDED.source, updated_at = now()", {"k": key, "v": value, "n": samples, "s": source})
