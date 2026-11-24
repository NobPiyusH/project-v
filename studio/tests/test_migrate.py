"""The Flyway-compatible migration runner (studio.migrate) on throwaway databases: a fresh one gets every file, a Flyway-migrated one is left alone,
a new file runs once, an edited applied file stops the start."""

import uuid

import pytest

pytest.importorskip("psycopg")
from studio.config import Settings  # noqa: E402
from studio.db import PgDb  # noqa: E402
from studio.migrate import MIGRATIONS, MigrationError, checksum, files, migrate, pending  # noqa: E402


@pytest.fixture
def fresh():
    import psycopg

    s = Settings.from_env()
    name = f"projectv_mig_{uuid.uuid4().hex[:10]}"
    try:
        admin = psycopg.connect(s.dsn.replace(f"dbname={s.postgres_db}", "dbname=postgres"), autocommit=True)
    except psycopg.OperationalError as e:
        pytest.skip(f"no local Postgres: {e}")
    admin.execute(f'CREATE DATABASE "{name}"')
    db = PgDb(s.dsn.replace(f"dbname={s.postgres_db}", f"dbname={name}"))
    yield db
    db.close()
    admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    admin.close()


def test_the_repo_has_the_43_flyway_files_in_order():
    vs = [v for v, _, _ in files()]
    assert vs == list(range(1, len(vs) + 1)) and len(vs) >= 43


def test_checksum_is_flyways_crc32_of_the_lines():
    assert checksum("a\nb\n") == checksum("a\r\nb\r\n") == checksum("﻿a\nb")
    assert checksum("select 1;") != checksum("select 2;")


def test_a_fresh_database_gets_every_file_once_and_a_second_run_does_nothing(fresh):
    done = migrate(fresh)
    assert done == [v for v, _, _ in files()]
    rows = fresh.rows("SELECT version, checksum, success FROM flyway_schema_history ORDER BY installed_rank")
    assert [int(r["version"]) for r in rows] == done and all(r["success"] for r in rows)
    assert fresh.rows("SELECT count(*) AS n FROM video")[0]["n"] == 0
    assert migrate(fresh) == []


def test_a_database_flyway_already_migrated_is_left_alone_and_only_newer_files_run(fresh, tmp_path):
    migrate(fresh)  # same history rows Flyway would have written (checksums verified against the live history table)
    (tmp_path / "V99__extra.sql").write_text("CREATE TABLE extra_thing (id int);", encoding="utf-8")
    for _v, _, p in files():
        (tmp_path / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    assert [v for v, _, _ in pending(fresh, tmp_path)] == [99]
    assert migrate(fresh, tmp_path) == [99]
    assert fresh.rows("SELECT count(*) AS n FROM extra_thing")[0]["n"] == 0
    assert migrate(fresh, tmp_path) == []


def test_an_edited_applied_file_stops_the_start(fresh, tmp_path):
    migrate(fresh)
    for _v, _, p in files():
        (tmp_path / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    first = tmp_path / "V1__core_schema.sql"
    first.write_text(first.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="changed after it was applied"):
        migrate(fresh, tmp_path)


def test_the_migration_folder_is_where_the_app_reads_it():
    assert MIGRATIONS.name == "migration" and MIGRATIONS.parent.name == "db" and any(MIGRATIONS.glob("V1__*.sql"))
