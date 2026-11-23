"""Port of InspectorTest plus the database side (media requests, evaluate, reports) on a scratch Postgres."""

import json
import uuid

import pytest

from studio.media.types import Finding, MediaInspectResult
from studio.planner.plan import VideoPlan
from studio.production import inspector as ins
from studio.production.records import StoredLine, StoredScript
from studio.production.support import ProductionDeps, diagram_shot, stock_shot
from studio.workflow.stage import Stage


def plan(fmt, seconds, must_include):
    return VideoPlan(title="t", summary="s", format=fmt, durationSec=seconds, aspectRatio="9:16", outputResolution="1080p",
                     stylePreset="shorts_punchy", tone="calm", language="en", needsResearch=True, narration="full", voice="v",
                     music="support", soundDesign=True, visualTypes=["ai_video"], shortsCount=0, mustInclude=must_include, rationale="")


def voiced(i, text, sec):
    return StoredLine(i, text, "a", "p", True, None, f"s3://x/{i}", sec, "READY", False, False, None, [], "coverage 100%", None)


def codes(findings):
    return [f.code for f in findings]


def test_a_script_too_long_for_the_short_fails():
    long_line = " ".join(["word"] * 160)
    s = StoredScript(1, [StoredLine(0, long_line, "a", "p", True)], "v", "s", "m", None, None)
    assert {"SCRIPT_TOO_LONG", "SHORT_TOO_LONG"} <= set(codes(ins.script(plan("short", 60, []), s)))


def test_missing_must_include_items_are_warned_not_failed():
    s = StoredScript(1, [StoredLine(0, "Olbers' paradoxes puzzled everyone.", "a", "p", True)], "v", "s", "m", None, None)
    f = ins.script(plan("short", 60, ["Olbers' paradox", "Expansion of the universe"]), s)
    assert codes(f) == ["MISSING_MUST_INCLUDE"] and "Expansion of the universe" in f[0].message


def test_dead_air_after_a_shortened_line_is_warned():
    # The night-sky case: line 2 got 2.6s shorter after re-voicing, but the next line still starts where planned.
    s = StoredScript(1, [voiced(0, "one", 5.0), voiced(1, "two", 7.8), voiced(2, "three", 6.0)], "v", "s", "m", {"0": 0.4, "1": 6.0, "2": 17.0}, 1.0)
    f = ins.gaps(s)
    assert codes(f) == ["DEAD_AIR"] and f[0].ref == "line 2" and f[0].message.startswith("3.2s of silence")


def test_a_short_whose_voice_runs_past_60s_fails():
    s = StoredScript(1, [voiced(0, "x", 30), voiced(1, "y", 29)], "v", "s", "m", {"0": 0.4, "1": 30.9}, 1.0)
    assert codes(ins.timing(plan("short", 60, []), s)) == ["SHORT_TOO_LONG"]
    assert ins.speech_end(s) == pytest.approx(59.9)


def test_report_verdicts_and_redo_notes():
    fail = ins.report([Finding("FAIL", "SPEECH_MISMATCH", "Instruction read aloud.", "line 3"), Finding("WARN", "CLIPPING", "Peak", "line 1")], 7)
    assert fail.verdict == "FAIL" and fail.summary.startswith("Inspector: FAIL, 1 problem (7 files checked): line 3: Instruction")
    assert "- line 3: Instruction read aloud." in fail.notes
    warn = ins.report([Finding("WARN", "CLIPPING", "Peak", "line 1")], 0)
    assert warn.verdict == "WARN" and warn.notes is None
    ok = ins.report([], 3)
    assert ok.verdict == "PASS" and "FAIL" not in ok.summary


def test_research_narration_shot_plan_and_clip_rules():
    p = plan("long_form", 480, [])
    assert codes(ins.research(p, [])) == ["NO_FACTS"]
    from studio.media.types import Fact

    assert codes(ins.research(p, [Fact("a", []), Fact("b", [])])) == ["FEW_FACTS", "UNSOURCED"]
    s = StoredScript(1, [StoredLine(0, "x", spoken=True), StoredLine(1, "y", spoken=True, audio_uri="u", check="only 60% spoken")], None)
    assert codes(ins.narration(s)) == ["MISSING_VOICE", "VOICE_CHECK_FAILED"] and codes(ins.narration(None)) == ["NO_SCRIPT"]
    assert codes(ins.shot_plan(p, None, [])) == ["NO_SHOTS"]
    assert codes(ins.shot_plan(p, None, [{"id": 1, "ord": 0, "duration_target_sec": 0.5}])) == ["FLASH_SHOTS"]
    shots = [{"id": "a", "ord": 0, "keyframe_uri": "k", "draft_uri": None, "clip_status": None}, {"id": "b", "ord": 1, "keyframe_uri": "k",
              "draft_uri": None, "clip_status": "STOPPED"}, {"id": "c", "ord": 2, "keyframe_uri": "k", "draft_uri": None, "footage_asset_id": 5}]
    mc = ins.missing_clips(shots, True)
    assert [f.target_id for f in mc] == ["a"] and mc[0].ref == "shot 1" and mc[0].message == "This shot has no draft clip."
    assert [f.target_id for f in ins.with_shot_targets([Finding("FAIL", "X", "m", "shot 2")], shots)] == ["b"]


def test_diagram_and_stock_shots_are_recognised_by_lane_and_need_no_keyframe():
    shot = {"id": "s1", "ord": 0, "prompt_json": '{"lane": "diagram", "diagramSpec": {}}'}
    assert diagram_shot(shot) and not diagram_shot({"id": "s2", "prompt_json": '{"lane": "ai_video"}'}) and not diagram_shot({"id": "s3"})
    assert stock_shot({"prompt_json": '{"lane":"stock"}'}) and not stock_shot(None)
    assert ins.keyframes([shot]) == [] and len(ins.keyframes([{"id": "s2", "ord": 1}])) == 1


def test_mentions_and_helpers():
    assert ins.word_count("  a  b c ") == 3 and ins.word_count(None) == 0 and ins.normalise("Hello, World!_x") == "hello world x"
    assert ins.mentions("olbers paradoxes", "Olbers' paradox") and not ins.mentions("olbers", "Olbers' paradox")


# ---- database side


@pytest.fixture
def video(scratch_db):
    v = uuid.uuid4()
    p = plan("short", 60, [])
    scratch_db.execute("INSERT INTO video (id, prompt, status, plan_json) VALUES (%(i)s, 'x', 'RUNNING', CAST(%(p)s AS jsonb))",
                       {"i": v, "p": json.dumps({"plan": p.model_dump()})})
    return v


def inspector(scratch_db):
    return ins.Inspector(ProductionDeps(scratch_db, None, None, None, None, None, None, None))


def test_evaluate_stores_the_report_and_replaces_it_on_a_new_run(scratch_db, video):
    i = inspector(scratch_db)
    r = i.evaluate(video, Stage.ASSEMBLY, 1, MediaInspectResult([Finding("WARN", "CLIPPING", "Peak", "line 1")], ["a.wav"]))
    assert r.verdict == "FAIL" and "No master was stored." in r.summary  # a worker WARN does not hide the structural FAIL
    stored = i.reports(video)
    assert len(stored) == 1 and stored[0]["verdict"] == "FAIL" and stored[0]["checked"] == ["a.wav"]
    assert {f["code"] for f in stored[0]["findings"]} == {"NO_MASTER", "CLIPPING"}
    scratch_db.execute("UPDATE video SET status = status WHERE id = %(v)s", {"v": video})
    i.evaluate(video, Stage.ASSEMBLY, 1, None)
    assert len(i.reports(video)) == 1, "same stage and attempt: replaced, not duplicated"


def test_media_request_per_stage(scratch_db, video):
    i = inspector(scratch_db)
    assert i.media_request(video, Stage.RESEARCH) is None
    r = i.media_request(video, Stage.ASSEMBLY)
    assert (r.stage, r.is_short, r.aspect_ratio, r.target_sec, r.video_uri) == ("ASSEMBLY", True, "9:16", 60.0, None)
    assert i.media_request(video, Stage.NARRATION).lines == []
    assert i.structural(video, Stage.SCRIPT) == [Finding("FAIL", "NO_SCRIPT", "The script has no lines.", "script")]
    assert [f.code for f in i.structural(video, Stage.MUSIC)] == ["NO_MUSIC"]
    assert [f.code for f in i.structural(video, Stage.ANIMATIC)] == ["NO_ANIMATIC"]
    assert [f.code for f in i.structural(video, Stage.SHOT_PLAN)] == ["NO_SHOTS"]
    assert i.structural(video, Stage.PLAN) == [] and Stage.PLAN not in ins.INSPECTED
