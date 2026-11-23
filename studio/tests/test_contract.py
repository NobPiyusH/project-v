import httpx
import pytest

from studio.contract.replay import Exchange, diff, load, record, replay, save


def server(payload, status=200):
    return httpx.Client(base_url="http://x", transport=httpx.MockTransport(lambda r: httpx.Response(status, json=payload)))


def test_numbers_compare_by_value_and_key_order_is_ignored():
    assert diff({"a": 12.5, "b": 1}, {"b": 1, "a": 12.50}) == []


def test_timestamps_are_masked_but_real_drift_is_reported():
    old = [{"provider_id": "gemini_api", "amount_inr": 1.5, "ts": "a"}]
    new = [{"provider_id": "gemini_api", "amount_inr": 1.6, "ts": "b"}]
    assert diff(old, new) == ["$[0].amount_inr: 1.5 != 1.6"]


def test_missing_field_and_length_are_reported():
    assert diff({"a": 1}, {}) == ["$.a: missing in new"]
    assert diff([1], [1, 2]) == ["$: length 1 != 2"]


def test_replay_roundtrip_and_status_check():
    rec = record(server([{"x": 1}]), [("GET", "/api/ledger")])
    assert replay(server([{"x": 1}]), rec) == []
    assert replay(server([{"x": 2}]), rec) != []
    assert replay(server([], status=500), rec) == ["GET /api/ledger: status 200 != 500"]


def test_only_get_can_be_recorded():
    with pytest.raises(ValueError):
        record(server({}), [("POST", "/api/videos")])


def test_exchange_roundtrip(tmp_path):
    save(tmp_path / "r.json", [Exchange("GET", "/api/ledger", 200, [])])
    assert load(tmp_path / "r.json") == [Exchange("GET", "/api/ledger", 200, [])]
