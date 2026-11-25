"""VideoService on a scratch Postgres (real schema) with a fake Temporal gateway: REDO_WITH, the guards, decisions recorded as taste signals."""

import json
import uuid

import pytest

from studio.video.service import REDO_WITH, CreateVideo, VideoService
from studio.workflow import queues
from studio.workflow.stage import Stage, StageMode


class FakeGateway:
    def __init__(self):
        self.started = []
        self.signals = []

    def start(self, workflow, workflow_id, arg):
        self.started.append((workflow, workflow_id, arg))

    def signal(self, workflow_id, name, *args):
        self.signals.append((workflow_id, name, args))


class FakeProducer:
    def __init__(self):
        self.decisions = []
        self.learned = []

    def record_owner_decision(self, vid, stage, decision, notes):
        self.decisions.append((stage, decision, notes))

    def learn(self, vid, stage):
        self.learned.append(stage)


@pytest.fixture
def svc(scratch_db):
    gw, prod = FakeGateway(), FakeProducer()
    return VideoService(scratch_db, gw, prod, spawn=lambda fn: fn()), gw, prod, scratch_db


def set_status(db, vid, status, stage=None):
    db.execute("UPDATE video SET status = %(s)s, current_stage = %(st)s WHERE id = %(id)s", {"s": status, "st": stage, "id": vid})


def test_redo_with_matches_java():
    assert REDO_WITH[Stage.SCRIPT] == [Stage.SCRIPT, Stage.NARRATION, Stage.SHOT_PLAN, Stage.KEYFRAMES, Stage.ANIMATIC]
    assert REDO_WITH[Stage.NARRATION] == [Stage.NARRATION, Stage.ANIMATIC]
    assert REDO_WITH[Stage.UPSCALE] == [Stage.UPSCALE, Stage.ASSEMBLY, Stage.MOON_LAYER, Stage.INTERPOLATE]  # the Moon layer skips itself without a moon block
    assert REDO_WITH[Stage.DRAFT_GEN] == [Stage.DRAFT_GEN]  # clips cost GPU money: each runs alone
    assert Stage.PUBLISH not in REDO_WITH and Stage.PLAN not in REDO_WITH


def test_create_inserts_the_row_then_starts_the_workflow(svc):
    s, gw, _, db = svc
    vid = s.create(CreateVideo("  a calm sea  ", StageMode.REVIEW, {Stage.SCRIPT: StageMode.MANUAL}, 5000))
    row = db.rows("SELECT prompt, status, modes_json::text AS m, workflow_id, review_minutes FROM video WHERE id = %(i)s", {"i": vid})[0]
    assert row["prompt"] == "a calm sea" and row["status"] == "CREATED" and row["workflow_id"] == f"video-{vid}"
    assert row["review_minutes"] == 24 * 60  # capped at a day
    assert json.loads(row["m"]) == {"default": "REVIEW", "stages": {"SCRIPT": "MANUAL"}}
    wf, wid, req = gw.started[0]
    assert wf == queues.VIDEO_WORKFLOW and wid == f"video-{vid}" and req.only_stages is None and req.review_minutes == 24 * 60


def test_review_minutes_default_to_ten(svc):
    s, _, _, db = svc
    vid = s.create(CreateVideo("x", None, None, 0))
    assert db.rows("SELECT review_minutes FROM video WHERE id = %(i)s", {"i": vid})[0]["review_minutes"] == 10


def test_rerun_refuses_a_video_still_in_production_and_unbuilt_stages(svc):
    s, gw, _, db = svc
    vid = s.create(CreateVideo("x"))
    with pytest.raises(RuntimeError, match="still in production"):
        s.rerun(vid, Stage.SCRIPT)
    set_status(db, vid, "COMPLETED")
    with pytest.raises(RuntimeError, match="can't be redone yet"):
        s.rerun(vid, Stage.PUBLISH)
    assert len(gw.started) == 1  # only the create


def test_rerun_starts_exactly_the_dependent_stages_and_records_it(svc):
    s, gw, _, db = svc
    vid = s.create(CreateVideo("x", StageMode.MANUAL))
    set_status(db, vid, "COMPLETED")
    stages = s.rerun(vid, Stage.NARRATION)
    assert stages == [Stage.NARRATION, Stage.ANIMATIC]
    _, wid, req = gw.started[-1]
    assert wid == f"video-{vid}" and req.only_stages == stages and req.default_mode == StageMode.MANUAL
    row = db.rows("SELECT status, current_stage FROM video WHERE id = %(i)s", {"i": vid})[0]
    assert row == {"status": "RUNNING", "current_stage": "NARRATION"}
    a = db.rows("SELECT stage, decision, notes, actor FROM approval WHERE video_id = %(i)s", {"i": vid})[0]
    assert a == {"stage": "NARRATION", "decision": "rerun", "notes": "[NARRATION, ANIMATIC]", "actor": "owner"}
    p = db.rows("SELECT kind, chosen FROM preference_event WHERE video_id = %(i)s", {"i": vid})[0]
    assert p == {"kind": "rating", "chosen": "[NARRATION, ANIMATIC]"}


def test_unknown_video_is_a_lookup_error(svc):
    s, *_ = svc
    with pytest.raises(LookupError):
        s.rerun(uuid.uuid4(), Stage.SCRIPT)


def test_shot_redo_guards_and_workflow(svc):
    s, gw, _, db = svc
    vid = s.create(CreateVideo("x"))
    with pytest.raises(RuntimeError, match="at least one shot"):
        s.redo_shots(vid, [], None, None)
    set_status(db, vid, "RUNNING", "KEYFRAMES")
    with pytest.raises(RuntimeError, match="being made right now"):
        s.redo_shots(vid, [uuid.uuid4()], None, None)
    set_status(db, vid, "COMPLETED", "ASSEMBLY")
    with pytest.raises(RuntimeError, match="policy must be"):
        s.redo_shots(vid, [uuid.uuid4()], None, "cheap")
    shot = uuid.uuid4()
    wid = s.redo_shots(vid, [shot], "warmer", "premium")
    wf, started_id, arg = gw.started[-1]
    assert wf == queues.SHOT_REDO_WORKFLOW and started_id == wid and wid.startswith(f"shot-redo-{vid}-")
    assert arg == {"videoId": str(vid), "shotIds": [str(shot)], "ownerNotes": "warmer", "policy": "PREMIUM"}
    assert db.rows("SELECT notes FROM approval WHERE decision = 'shot_redo'")[0]["notes"] == "warmer [1 shot(s), PREMIUM]"


def test_clip_redo_is_blocked_while_the_main_run_makes_clips(svc):
    s, gw, _, db = svc
    vid = s.create(CreateVideo("x"))
    set_status(db, vid, "RUNNING", "FINAL_GEN")
    with pytest.raises(RuntimeError, match="Clips are being made"):
        s.redo_clips(vid, [uuid.uuid4()], "draft")
    set_status(db, vid, "WAITING", "FINAL_GEN")  # waiting for the owner is not "being made"
    with pytest.raises(RuntimeError, match="draft or final"):
        s.redo_clips(vid, [uuid.uuid4()], "4k")
    s.redo_clips(vid, [uuid.uuid4()], "final")
    assert gw.started[-1][0] == queues.CLIP_REDO_WORKFLOW
    assert db.rows("SELECT stage FROM approval WHERE decision = 'clip_redo'")[0]["stage"] == "FINAL_GEN"


def test_voice_redo_and_review_images_guards(svc):
    s, gw, _, db = svc
    vid = s.create(CreateVideo("x"))
    with pytest.raises(RuntimeError, match="no images to review"):
        s.review_images(vid)
    set_status(db, vid, "RUNNING", "NARRATION")
    with pytest.raises(RuntimeError, match="voice or the animatic"):
        s.redo_lines(vid, [1], None)
    set_status(db, vid, "COMPLETED")
    s.redo_lines(vid, [1, 3], "slower")
    assert gw.started[-1][2]["lineIndexes"] == [1, 3]
    assert db.rows("SELECT notes FROM approval WHERE decision = 'voice_redo'")[0]["notes"] == "slower [1, 3]"


def test_approve_signals_the_workflow_records_and_teaches_the_producer(svc):
    s, gw, prod, db = svc
    vid = s.create(CreateVideo("x"))
    s.approve(vid, Stage.SCRIPT, "good")
    s.reject(vid, Stage.SCRIPT, None)
    assert gw.signals == [(f"video-{vid}", "approve", (Stage.SCRIPT, "good")), (f"video-{vid}", "reject", (Stage.SCRIPT, None))]
    assert prod.decisions == [("SCRIPT", "approve", "good"), ("SCRIPT", "reject", None)]
    assert prod.learned == ["SCRIPT"]  # only the decision with notes is a lesson


def test_set_mode_updates_the_mirror_and_all_modes_replaces_it(svc):
    s, gw, _, db = svc
    vid = s.create(CreateVideo("x"))
    s.set_mode(vid, Stage.MUSIC, StageMode.MANUAL)
    m = json.loads(db.rows("SELECT modes_json::text AS m FROM video WHERE id = %(i)s", {"i": vid})[0]["m"])
    assert m == {"default": "AUTO", "stages": {"MUSIC": "MANUAL"}}
    assert db.rows("SELECT kind FROM preference_event WHERE chosen = 'MANUAL'")[0]["kind"] == "mode_change"
    s.set_all_modes(vid, StageMode.REVIEW)
    m = json.loads(db.rows("SELECT modes_json::text AS m FROM video WHERE id = %(i)s", {"i": vid})[0]["m"])
    assert m == {"default": "REVIEW", "stages": {}}
    assert db.rows("SELECT stage FROM approval WHERE decision = 'mode_change' ORDER BY stage")[0]["stage"] == "ALL"
    assert [x[1] for x in gw.signals] == ["set_mode", "set_all_modes"]


def test_pause_and_review_window(svc):
    s, gw, _, db = svc
    vid = s.create(CreateVideo("x"))
    set_status(db, vid, "RUNNING")
    s.pause(vid)
    assert db.rows("SELECT status FROM video WHERE id = %(i)s", {"i": vid})[0]["status"] == "PAUSED"
    s.set_review_minutes(vid, 99999)
    assert db.rows("SELECT review_minutes FROM video WHERE id = %(i)s", {"i": vid})[0]["review_minutes"] == 1440
    assert gw.signals[-1] == (f"video-{vid}", "set_review_minutes", (1440,))


def test_detail_has_stage_runs(svc):
    s, _, _, db = svc
    vid = s.create(CreateVideo("x"))
    assert s.detail(vid)["stageRuns"] == [] and s.detail(vid)["prompt"] == "x"
    assert any(v["id"] == vid for v in s.list())
