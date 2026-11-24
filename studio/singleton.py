"""Run-once guards across processes: a Postgres advisory lock held on a connection of its own for as long as the holder lives.

The GPU watchdog and the periodic jobs (pod release checks, autopilot tick) must only ever run in ONE process: two watchdogs would both terminate
pods, two autopilot ticks would start two runs. Whoever takes the lock runs the loop; any other process just serves requests. A crashed holder
frees the lock by itself (its connection ends)."""

from __future__ import annotations

import logging

log = logging.getLogger("studio.singleton")

WATCHDOG = 7_650_002
JOBS = 7_650_003


class Singleton:
    def __init__(self, dsn: str, key: int, name: str):
        self.dsn, self.key, self.name = dsn, key, name
        self._conn = None

    def try_acquire(self) -> bool:
        """True when this process now holds the lock (or already did); False when another process does or the database cannot be reached."""
        if self._conn is not None:
            return True
        try:
            import psycopg

            conn = psycopg.connect(self.dsn, autocommit=True)
            if conn.execute("SELECT pg_try_advisory_lock(%s)", (self.key,)).fetchone()[0]:
                self._conn = conn
                return True
            conn.close()
            log.warning("%s is already running in another process: not starting it here", self.name)
        except Exception as e:
            log.warning("%s not started: cannot take its lock (%s)", self.name, e)
        return False

    def release(self) -> None:
        if self._conn is not None:
            self._conn.close()  # closing the connection frees the lock
            self._conn = None
