import json
from datetime import UTC, datetime

import pytest

from workers.quality_gate import config
from workers.quality_gate import ledger as L
from workers.quality_gate import vision as V

CFG = config.load()


def _ledger(tmp_path, **caps):
    cfg = json.loads(json.dumps(CFG))
    cfg["caps"].update(caps)
    return L.Ledger(tmp_path / "ledger.jsonl", cfg, "v1"), cfg


def test_parse_answer_reads_json_inside_text_and_rejects_junk():
    a = V.parse_answer('Sure: {"match": 2, "problems": ["flat screen, not a tube"], "wrong_text": [], "cut_off_text": false}')
    assert a["match"] == 2 and a["problems"] == ["flat screen, not a tube"]
    assert V.parse_answer("no json here") is None
    assert V.parse_answer('{"match": "high"}') is None


def test_verdict_fails_below_pass_score_and_flags_text_problems():
    ans = {"match": 2, "problems": ["wrong era"], "wrong_text": ["TELEVSION"], "cut_off_text": True}
    codes = {f.code: f.severity for f in V.verdict(ans, 3, "shot 4")}
    assert codes == {"VISION_MISMATCH": "FAIL", "VISION_WRONG_TEXT": "WARN", "VISION_CUT_OFF_TEXT": "WARN"}
    assert V.verdict({"match": 5, "problems": [], "wrong_text": [], "cut_off_text": False}, 3, "shot 1") == []


def test_run_logs_every_call_and_stops_at_the_run_cap(tmp_path):
    led, cfg = _ledger(tmp_path, per_run_usd=0.02)
    calls = []

    def ask(img, prompt):
        calls.append(prompt)
        return '{"match": 4, "problems": [], "wrong_text": [], "cut_off_text": false}', {"input_tokens": 1500, "output_tokens": 200}

    items = [{"ref": f"shot {i}", "description": "d"} for i in range(40)]
    res = V.run(items, ask, led, cfg, lambda it: b"jpg")
    assert 0 < len(calls) < 40
    assert any(f.code == "VISION_CAP_STOP" for f in res.findings)
    assert led.run_usd <= 0.02
    rows = [json.loads(x) for x in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert len(rows) == len(calls) and rows[0]["providerId"] == "openai_vision"


def test_day_cap_counts_rows_from_earlier_runs(tmp_path):
    led, _ = _ledger(tmp_path, per_day_usd=0.05)
    led.record(0.049, 10, "earlier run")
    led2 = L.Ledger(tmp_path / "ledger.jsonl", led.cfg, "v1")
    with pytest.raises(L.CapExceeded):
        led2.reserve(0.01)


def test_day_cap_ignores_yesterday(tmp_path):
    led, _ = _ledger(tmp_path, per_day_usd=0.05)
    led.now = lambda: datetime(2026, 11, 22, 12, tzinfo=UTC)
    led.record(0.049, 10, "yesterday")
    led.now = lambda: datetime(2026, 11, 23, 12, tzinfo=UTC)
    led.reserve(0.01)  # no raise


def test_estimate_is_shown_before_spending_and_is_small():
    e = L.estimate(21, CFG)
    assert e["worst_usd"] < 0.5 and e["typical_usd"] <= e["worst_usd"]
    assert L.image_tokens(1024, 576, 1.2) == 692


def test_unreadable_answer_is_a_warning_not_a_pass(tmp_path):
    led, cfg = _ledger(tmp_path)
    res = V.run([{"ref": "shot 1", "description": "d"}], lambda i, p: ("???", {"input_tokens": 10, "output_tokens": 5}), led, cfg, lambda it: b"")
    assert [f.code for f in res.findings] == ["VISION_UNREADABLE"]
    assert res.stats["judged"] == 0
