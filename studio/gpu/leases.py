"""A lease is the studio's claim on a pod it started (port of PodLeases): written right after the pod is created, its
heartbeat extended while a job runs, released when the job ends. The watchdog terminates studio pods with no lease, a stale
heartbeat or a released lease. Tables gpu_pod_lease (V31) and gpu_pod_user (V38)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from studio.db import Db


@dataclass
class Lease:
    pod_id: str
    purpose: str | None
    heartbeat_at: datetime | None
    released_at: datetime | None


@dataclass
class OpenLease:
    """A lease still held, with what the broker needs to rebuild its rental after a restart."""

    pod_id: str
    provider: str
    name: str
    dream_short_id: uuid.UUID | None
    hourly_usd: float
    gpu: str | None
    created_at: datetime


class PgPodLeases:
    def __init__(self, db: Db):
        self._db = db

    def open(self, provider: str, pod_id: str, name: str, purpose: str | None, dream_short_id: uuid.UUID | None = None,
             hourly_usd: float | None = None, gpu: str | None = None) -> None:
        """Opens (or re-opens) the lease; with a Short, also remembers the Short, live rate and GPU."""
        if dream_short_id is None and hourly_usd is None and gpu is None:
            self._db.execute(
                """
                INSERT INTO gpu_pod_lease (pod_id, provider, name, purpose) VALUES (%(id)s, %(p)s, %(n)s, %(purpose)s)
                ON CONFLICT (pod_id) DO UPDATE SET heartbeat_at = now(), released_at = NULL
                """,
                {"id": pod_id, "p": provider, "n": name, "purpose": purpose},
            )
            return
        self._db.execute(
            """
            INSERT INTO gpu_pod_lease (pod_id, provider, name, purpose, dream_short_id, hourly_usd, gpu)
            VALUES (%(id)s, %(p)s, %(n)s, %(purpose)s, %(d)s, %(h)s, %(g)s)
            ON CONFLICT (pod_id) DO UPDATE SET heartbeat_at = now(), released_at = NULL
            """,
            {"id": pod_id, "p": provider, "n": name, "purpose": purpose, "d": dream_short_id, "h": hourly_usd, "g": gpu},
        )

    def open_for_shorts(self) -> list[OpenLease]:
        """Unreleased leases that belong to a Dream Short, oldest first."""
        rows = self._db.rows(
            """
            SELECT pod_id, provider, name, dream_short_id, COALESCE(hourly_usd, 0) AS hourly_usd, gpu, created_at
            FROM gpu_pod_lease WHERE released_at IS NULL AND dream_short_id IS NOT NULL ORDER BY created_at
            """
        )
        return [OpenLease(r["pod_id"], r["provider"], r["name"], r["dream_short_id"], float(r["hourly_usd"]), r["gpu"], r["created_at"])
                for r in rows]

    def attach(self, pod_id: str, dream_short_id: uuid.UUID) -> None:
        self._db.execute(
            "INSERT INTO gpu_pod_user (pod_id, dream_short_id) VALUES (%(p)s, %(d)s) ON CONFLICT DO NOTHING",
            {"p": pod_id, "d": dream_short_id},
        )

    def detach(self, pod_id: str, dream_short_id: uuid.UUID) -> None:
        self._db.execute("DELETE FROM gpu_pod_user WHERE pod_id = %(p)s AND dream_short_id = %(d)s",
                         {"p": pod_id, "d": dream_short_id})

    def attached(self, pod_id: str) -> list[uuid.UUID]:
        rows = self._db.rows("SELECT dream_short_id FROM gpu_pod_user WHERE pod_id = %(p)s ORDER BY attached_at", {"p": pod_id})
        return [r["dream_short_id"] for r in rows]

    def heartbeat(self, pod_id: str) -> None:
        self._db.execute("UPDATE gpu_pod_lease SET heartbeat_at = now() WHERE pod_id = %(id)s AND released_at IS NULL", {"id": pod_id})

    def release(self, pod_id: str) -> None:
        self._db.execute("UPDATE gpu_pod_lease SET released_at = now() WHERE pod_id = %(id)s AND released_at IS NULL", {"id": pod_id})

    def find(self, pod_id: str) -> Lease | None:
        rows = self._db.rows("SELECT pod_id, purpose, heartbeat_at, released_at FROM gpu_pod_lease WHERE pod_id = %(id)s", {"id": pod_id})
        return Lease(rows[0]["pod_id"], rows[0]["purpose"], rows[0]["heartbeat_at"], rows[0]["released_at"]) if rows else None

    def recent(self, limit: int) -> list[dict[str, Any]]:
        return self._db.rows(
            """
            SELECT pod_id, provider, name, purpose, created_at, heartbeat_at, released_at
            FROM gpu_pod_lease ORDER BY created_at DESC LIMIT %(n)s
            """,
            {"n": limit},
        )
