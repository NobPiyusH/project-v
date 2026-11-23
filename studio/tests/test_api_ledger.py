"""API contract for GET /api/ledger against the fake DB (needs the `studio` dependency group; skipped without it)."""

from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import create_app  # noqa: E402
from studio.config import Settings  # noqa: E402
from studio.tests.fakes import FakeDb  # noqa: E402


def test_ledger_route_returns_java_shaped_json():
    row = {"provider_id": "gemini_api", "entries": 3, "amount_inr": Decimal("12.3400"), "units": Decimal("100"), "unit_type": "tokens"}
    client = TestClient(create_app(db=FakeDb({"GROUP BY provider_id": [row]}), settings=Settings()))
    r = client.get("/api/ledger")
    assert r.status_code == 200
    assert r.json() == [{"provider_id": "gemini_api", "entries": 3, "amount_inr": 12.34, "units": 100.0, "unit_type": "tokens"}]
