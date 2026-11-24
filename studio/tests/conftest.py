"""Shared fixtures.

`scratch_db`: a throwaway database on the local Postgres server with the real schema (db/migration/V*.sql applied in
order), dropped afterwards. Tests that change data use this, never the live `projectv` database.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from studio.config import REPO_ROOT, Settings

MIGRATIONS = REPO_ROOT / "db" / "migration"


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
    from studio.db import PgDb
    from studio.migrate import migrate

    runner = PgDb(dsn)
    migrate(runner, MIGRATIONS, user="tests")  # the same runner the app uses at start
    runner.close()
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


@pytest.fixture(scope="session")
def bash():
    """A bash that really runs. On Windows `bash` on PATH may be C:\\Windows\\System32\bash.exe, the WSL launcher, which fails when no
    Linux distro is installed (24 Nov 2026: two tests failed in PowerShell, passed in Git Bash). Skip when it cannot run a command."""
    candidates = [shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"]
    for exe in candidates:
        if not exe or not Path(exe).exists():
            continue
        try:
            r = subprocess.run([exe, "-c", "echo ok"], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and r.stdout.strip() == "ok":
            return exe
    pytest.skip("no usable bash (the WSL launcher without a distro does not count)")
