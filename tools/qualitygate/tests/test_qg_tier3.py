"""Tier 3: prompt, parser, decision and the judge loop with a fake model (no network)."""

import json

import pytest

from tools.film4k.repair import Region
from tools.qualitygate import tier3
from tools.qualitygate.config import RUBRIC, THRESHOLDS
from tools.qualitygate.types import Shot

SHOT = Shot("s1", 10.0, 14.0)


def reply(scores=None, defects=None):
    s = dict.fromkeys(RUBRIC, 9) | (scores or {})
    return json.dumps({"scores": s, "defects": defects or []})


def test_sample_times_are_slice_midpoints():
    assert tier3.sample_times(SHOT, 4) == [10.5, 11.5, 12.5, 13.5]


def test_prompt_lists_frames_and_rubric():
    refs = [tier3.FrameRef(t, f"/tmp/{i}.jpg") for i, t in enumerate(tier3.sample_times(SHOT, 3))]
    p = tier3.build_prompt(SHOT, refs, intent="a dragon over a city")
    assert p.shot_id == "s1" and len(p.frames) == 3
    for key in RUBRIC:
        assert key in p.text
    assert "a dragon over a city" in p.text and "Frame 3" in p.text and "ONLY this JSON" in p.text


def test_parse_weighted_score_and_fences():
    v = tier3.parse_verdict("Sure!\n```json\n" + reply({"artifacts": 4}) + "\n```")
    expected = sum(w * (4 if k == "artifacts" else 9) for k, (w, _) in RUBRIC.items())
    assert v.weighted == pytest.approx(expected, abs=0.01)


def test_parse_clamps_and_drops_bad_defects():
    defects = [{"kind": "x", "t0": "bad"}, {"kind": "ok", "t0": 1, "t1": 2, "severity": "major"}]
    v = tier3.parse_verdict(reply({"composition": 99}, defects))
    assert v.scores["composition"] == 10 and [d.kind for d in v.defects] == ["ok"] and v.defects[0].severity == "major"


@pytest.mark.parametrize("bad", ["", "no json", "{not json}", '{"scores": {"artifacts": 5}}', '{"scores": 3}'])
def test_parse_rejects_unusable(bad):
    with pytest.raises(tier3.JudgeReplyError):
        tier3.parse_verdict(bad)


def test_accept_when_good_and_no_major_defect():
    assert tier3.decide(tier3.parse_verdict(reply()), SHOT).action == "accept"
    minor = [{"kind": "tiny", "severity": "minor", "t0": 0, "t1": 1}]
    assert tier3.decide(tier3.parse_verdict(reply(defects=minor)), SHOT).action == "accept"


def test_major_defect_blocks_accept_and_maps_to_inpaint_region():
    d = [{"kind": "wing turns blue", "severity": "major", "t0": 1.0, "t1": 2.5, "box": [0.1, 0.1, 0.3, 0.3]}]
    dec = tier3.decide(tier3.parse_verdict(reply(defects=d)), SHOT)
    assert dec.action == "repair" and len(dec.repairs) == 1
    plan = dec.repairs[0]
    assert plan.mode == "vace_inpaint" and plan.region == Region(0.1, 0.1, 0.3, 0.3, 1.0, 2.5)


def test_big_or_long_or_unboxed_defects_become_reroll():
    long_ = [{"kind": "melt", "severity": "major", "t0": 0, "t1": 4.5, "box": [0.1, 0.1, 0.3, 0.3]}]
    huge = [{"kind": "melt", "severity": "major", "t0": 0, "t1": 1, "box": [0, 0, 0.9, 0.9]}]
    nobox = [{"kind": "colour shift", "severity": "major", "t0": 0, "t1": 1, "box": None}]
    bad_box = [{"kind": "x", "severity": "major", "t0": 0, "t1": 1, "box": [0.9, 0.9, 0.5, 0.5]}]
    long_shot = Shot("long", 0.0, 8.0)  # the 4 s inpaint limit must apply, not the shot length
    for defects in (long_, huge, nobox, bad_box):
        dec = tier3.decide(tier3.parse_verdict(reply(defects=defects)), long_shot)
        assert dec.action == "repair" and dec.repairs[0].mode == "reroll" and dec.repairs[0].region is None


def test_low_score_is_reroll_even_with_boxed_defect():
    scores = dict.fromkeys(RUBRIC, 2)
    d = [{"kind": "a", "severity": "major", "t0": 0, "t1": 1, "box": [0.1, 0.1, 0.2, 0.2]}]
    dec = tier3.decide(tier3.parse_verdict(reply(scores, d)), SHOT)
    assert [p.mode for p in dec.repairs] == ["reroll"]


def test_mid_score_without_defects_rerolls():
    dec = tier3.decide(tier3.parse_verdict(reply({"colour_drift": 2, "composition": 4, "artifacts": 6})), SHOT)
    assert dec.action == "repair" and dec.repairs[0].mode == "reroll"


def test_at_most_two_repairs_per_shot():
    d = [{"kind": f"d{i}", "severity": "major", "t0": i * 0.5, "t1": i * 0.5 + 0.4, "box": [0.1, 0.1, 0.2, 0.2]} for i in range(5)]
    v = tier3.parse_verdict(reply(defects=d))
    assert len(tier3.decide(v, SHOT, repairs_done=0).repairs) == 2
    assert len(tier3.decide(v, SHOT, repairs_done=1).repairs) == 1
    assert tier3.decide(v, SHOT, repairs_done=2).action == "fail"
    assert THRESHOLDS["max_repairs_per_shot"] == 2


def test_judge_shot_with_fake_judge_and_extractor(tmp_path):
    seen = {}
    extracted = []

    def fake_judge(prompt):
        seen["prompt"] = prompt
        return reply()

    out = tier3.judge_shot("v.mp4", SHOT, fake_judge, extract=lambda v, t, p: extracted.append((v, t, p)), workdir=str(tmp_path))
    assert out.decision.action == "accept" and len(extracted) == THRESHOLDS["frames_per_shot"]
    assert len(seen["prompt"].frames) == THRESHOLDS["frames_per_shot"]
    assert out.to_dict()["decision"]["action"] == "accept"


def test_unparseable_reply_fails_never_accepts(tmp_path):
    out = tier3.judge_shot("v.mp4", SHOT, lambda p: "I cannot help", extract=lambda *a: None, workdir=str(tmp_path))
    assert out.decision.action == "fail" and out.verdict is None


def test_plan_times_spans_shot_and_adds_focus_moments():
    t = tier3.plan_times(SHOT, 4)
    assert len(t) == 4 and t[0] == pytest.approx(10.1) and t[-1] == pytest.approx(13.9)  # first/last frame = before/after pair
    with_focus = tier3.plan_times(SHOT, 4, focus=(11.0, 10.15, 99.0))
    assert 11.0 in with_focus and 99.0 not in with_focus and 10.15 not in with_focus  # outside the shot / too close to a sample
    assert len(tier3.plan_times(SHOT, 4, focus=(10.7, 11.3, 11.9, 12.5, 13.2))) <= 4 + 3


def test_prompt_includes_hints_and_comparison_checks():
    refs = [tier3.FrameRef(10.5, "a.jpg"), tier3.FrameRef(12.0, "b.jpg")]
    p = tier3.build_prompt(SHOT, refs, hints=("colour change near 11.2s",))
    assert "colour change near 11.2s" in p.text and "COLOUR OF PARTS" in p.text and "near-static" in p.text
    assert "colour change" not in tier3.build_prompt(SHOT, refs).text
