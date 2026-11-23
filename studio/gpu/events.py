"""What the watchdog leaves behind when it terminates a pod: a CostEntry (rule 6) and a gpu_pod_event row. Port of PodEvents."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from studio.agents.jfmt import fixed
from studio.db import Db
from studio.gpu.pods import Pod
from studio.ledger import CostLedger, Pricing
from studio.ledger.pricing import dec

_FOUR = Decimal("0.0001")


class PodEvents(Protocol):
    def terminated(self, provider: str, pod: Pod, reason: str, hours: float, estimated_usd: float) -> None:
        """hours: how long the pod had been billing; estimated_usd: hours x the live hourly rate (the provider's bill is the truth)."""
        ...

    def last_orphan_booking(self, pod_id: str) -> datetime | None:
        """The last time an orphan's running cost was booked by a dry-run sweep (so the real kill books only the rest)."""
        ...

    def orphan_billed(self, provider: str, pod: Pod, reason: str, hours: float, estimated_usd: float) -> None:
        """A dry-run sweep found a studio pod that should be gone but is still billing: book what it has cost since last time."""
        ...


class PgPodEvents:
    def __init__(self, db: Db, ledger: CostLedger, pricing: Pricing):
        self._db, self._ledger, self._pricing = db, ledger, pricing

    def _book(self, kind: str, note: str, provider: str, pod: Pod, reason: str, hours: float, usd: float) -> None:
        inr = dec(usd * self._pricing.fx()).quantize(_FOUR, rounding=ROUND_HALF_UP)
        self._ledger.record(None, provider, inr, dec(hours).quantize(_FOUR, rounding=ROUND_HALF_UP), "gpu_hour", True, note)
        data = {"hours": hours, "estimatedUsd": usd, "costPerHrUsd": pod.cost_per_hr_usd, "status": pod.status}
        self._db.execute(
            """
            INSERT INTO gpu_pod_event (provider, pod_id, name, kind, reason, data)
            VALUES (%(p)s, %(id)s, %(n)s, %(k)s, %(r)s, CAST(%(d)s AS jsonb))
            """,
            {"p": provider, "id": pod.id, "n": pod.name, "k": kind, "r": reason, "d": json.dumps(data)},
        )

    def terminated(self, provider, pod, reason, hours, estimated_usd) -> None:
        note = (f"GPU watchdog terminated pod {pod.id} ({pod.name}): {reason}; "
                f"${fixed(pod.cost_per_hr_usd, 4)}/h live rate")
        self._book("terminated", note, provider, pod, reason, hours, estimated_usd)

    def last_orphan_booking(self, pod_id: str) -> datetime | None:
        rows = self._db.rows("SELECT max(created_at) AS t FROM gpu_pod_event WHERE pod_id = %(id)s AND kind = 'orphan_billed'", {"id": pod_id})
        return rows[0]["t"] if rows else None

    def orphan_billed(self, provider, pod, reason, hours, estimated_usd) -> None:
        note = (f"GPU watchdog (dry-run): pod {pod.id} ({pod.name}) still billing, should be terminated: {reason}; "
                f"${fixed(pod.cost_per_hr_usd, 4)}/h live rate")
        self._book("orphan_billed", note, provider, pod, reason, hours, estimated_usd)

    def recent(self, limit: int) -> list[dict[str, Any]]:
        return self._db.rows(
            """
            SELECT provider, pod_id, name, kind, reason, data::text AS data, created_at
            FROM gpu_pod_event ORDER BY created_at DESC LIMIT %(n)s
            """,
            {"n": limit},
        )
