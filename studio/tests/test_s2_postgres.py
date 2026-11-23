"""S2 against the real Postgres, always rolled back (nothing is left behind)."""

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.rows import dict_row  # noqa: E402

from studio.assets.asset_store import LICENCE_GEMINI, AssetStore, NewAsset  # noqa: E402
from studio.config import REPO_ROOT, Settings  # noqa: E402
from studio.db import PgDb  # noqa: E402
from studio.provider.registry import ProviderRegistry  # noqa: E402


@pytest.fixture
def db():
    try:
        conn = psycopg.connect(Settings.from_env().dsn, row_factory=dict_row, autocommit=False)
    except psycopg.OperationalError as e:
        pytest.skip(f"no local Postgres: {e}")
    yield PgDb("", conn=conn)
    conn.rollback()
    conn.close()


def test_asset_create_then_describe_and_uri(db):
    store = AssetStore(db)
    a = NewAsset(None, "image", "s3://projectv/zz/test.png", "image/png", 10, 20, None, "gemini", "m", "prompt", 9, LICENCE_GEMINI, True)
    asset_id = store.create(a)
    assert store.uri_of(asset_id) == "s3://projectv/zz/test.png"
    d = store.describe(asset_id)
    assert d["provider"] == "gemini" and d["seed"] == 9 and d["width"] == 10


def test_registry_reload_lists_the_configured_providers(db):
    n = ProviderRegistry(db, REPO_ROOT / "config").reload()
    ids = {r["id"] for r in ProviderRegistry(db, REPO_ROOT / "config").all()}
    assert n > 0 and len(ids) >= n
