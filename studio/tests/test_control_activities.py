"""The real control activities on a scratch Postgres: the database mirror of the workflow (stage_run, video status), JSON in and out, and the
Producer activities. The stages themselves are tested in their own modules; here the adapters and the wiring are checked."""

import json
import uuid
from types import SimpleNamespace

import pytest

from studio.agents.types import Act, Concept, Script, ScriptLine
from studio.media.wire import to_json
from studio.planner.plan import VideoPlan
from studio.production.finishing_stages import AssemblyStage, FinishStage, FootageStage, SharpenStage
from studio.production.inspector import Inspector
from studio.production.producer import ProducerService
from studio.production.shot_stage import ShotStage, StockStage
from studio.production.stages_m2a import AnimaticStage, MusicStage, ResearchStage, StoryStage, VoiceStage
from studio.production.video_stage import VideoStage
from studio.tests.pworld import make_deps, scalar, seed_video
from studio.workflow.activities import ControlActivities, ProducerActivities, activity_names
from studio.workflow.stage import Stage


class FakePlanner:
    def plan(self, video_id, prompt, notes):
        plan = VideoPlan(title="Light", summary="s", format="short", durationSec=30, aspectRatio="9:16", stylePreset="shorts_punchy", needsResearch=True,
                         narration="full", music="support")
        return SimpleNamespace(plan=plan, stages=[Stage.PLAN, Stage.SCRIPT], source="rules", fallback_reason="no key")


class FakeWriter:
    def concept(self, *a):
        return Concept(logline="L", bigIdea="B", clockLabel="", acts=[Act(title="A", approxSec=30)], emotionalArc="x")

    def script(self, *a):
        return Script(lines=[ScriptLine(text="Hello there.", spoken=True)], voiceName="Charon", voiceStyle="calm", musicBrief="m")


@pytest.fixture
def acts(scratch_db):
    db = scratch_db
    deps = make_deps(db, FakeWriter(), None)
    finish = FinishStage(deps)
    control = ControlActivities(
        db, FakePlanner(), researcher=None, inspector=Inspector(deps), creative=None, research=ResearchStage(deps), story=StoryStage(deps),
        music=MusicStage(deps), voice=VoiceStage(deps), shots=ShotStage(deps), stock=StockStage(deps), animatic=AnimaticStage(deps),
        video=VideoStage(deps), footage=FootageStage(deps), assembly=AssemblyStage(deps), finish=finish, sharpen=SharpenStage(deps, finish))
    producer = ProducerActivities(ProducerService(deps), VoiceStage(deps))
    return db, control, producer, seed_video(db, status="CREATED", with_script=False)


def test_plan_stores_the_plan_and_title_and_returns_the_planned_stages(acts):
    db, c, _, video = acts
    r = c.plan(str(video), "a calm sea", None, 1)
    assert r["plannedStages"] == ["PLAN", "SCRIPT"]
    assert r["summary"] == "Light: short, 30s, 9:16, preset shorts_punchy, narration full, music support, research yes (planned by rules)"
    art = json.loads(r["artifactJson"])
    assert art["source"] == "rules" and art["fallbackReason"] == "no key" and art["stages"] == ["PLAN", "SCRIPT"] and art["plan"]["title"] == "Light"
    row = db.rows("SELECT title, plan_json::text AS p FROM video WHERE id = %(i)s", {"i": video})[0]
    assert row["title"] == "Light" and json.loads(row["p"])["plan"]["durationSec"] == 30


def test_a_placeholder_stage_records_what_it_will_do(acts):
    _, c, _, video = acts
    r = c.run_stage(str(video), "PUBLISH", "slower", 1)
    assert r["summary"].startswith("Placeholder (real work arrives in M2+): Upload as private to YouTube / Instagram.")
    assert 'Notes received: "slower".' in r["summary"] and json.loads(r["artifactJson"])["ownerNotesApplied"] == "slower"


def test_record_stage_upserts_one_row_per_attempt_and_mirrors_the_video_status(acts):
    db, c, _, video = acts
    v = str(video)
    c.record_stage(v, "SCRIPT", 1, "AUTO", "RUNNING", None, None, None)
    row = db.rows("SELECT status, finished_at, summary FROM stage_run WHERE video_id = %(v)s", {"v": video})[0]
    assert row["status"] == "RUNNING" and row["finished_at"] is None
    assert db.rows("SELECT status, current_stage FROM video WHERE id = %(v)s", {"v": video})[0] == {"status": "RUNNING", "current_stage": "SCRIPT"}
    c.record_stage(v, "SCRIPT", 1, "REVIEW", "WAITING_APPROVAL", "done", "{}", "n")
    c.set_review_deadline(v, "SCRIPT", 1, "2026-11-23T10:00:00+00:00")
    assert db.rows("SELECT status FROM video WHERE id = %(v)s", {"v": video})[0]["status"] == "WAITING"
    assert scalar(db, "SELECT review_deadline IS NOT NULL FROM stage_run WHERE video_id = %(v)s", v=video) is True
    c.record_stage(v, "SCRIPT", 1, "REVIEW", "APPROVED", None, None, None)
    row = db.rows("SELECT summary, owner_notes, finished_at, review_deadline, artifact_json::text AS a FROM stage_run WHERE video_id = %(v)s", {"v": video})
    assert len(row) == 1 and row[0]["summary"] == "done" and row[0]["owner_notes"] == "n" and row[0]["finished_at"] is not None
    assert row[0]["review_deadline"] is None and row[0]["a"] == "{}", "earlier values kept when the update has none"
    assert c.last_attempt(v, "SCRIPT") == 1 and c.last_attempt(v, "MUSIC") == 0
    c.record_stage(v, "MUSIC", 0, "AUTO", "SKIPPED", "Not needed", None, None)
    assert db.rows("SELECT current_stage FROM video WHERE id = %(v)s", {"v": video})[0]["current_stage"] == "SCRIPT", "a skipped stage is not the current one"
    c.set_video_status(v, "COMPLETED")
    assert db.rows("SELECT status, current_stage FROM video WHERE id = %(v)s", {"v": video})[0] == {"status": "COMPLETED", "current_stage": None}
    c.record_stage(v, "PUBLISH", 1, "AUTO", "RUNNING", None, None, None)
    assert db.rows("SELECT status FROM video WHERE id = %(v)s", {"v": video})[0]["status"] == "COMPLETED", "a finished video is not reopened"


def test_requests_and_results_travel_as_camelcase_json(acts):
    db, c, _, video = acts
    db.execute("UPDATE video SET plan_json = CAST(%(p)s AS jsonb) WHERE id = %(v)s",
               {"p": json.dumps({"plan": VideoPlan(title="T", format="short", durationSec=60, aspectRatio="9:16", stylePreset="shorts_punchy",
                                                   language="en", narration="full").model_dump()}), "v": video})
    req = c.research_request(str(video), "focus")
    assert req["topic"] == "p" and req["maxFacts"] == 8 and req["ownerNotes"] == "focus"
    saved = c.save_research(str(video), {"facts": [{"text": "x", "sources": [{"title": "t", "uri": "u"}], "check": None}], "sources": [],
                                         "queries": ["q"], "model": "gemini-3.6-flash", "usage": {"promptTokens": 10, "outputTokens": 5, "thoughtTokens": 0,
                                                                                                    "totalTokens": 15}})
    assert saved["summary"].startswith("1 facts (1 with sources)") and saved["plannedStages"] is None
    assert c.concept(str(video), None)["summary"] == "L Big idea: B"
    s = c.script(str(video), None)
    assert s["summary"] == "1 lines (1 spoken, 2 words), voice Charon."
    nr = c.narration_request(str(video))
    assert nr["voiceName"] == "Charon" and nr["lines"] == [{"index": 0, "text": "Hello there.", "delivery": None}]
    line = c.line_narration_request(str(video), [0], "calm")
    assert line["voiceProvider"] == "auto" and line["style"] == "in a voice that is calm"
    c.mark_lines(str(video), [0], "REDOING", None)
    assert scalar(db, "SELECT beat_sheet_json->'lines'->0->>'voiceStatus' FROM script WHERE video_id = %(v)s", v=video) == "REDOING"
    assert c.inspection_request(str(video), "SCRIPT") is None or isinstance(c.inspection_request(str(video), "SCRIPT"), dict)
    rep = c.save_inspection(str(video), "SCRIPT", 1, None)
    assert set(rep) == {"verdict", "summary", "notes", "redoTargets"}
    assert c.stock_requests(str(video)) == [] and to_json([]) == []


def test_clip_request_clears_the_batch_state_and_marks_shots_rendering(acts):
    db, c, _, video = acts
    db.execute("UPDATE video SET plan_json = CAST(%(p)s AS jsonb) WHERE id = %(v)s",
               {"p": json.dumps({"plan": VideoPlan(title="T", format="short", durationSec=60, aspectRatio="9:16", stylePreset="shorts_punchy").model_dump()}),
                "v": video})
    scene, shot, image = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.execute("INSERT INTO scene (id, video_id, ord, title) VALUES (%(s)s, %(v)s, 0, 'S')", {"s": scene, "v": video})
    db.execute("INSERT INTO asset (id, video_id, kind, uri_hot) VALUES (%(a)s, %(v)s, 'image', 's3://b/k.png')", {"a": image, "v": video})
    db.execute("INSERT INTO shot (id, scene_id, ord, duration_target_sec, description, prompt_json, keyframe_asset_id) "
               "VALUES (%(i)s, %(s)s, 0, 3, 'd', '{}', %(a)s)", {"i": shot, "s": scene, "a": image})
    c.clip_progress.request_stop(video)
    est = c.clip_estimate(str(video), "draft", None)
    assert est["clips"] == 1 and est["needsApproval"] is True and isinstance(est["inr"], float)
    req = c.clip_request(str(video), "draft", None)
    assert req["jobs"][0]["shotId"] == str(shot) and req["provider"] == "wan" and not c.clip_progress.stop_requested(video)
    assert db.rows("SELECT clip_status FROM shot WHERE id = %(i)s", {"i": shot})[0]["clip_status"] == "RENDERING"
    result = {"clips": [{"shotId": str(shot), "take": 1, "kind": "draft", "uri": "s3://b/c.mp4", "gpuSeconds": 12, "frames": 49, "fps": 16, "mode": "fast",
                         "error": None, "outputTokens": 0}], "containers": 1, "stopped": False, "coldStarts": 1}
    assert c.save_clips(str(video), req, result)["summary"].startswith("1/1 draft clips")
    c.mark_clips(str(video), [str(shot)], "FAILED", "x")
    assert db.rows("SELECT clip_status, clip_error FROM shot WHERE id = %(i)s", {"i": shot})[0] == {"clip_status": "FAILED", "clip_error": "x"}


def test_producer_activities_wrap_the_service_with_json_values(acts):
    db, _, p, video = acts
    v = str(video)
    assert p.review_request(v, "MUSIC") is None and p.spend_advice(v, 30.0).startswith("AI Producer: would APPROVE")
    assert p.may_redo(v, 1) is True and p.deciding() in (True, False) and p.shot_ids_for(v, ["line-1"]) == []
    d = p.spend_decision(v, "DRAFT_GEN", 60.0)
    assert d["approve"] is False and "would ASK YOU" in d["text"]
    p.mark_acted(None, "approved")  # a review that failed has no decision id: nothing to mark
    saved = p.save_review(v, "SCRIPT", 1, {"verdict": "approve", "confidence": 0.9, "summary": "good", "redoNotes": None, "items": [], "model": "m",
                                           "usage": {"promptTokens": 0, "outputTokens": 0, "thoughtTokens": 0, "totalTokens": 0}})
    assert saved["verdict"] == "approve" and saved["decisionId"] and saved["weakItems"] == []
    p.mark_acted(saved["decisionId"], "approved")
    assert scalar(db, "SELECT acted FROM producer_decision WHERE id = %(i)s", i=uuid.UUID(saved["decisionId"])) == "approved"


def test_every_activity_is_named_and_unique():
    names = activity_names()
    assert len(names) == len(set(names)) and "control.record_stage" in names and "producer.save_review" in names
    assert all(n.startswith(("control.", "producer.")) for n in names)


def test_redo_check_free_stages_paid_stages_and_the_per_video_flag(acts):
    db, c, _, video = acts
    free = c.redo_check(str(video), "ANIMATIC", None)
    assert free["paid"] is False
    paid = c.redo_check(str(video), "KEYFRAMES", ["00000000-0000-0000-0000-000000000001"])
    assert paid["paid"] is True and paid["autoPaid"] is False and "pod" in paid["what"]
    db.execute("UPDATE video SET auto_redo_paid = true WHERE id = %(i)s", {"i": video})
    assert c.redo_check(str(video), "DRAFT_GEN", None)["autoPaid"] is True


def test_record_hold_is_what_the_dashboard_reads(acts):
    db, c, _, video = acts
    c.record_stage(str(video), "DRAFT_GEN", 1, "AUTO", "FAILED", "failed", None, None)
    c.record_hold(str(video), "DRAFT_GEN", 1, "save crashed", True, True, None)
    row = db.rows("SELECT held_reason, can_resume, has_stored_result FROM stage_run WHERE video_id = %(v)s", {"v": video})[0]
    assert row == {"held_reason": "save crashed", "can_resume": True, "has_stored_result": True}


def test_the_tolerant_parser_defaults_missing_optional_fields_and_names_them():
    from dataclasses import dataclass

    from studio.media.types import ClipResult
    from studio.media.wire import from_json

    @dataclass
    class Item:
        name: str
        note: str | None  # no default: a sender that forgets it must not crash the save

    @dataclass
    class Batch:
        items: list[Item]
        tags: list[str]
        total: int

    dropped: list[str] = []
    b = from_json(Batch, {"items": [{"name": "a"}, {"name": "b", "note": "x"}], "total": 2}, dropped)
    assert b.tags == [] and b.items[0].note is None and b.items[1].note == "x"
    assert sorted(dropped) == ["items.0.note", "tags"]
    with pytest.raises(TypeError):  # a required scalar is never guessed
        from_json(Batch, {"items": [], "tags": []})
    assert from_json(ClipResult, {"clips": [], "containers": 1, "stopped": False}).cold_starts is None
