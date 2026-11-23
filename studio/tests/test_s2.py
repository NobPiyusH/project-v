"""Stage S2: media types (wire contract), asset store, presigned URLs, provider registry."""

import json
import uuid
from decimal import Decimal

import pytest

from studio.assets.asset_store import LICENCE_GEMINI, AssetStore, NewAsset
from studio.assets.media_urls import MediaUrls
from studio.config import Settings
from studio.media import types as t
from studio.media.wire import camel, from_json, to_json
from studio.provider.registry import ProviderRegistry
from studio.tests.fakes import FakeDb
from studio.tests.test_pricing import TEST_CONFIG


def test_stock_result_ignores_fields_the_worker_adds_later():
    """Port of StockResultJsonTest: an unknown field must never wedge a workflow."""
    raw = (
        '{"found":true,"reason":null,"queries":["q"],"skipped":[],"uri":"s3://b/x.mp4","width":1920,"height":1080,'
        '"durationSec":4.0,"sourceWidth":3840,"sourceHeight":2160,"provenance":{"source":"pixabay","sourceId":"1",'
        '"pageUrl":"u","author":"a","licence":"l","licenceUrl":"","attribution":"","creditRequired":false,'
        '"fetchedAt":"t","sha256":"h","futureField":1}}'
    )
    r = from_json(t.StockResult, json.loads(raw))
    assert r.found is True
    assert r.height == 1080
    assert r.provenance.source_id == "1"


def test_camel_case_names():
    assert camel("shot_id") == "shotId"
    assert camel("estimate_inr") == "estimateInr"
    assert camel("uri") == "uri"


def test_clip_request_round_trips_with_money_as_a_number_and_java_defaults():
    job = t.ClipJob("s1", 1, 1, "draft", "fast", "s3://b/i.png", "p", 832, 480, 81, 7)
    req = t.ClipRequest("v", "app", "cls", None, [job], 1, 60.0, Decimal("12.5"), "modal", "480p", None)
    data = to_json(req)
    assert data["estimateInr"] == 12.5 and isinstance(data["estimateInr"], float)
    assert data["jobs"][0]["numFrames"] == 81
    assert (data["holdSeconds"], data["chain"], data["svi"], data["gpuUrl"]) == (0, False, False, None)
    assert (data["jobs"][0]["scene"], data["jobs"][0]["beat"]) == (0, 0)
    back = from_json(t.ClipRequest, json.loads(json.dumps(data)))
    assert back.estimate_inr == Decimal("12.5") and back.jobs[0].seed == 7


def test_clip_result_start_ups_falls_back_to_containers():
    assert t.ClipResult([], 3, False).start_ups() == 3
    assert t.ClipResult([], 3, False, cold_starts=1).start_ups() == 1


def test_narration_voice_kind_by_model_prefix():
    r = t.NarrationResult([], "chatterbox:v1", "x")
    assert r.chatterbox() and not r.eleven_labs() and not r.kokoro()


def test_asset_create_writes_provenance_asset_and_backlink_in_one_transaction():
    db = FakeDb()
    a = NewAsset(None, "image", "s3://b/k.png", "image/png", 1, 2, None, "gemini", "m", "p", 5, LICENCE_GEMINI, True)
    AssetStore(db).create(a)
    assert db.transactions == 1
    starts = [" ".join(sql.split()[:3]) for sql, _ in db.executed]
    assert starts == ["INSERT INTO provenance", "INSERT INTO asset", "UPDATE provenance SET"]


def test_asset_lookup_of_missing_row_fails():
    with pytest.raises(LookupError):
        AssetStore(FakeDb()).uri_of(uuid.uuid4())


def test_presign_builds_a_path_style_signed_url_and_rejects_non_s3():
    urls = MediaUrls(Settings())
    u = urls.presign("s3://projectv/a/b.mp4", 600)
    assert u.startswith("http://127.0.0.1:8333/projectv/a/b.mp4?") and "X-Amz-Signature" in u
    for bad in (None, "http://x/y", "s3://bucketonly"):
        with pytest.raises(ValueError):
            urls.presign(bad, 60)


def test_registry_upserts_every_provider_with_config_split_from_header_fields():
    db = FakeDb()
    n = ProviderRegistry(db, TEST_CONFIG).reload()
    assert n == len(db.executed) > 0
    first = db.executed[0][1]
    assert set(first) == {"id", "name", "kind", "caps", "config", "enabled"}
    cfg = json.loads(first["config"])
    assert not {"id", "name", "kind", "capabilities", "enabled"} & set(cfg)
