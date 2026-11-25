"""Read-only cost view of one video (or Dream Short): what it spent per stage, per provider and per pod, against its cap, and what a
second of the finished video cost. Built only from CostEntry rows (CLAUDE.md rule 6), so failed attempts and aborted pods are in it.

cost_entry has no stage column. A row belongs to the stage whose stage_run window (started_at .. finished_at) holds its time; a pod
rental is booked when the pod ends, so it is its own line, "GPU_RENTAL", split out by pod id instead of landing in whatever stage
happened to be running at that moment. Anything else outside every window is "OTHER".
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from studio.db import Db
from studio.ledger.budget_guard import BudgetGuard

_POD = re.compile(r"GPU rental (\S+)")
RENTAL, OTHER = "GPU_RENTAL", "OTHER"


def _add(into: dict[str, Any], key: str, row: dict[str, Any]) -> None:
    e = into.setdefault(key, {"key": key, "amountInr": Decimal(0), "entries": 0, "estimated": 0})
    e["amountInr"] += Decimal(row["amount_inr"])
    e["entries"] += 1
    e["estimated"] += 1 if row["estimated"] else 0


def stage_of(row: dict[str, Any], runs: list[dict[str, Any]]) -> str:
    note = row.get("note") or ""
    if note.startswith("GPU rental ") or note.startswith("canary rental "):
        return RENTAL
    ts: datetime = row["ts"]
    best: str | None = None
    for r in runs:
        end = r["finished_at"] or datetime.now(UTC)
        if r["started_at"] <= ts <= end:
            best = r["stage"]  # runs come oldest first: the latest stage that holds the time wins
    return best or OTHER


class CostView:
    def __init__(self, db: Db, budget: BudgetGuard):
        self._db, self._budget = db, budget

    def for_video(self, video_id: uuid.UUID) -> dict[str, Any]:
        entries = self._db.rows(
            """
            SELECT provider_id, amount_inr, estimated, note, ts FROM cost_entry
            WHERE video_id = %(v)s OR dream_short_id = %(v)s ORDER BY ts
            """,
            {"v": video_id},
        )
        runs = self._db.rows(
            "SELECT stage, started_at, finished_at FROM stage_run WHERE video_id = %(v)s ORDER BY started_at, attempt", {"v": video_id}
        )
        by_stage: dict[str, Any] = {}
        by_provider: dict[str, Any] = {}
        by_pod: dict[str, Any] = {}
        for r in entries:
            stage = stage_of(r, runs)
            _add(by_stage, stage, r)
            _add(by_provider, r["provider_id"], r)
            m = _POD.match(r.get("note") or "")
            if m:
                _add(by_pod, m.group(1), r)
        total = sum((Decimal(r["amount_inr"]) for r in entries), Decimal(0))
        seconds = self._finished_seconds(video_id)
        budget = self._budget.video_budget(video_id)
        return {
            "videoId": video_id,
            "totalInr": total,
            "capInr": budget["capInr"],
            "remainingInr": budget["remainingInr"],
            "overCap": budget["capInr"] is not None and total > budget["capInr"],
            "finishedSeconds": seconds,
            "costPerSecondInr": (total / Decimal(str(seconds))).quantize(Decimal("0.0001")) if seconds else None,
            "perStage": list(by_stage.values()),
            "perProvider": sorted(by_provider.values(), key=lambda e: -e["amountInr"]),
            "perPod": sorted(by_pod.values(), key=lambda e: -e["amountInr"]),
        }

    def _finished_seconds(self, video_id: uuid.UUID) -> float | None:
        rows = self._db.rows(
            """
            SELECT a.duration_sec FROM asset a
            WHERE a.id = (SELECT master_asset_id FROM video WHERE id = %(v)s)
               OR a.id = (SELECT final_asset_id FROM dream_short WHERE id = %(v)s)
            """,
            {"v": video_id},
        )
        d = rows[0]["duration_sec"] if rows else None
        return float(d) if d is not None and float(d) > 0 else None
