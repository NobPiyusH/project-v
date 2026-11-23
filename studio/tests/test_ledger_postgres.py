"""Real Postgres (localhost:5433), everything rolled back. Skipped when psycopg or the database is not available."""

import uuid
from decimal import Decimal

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.rows import dict_row  # noqa: E402

from studio.config import Settings  # noqa: E402
from studio.db import PgDb  # noqa: E402
from studio.ledger import BudgetGuard, CostLedger  # noqa: E402
from studio.tests.test_pricing import at  # noqa: E402


@pytest.fixture
def db():
    try:
        conn = psycopg.connect(Settings.from_env().dsn, row_factory=dict_row, autocommit=False, connect_timeout=3)
    except psycopg.OperationalError as e:
        pytest.skip(f"no local Postgres: {e}")
    d = PgDb("", conn=conn)
    yield d
    conn.rollback()
    conn.close()


def test_record_then_totals_and_cap(db):
    marker = f"zz_test_{uuid.uuid4().hex[:8]}"
    ledger = CostLedger(db)
    ledger.record(None, marker, Decimal("2.5"), Decimal("10"), "tokens", True, "migration test")
    totals = {r["provider_id"]: r for r in ledger.totals_by_provider()}
    assert totals[marker]["amount_inr"] == Decimal("2.5000")
    assert totals[marker]["entries"] == 1
    guard = BudgetGuard(db, at("2026-11-19"))
    before = guard.gemini_spent_this_month()
    ledger.record(None, "gemini_api", Decimal("7"), None, None, False, None)
    assert guard.gemini_spent_this_month() == before + Decimal("7")
