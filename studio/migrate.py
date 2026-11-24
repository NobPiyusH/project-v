"""Schema migrations, Flyway-compatible: the Java app used Flyway on db/migration/V<n>__<description>.sql and recorded every run in
`flyway_schema_history`. This runner reads and writes that same table, so a database Flyway already migrated (versions 1..43) is left alone and
only newer files run, in version order. Applied files are never re-run; one whose recorded checksum no longer matches the file is reported and
stops the start (an edited migration is a mistake, as with Flyway's validate).

    python -m studio.migrate            # apply what is missing on the configured database

An advisory lock makes two starting processes take turns."""

from __future__ import annotations

import re
import sys
import time
import zlib
from pathlib import Path

from studio.config import REPO_ROOT, Settings

MIGRATIONS = REPO_ROOT / "db" / "migration"
LOCK_KEY = 7_650_001
HISTORY = """CREATE TABLE IF NOT EXISTS flyway_schema_history (
    installed_rank integer NOT NULL, version varchar(50), description varchar(200) NOT NULL, type varchar(20) NOT NULL,
    script varchar(1000) NOT NULL, checksum integer, installed_by varchar(100) NOT NULL, installed_on timestamp NOT NULL DEFAULT now(),
    execution_time integer NOT NULL, success boolean NOT NULL, CONSTRAINT flyway_schema_history_pk PRIMARY KEY (installed_rank))"""


class MigrationError(RuntimeError):
    pass


def checksum(text: str) -> int:
    """Flyway's checksum: CRC32 over each line (without its line ending, BOM dropped), as a signed 32-bit integer."""
    crc = 0
    for i, line in enumerate(text.splitlines()):
        if i == 0:
            line = line.lstrip("﻿")
        crc = zlib.crc32(line.encode("utf-8"), crc)
    return crc - (1 << 32) if crc >= (1 << 31) else crc


def files(folder: Path = MIGRATIONS) -> list[tuple[int, str, Path]]:
    out = []
    for p in folder.glob("V*__*.sql"):
        m = re.fullmatch(r"V(\d+)__(.+)\.sql", p.name)
        if m:
            out.append((int(m.group(1)), m.group(2).replace("_", " "), p))
    return sorted(out)


def pending(db, folder: Path = MIGRATIONS) -> list[tuple[int, str, Path]]:
    """The files still to run. Raises MigrationError when an applied file was changed or an applied version has no file."""
    try:
        done = {int(r["version"]): r for r in db.rows("SELECT version, checksum, success FROM flyway_schema_history WHERE version IS NOT NULL")}
    except Exception:
        done = {}
    have = files(folder)
    known = {v for v, _, _ in have}
    for v, r in done.items():
        if not r["success"]:
            raise MigrationError(f"migration {v} failed earlier: repair flyway_schema_history by hand before starting")
        if v not in known:
            raise MigrationError(f"migration {v} is applied but its file is missing from {folder}")
    for v, _, p in have:
        if v in done and done[v]["checksum"] != checksum(p.read_text(encoding="utf-8")):
            raise MigrationError(f"{p.name} changed after it was applied (checksum differs): add a new version instead of editing it")
    return [(v, d, p) for v, d, p in have if v not in done]


def migrate(db, folder: Path = MIGRATIONS, user: str = "studio") -> list[int]:
    """Apply every pending file in order; returns the versions applied. Each file runs in its own transaction."""
    db.execute("SELECT pg_advisory_lock(%(k)s)", {"k": LOCK_KEY})
    try:
        db.execute(HISTORY)
        applied = []
        for v, desc, p in pending(db, folder):
            text = p.read_text(encoding="utf-8")
            t0 = time.monotonic()
            with db.transaction():
                db.execute(text.replace("%", "%%"))
                rank = db.rows("SELECT COALESCE(MAX(installed_rank), 0) + 1 AS r FROM flyway_schema_history")[0]["r"]
                db.execute("INSERT INTO flyway_schema_history (installed_rank, version, description, type, script, checksum, installed_by, execution_time, success) "
                           "VALUES (%(r)s, %(v)s, %(d)s, 'SQL', %(s)s, %(c)s, %(u)s, %(t)s, true)",
                           {"r": rank, "v": str(v), "d": desc, "s": p.name, "c": checksum(text), "u": user, "t": int((time.monotonic() - t0) * 1000)})
            applied.append(v)
        return applied
    finally:
        db.execute("SELECT pg_advisory_unlock(%(k)s)", {"k": LOCK_KEY})


def main() -> int:
    from studio.db import PgDb

    db = PgDb(Settings.from_env().dsn)  # one connection: the advisory lock belongs to it
    applied = migrate(db)
    print(f"applied {applied}" if applied else "database is up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
