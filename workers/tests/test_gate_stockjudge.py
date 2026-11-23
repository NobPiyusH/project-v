import json

from workers.activities import stock as S
from workers.quality_gate import config
from workers.quality_gate import ledger as L
from workers.quality_gate import stockjudge as J

CFG = config.load()


def _c(i, **kw):
    return {"source": "pixabay", "id": str(i), "thumb": f"http://t/{i}", "width": 1920, "height": 1080, "duration": 10.0, **kw}


def _ans(score):
    return json.dumps({"match": score, "problems": ["wrong subject"] if score < 4 else [], "wrong_text": [], "cut_off_text": False}), \
        {"input_tokens": 600, "output_tokens": 150}


def _run(tmp_path, scores, cands=None, cfg=CFG):
    cands = cands or [_c(i) for i in range(len(scores))]
    seen = iter(scores)
    led = L.Ledger(tmp_path / "l.jsonl", cfg, "v1")
    kept, notes = J.judge_candidates(cands, "hands unscrewing a tv", "", lambda img, p: _ans(next(seen)), led, cfg, lambda c: b"jpg")
    return kept, notes, led


def test_picks_the_best_scoring_at_least_four_and_logs_each_call(tmp_path):
    kept, notes, led = _run(tmp_path, [2, 4, 3, 4, 2])
    assert [c["id"] for c in kept] == ["1", "3"]           # both 4s, rank order kept
    assert len((tmp_path / "l.jsonl").read_text().splitlines()) == 5 and led.run_usd > 0


def test_a_five_wins_and_stops_the_calls(tmp_path):
    kept, notes, led = _run(tmp_path, [3, 5, 4, 4, 4])
    assert [c["id"] for c in kept] == ["1"] and len((tmp_path / "l.jsonl").read_text().splitlines()) == 2


def test_none_good_enough_returns_empty_so_the_ai_lane_runs(tmp_path):
    kept, notes, _ = _run(tmp_path, [1, 2, 3, 2, 1])
    assert kept == [] and any("1/5" in n for n in notes)


def test_only_top_n_are_judged_and_cap_stops_the_judge(tmp_path):
    kept, notes, led = _run(tmp_path, [1] * 8, cands=[_c(i) for i in range(8)])
    assert len((tmp_path / "l.jsonl").read_text().splitlines()) == CFG["stock_judge"]["top_n"]
    cfg = json.loads(json.dumps(CFG))
    cfg["caps"]["per_run_usd"] = 0.0
    kept, notes, led = _run(tmp_path / "x", [4], cfg=cfg)
    assert kept == [] and "cap" in notes[0]


def test_candidate_without_preview_is_skipped_not_scored(tmp_path):
    led = L.Ledger(tmp_path / "l.jsonl", CFG, "v1")
    kept, notes = J.judge_candidates([_c(1), _c(2)], "d", "", lambda i, p: _ans(5), led, CFG, lambda c: None if c["id"] == "1" else b"x")
    assert [c["id"] for c in kept] == ["2"] and "no preview" in notes[0]


# ---- the stock lane: switch default OFF, judge failure never breaks the lane ----------------------------------
def test_switch_is_on_by_default_and_can_be_turned_off(monkeypatch):
    monkeypatch.delenv("STOCK_VISION_JUDGE", raising=False)
    assert S._judge_enabled({}) and not S._judge_enabled({"visionJudge": False})
    monkeypatch.setenv("STOCK_VISION_JUDGE", "0")
    assert not S._judge_enabled({}) and S._judge_enabled({"visionJudge": True})


def test_judge_crash_keeps_the_plain_ranking(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no key")
    monkeypatch.setattr(J, "pick", boom)
    ranked, notes = S._vision_pick([_c(1)], {"description": "d"})
    assert [c["id"] for c in ranked] == ["1"] and "skipped" in notes[0]


def test_lane_uses_only_judged_clips_and_falls_back_when_none(monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "k")
    monkeypatch.setitem(S.SEARCHERS, "pixabay", lambda q, a: [_c(7, text="tv repair")])
    monkeypatch.setattr(J, "pick", lambda ranked, d, n, v=None: ([], ["judge: pixabay 7 scored 1/5"]))
    out = S.stock_fetch({"videoId": "v1", "shotId": "s", "shotNumber": 4, "description": "tv repair bench", "aspectRatio": "16:9",
                         "durationSec": 3, "sources": ["pixabay"], "visionJudge": True})
    assert out["found"] is False and "vision judge" in out["reason"] and "scored 1/5" in " ".join(out["skipped"])
