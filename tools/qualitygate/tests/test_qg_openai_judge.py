"""OpenAI judge backend with fakes only: no network, no key, no real ledger path outside tmp."""

import json
import logging

import pytest

pytest.importorskip("yaml")
pytest.importorskip("dotenv")

from tools.qualitygate import openai_judge, tier3  # noqa: E402
from tools.qualitygate.config import RUBRIC  # noqa: E402
from tools.qualitygate.types import Shot  # noqa: E402
from workers.quality_gate import config as gate_config  # noqa: E402
from workers.quality_gate.ledger import Ledger  # noqa: E402

SHOT = Shot("s1", 0.0, 4.0)
GOOD = json.dumps({"scores": dict.fromkeys(RUBRIC, 9), "defects": []})
KEY = "sk-test-SECRET-123"


class FakeResp:
    def __init__(self, status=200, text=GOOD, usage=(3000, 200)):
        self.status_code = status
        self._text = text
        self._usage = usage

    def json(self):
        return {"choices": [{"message": {"content": self._text}}], "usage": {"prompt_tokens": self._usage[0], "completion_tokens": self._usage[1]}}

    @property
    def text(self):
        return f"error echo {KEY}" if self.status_code != 200 else "ok"


class FakePost:
    def __init__(self, *responses):
        self.responses = list(responses) or [FakeResp()]
        self.calls = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


@pytest.fixture
def cfg():
    return gate_config.load()


def build(tmp_path, cfg, post, max_usd=0.2):
    ledger = Ledger(tmp_path / "ledger.jsonl", cfg)
    judge = openai_judge.make_openai_judge(max_usd, ledger=ledger, cfg=cfg, post=post, read_jpeg=lambda p: b"JPEG:" + p.encode(), key_fn=lambda: KEY)
    return judge, ledger


def shot_judgement(judge, tmp_path):
    return tier3.judge_shot("v.mp4", SHOT, judge, extract=lambda *a: None, workdir=str(tmp_path))


def test_cap_is_mandatory(cfg):
    for bad in (0, -1, None):
        with pytest.raises(ValueError):
            openai_judge.make_openai_judge(bad, cfg=cfg)


def test_request_shape_sends_all_frames_and_json_mode(tmp_path, cfg):
    post = FakePost()
    judge, _ = build(tmp_path, cfg, post)
    out = shot_judgement(judge, tmp_path)
    assert out.decision.action == "accept"
    url, body, headers = post.calls[0]
    assert url == openai_judge.URL and headers["Authorization"] == f"Bearer {KEY}"
    content = body["messages"][1]["content"]
    assert content[0]["type"] == "text" and len(content) == 1 + tier3.THRESHOLDS["frames_per_shot"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert body["response_format"] == {"type": "json_object"} and body["model"] == cfg["vision"]["model"]


def test_cost_is_recorded_and_logged(tmp_path, cfg, caplog):
    judge, ledger = build(tmp_path, cfg, FakePost(FakeResp(usage=(3000, 200))))
    with caplog.at_level(logging.INFO, logger="qualitygate.judge"):
        shot_judgement(judge, tmp_path)
    p = cfg["vision"]["price_usd_per_1m"]
    expected = (3000 * p["input"] + 200 * p["output"]) / 1e6
    row = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[0])
    assert row["amountUsd"] == pytest.approx(expected, abs=1e-6) and row["units"] == 3200
    assert ledger.run_usd == pytest.approx(expected)
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "in=3000" in msg and "out=200" in msg and KEY not in msg


def test_cap_blocks_the_call_before_any_request(tmp_path, cfg):
    post = FakePost()
    worst = openai_judge.worst_case_usd(6, cfg)
    judge, _ = build(tmp_path, cfg, post, max_usd=worst / 2)  # cap below one worst-case call
    out = shot_judgement(judge, tmp_path)
    assert post.calls == [] and out.decision.action == "fail" and "spend cap" in out.decision.reason


def test_cap_stops_after_spend_accumulates(tmp_path, cfg):
    post = FakePost(FakeResp(usage=(20000, 1200)))
    worst = openai_judge.worst_case_usd(6, cfg)
    judge, ledger = build(tmp_path, cfg, post, max_usd=worst * 1.5)
    assert shot_judgement(judge, tmp_path).decision.action == "accept"
    assert shot_judgement(judge, tmp_path).decision.action == "fail"  # second call could pass the cap
    assert len(post.calls) == 1


def test_http_error_fails_the_shot_and_scrubs_key(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(openai_judge.time, "sleep", lambda s: None)
    post = FakePost(FakeResp(status=429))
    judge, ledger = build(tmp_path, cfg, post)
    out = shot_judgement(judge, tmp_path)
    assert out.decision.action == "fail" and "judge unavailable" in out.decision.reason
    assert KEY not in out.decision.reason and len(post.calls) == 3 and ledger.run_usd == 0


def test_retry_then_success(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(openai_judge.time, "sleep", lambda s: None)
    judge, _ = build(tmp_path, cfg, FakePost(FakeResp(status=503), FakeResp()))
    assert shot_judgement(judge, tmp_path).decision.action == "accept"


def test_estimate_run_matches_worst_case(cfg):
    est = openai_judge.estimate_run(10, 6, cfg)
    assert est["worst_run_usd"] == pytest.approx(10 * openai_judge.worst_case_usd(6, cfg), abs=1e-3)
    assert 0 < est["worst_per_shot_usd"] < 0.1 and est["model"] == cfg["vision"]["model"]
