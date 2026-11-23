"""Shared fixtures.

`scratch_db`: a throwaway database on the local Postgres server with the real schema (db/migration/V*.sql applied in
order), dropped afterwards. Tests that change data use this, never the live `projectv` database.
"""

from __future__ import annotations

import re
import uuid

import pytest

from studio.config import REPO_ROOT, Settings

MIGRATIONS = REPO_ROOT / "control-plane" / "src" / "main" / "resources" / "db" / "migration"


def _version(p) -> int:
    return int(re.match(r"V(\d+)__", p.name).group(1))


@pytest.fixture(scope="session")
def scratch_dsn():
    psycopg = pytest.importorskip("psycopg")
    s = Settings.from_env()
    admin_dsn = s.dsn.replace(f"dbname={s.postgres_db}", "dbname=postgres")
    name = f"projectv_test_{uuid.uuid4().hex[:10]}"
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True)
    except psycopg.OperationalError as e:
        pytest.skip(f"no local Postgres: {e}")
    admin.execute(f'CREATE DATABASE "{name}"')
    dsn = s.dsn.replace(f"dbname={s.postgres_db}", f"dbname={name}")
    with psycopg.connect(dsn, autocommit=True) as conn:
        for f in sorted(MIGRATIONS.glob("V*.sql"), key=_version):
            conn.execute(f.read_text(encoding="utf-8"))
    yield dsn
    admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    admin.close()


@pytest.fixture
def scratch_db(scratch_dsn):
    import psycopg
    from psycopg.rows import dict_row

    from studio.db import PgDb

    conn = psycopg.connect(scratch_dsn, row_factory=dict_row, autocommit=True)
    # The scratch database lives for the whole session: every test starts with an empty ledger, so monthly caps and wallet sums are its own.
    conn.execute("DELETE FROM cost_entry")
    yield PgDb("", conn=conn)
    conn.close()
