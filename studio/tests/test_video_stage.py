"""Clips: estimates, requests, saving and costs (ports of the clip tests in ProductionPersistenceTest and of VideoLanesTest)."""

import json
import uuid
from decimal import Decimal

import pytest

from studio.ledger.budget_guard import BudgetExceededError
from studio.media.types import ClipOutput, ClipResult
from studio.production import video_stage as vs
from studio.production.video_gpu import VideoGpu
from studio.production.video_stage import VideoStage
from studio.tests.pworld import make_deps, scalar, seed_video
from studio.tests.test_shot_stage import FakePod, FakeRouter


@pytest.fixture
def world(scratch_db):
    scratch_db.execute("DELETE FROM cost_entry")
    return scratch_db, seed_video(scratch_db, duration=60)


def scene_of(db, video):
    rows = db.rows("SELECT id FROM scene WHERE video_id = %(v)s", {"v": video})
    if rows:
        return rows[0]["id"]
    sid = uuid.uuid4()
    db.execute("INSERT INTO scene (id, video_id, ord, title) VALUES (%(s)s, %(v)s, 0, 'S')", {"s": sid, "v": video})
    return sid


def shot(db, video, ord_, seconds, prompt=None, keyframe=True, priority="standard"):
    sid, image = uuid.uuid4(), None
    if keyframe:
        image = uuid.uuid4()
        db.execute("INSERT INTO asset (id, video_id, kind, uri_hot) VALUES (%(a)s, %(v)s, 'image', 's3://b/k.png')", {"a": image, "v": video})
    db.execute("INSERT INTO shot (id, scene_id, ord, duration_target_sec, description, prompt_json, keyframe_asset_id, priority) "
               "VALUES (%(i)s, %(s)s, %(o)s, %(d)s, 'stars wheel overhead', CAST(%(p)s AS jsonb), %(k)s, %(pr)s)",
               {"i": sid, "s": scene_of(db, video), "o": ord_, "d": seconds, "p": json.dumps(prompt or {"motionPrompt": "slow push in"}), "k": image,
                "pr": priority})
    return sid


def stage(db, **kw):
    kw.setdefault("final_provider", "wan")  # the Java persistence tests build the stage with wan/wan
    return VideoStage(make_deps(db), **kw)


def out(shot_id, take, kind, uri, gpu, frames, fps, mode, error=None, tokens=0):
    return ClipOutput(str(shot_id), take, kind, uri, gpu, frames, fps, mode, error, tokens)


def test_frames_are_4n_plus_1_between_two_seconds_and_wans_limit():
    assert (vs.frames(1.2), vs.frames(3.0), vs.frames(9.0)) == (33, 49, 81)
    assert (vs.containers("draft", 24), vs.containers("final", 24), vs.containers("final", 2), vs.containers("draft", 0)) == (1, 4, 2, 0)
    assert (vs.omni_seconds(1.0), vs.omni_seconds(4.2), vs.omni_seconds(30)) == (3, 5, 10)


def test_clips_are_estimated_rendered_and_costed_per_shot(world):
    db, video = world
    a, b = shot(db, video, 0, 3.0), shot(db, video, 1, 6.0)
    st = stage(db)
    est = st.estimate(video, "draft", None)
    assert est.clips == 2 and est.needs_approval, "auto-approve is 0: the owner always says yes first"
    assert est.summary.startswith("Ready to make 2 draft clips (fast, 480p) on Modal H100: about ₹")
    assert st.estimate(video, "final", None).clips == 0 and "No shots need final clips (hybrid, 720p)." in st.estimate(video, "final", None).summary
    req = st.clip_request(video, "draft", None)
    job = req.jobs[0]
    assert (job.width, job.height, job.mode, req.containers, req.provider) == (480, 832, "fast", 1, "wan")
    assert job.prompt.startswith("slow push in. stars wheel overhead. Visual style:")
    saved = st.save_clips(video, req, ClipResult([out(a, 1, "draft", "s3://b/a.mp4", 50, 49, 16, "fast"),
                                                  out(b, 1, "draft", None, 0, 0, 0, "fast", "stopped")], 2, True))
    assert saved.summary.startswith("1/2 draft clips (fast, 480p) in 50 s of GPU render time; stopped by owner before 1 finished")
    assert scalar(db, "SELECT clip_status FROM shot WHERE id = %(s)s", s=a) == "READY"
    assert scalar(db, "SELECT clip_status FROM shot WHERE id = %(s)s", s=b) == "STOPPED"
    assert scalar(db, "SELECT count(*) FROM asset x JOIN provenance p ON p.id = x.provenance_id WHERE x.shot_id = %(s)s AND x.kind = 'video' "
                      "AND x.clip_kind = 'draft' AND p.licence_id = '00000000-0000-0000-0000-00000000a004'", s=a) == 1
    assert scalar(db, "SELECT count(*) FROM cost_entry WHERE video_id = %(v)s AND provider_id = 'modal_h100'", v=video) == 2, "the clip + the start-up"
    assert st.estimate(video, "final", None).clips == 1, "a final for the shot with a draft"
    st.set_final_wanted(a, False)
    assert st.estimate(video, "final", None).clips == 0, "kept draft: no final"
    assert len(st.clip_versions(a)) == 1


def test_hero_shots_get_two_draft_takes_and_the_first_good_one_is_used(world):
    db, video = world
    hero, plain = shot(db, video, 0, 3.0, priority="hero"), shot(db, video, 1, 3.0)
    st = stage(db)
    req = st.clip_request(video, "draft", None)
    assert [f"{j.shot_id}:{j.take}" for j in req.jobs] == [f"{hero}:1", f"{hero}:2", f"{plain}:1"]
    assert req.jobs[0].seed != req.jobs[1].seed, "different seeds per take"
    saved = st.save_clips(video, req, ClipResult([out(hero, 1, "draft", None, 10, 0, 0, "fast", "CUDA error"),
                                                  out(hero, 2, "draft", "s3://b/h2.mp4", 12, 49, 16, "fast"),
                                                  out(plain, 1, "draft", "s3://b/p1.mp4", 12, 49, 16, "fast")], 1, False))
    assert "1 hero shot has takes to compare" in saved.summary and "1 failed: shot 1: CUDA error" in saved.summary
    in_use = scalar(db, "SELECT draft_clip_asset_id FROM shot WHERE id = %(s)s", s=hero)
    assert scalar(db, "SELECT uri_hot FROM asset WHERE id = %(a)s", a=in_use) == "s3://b/h2.mp4", "take 1 failed, so take 2 is the one in use"
    assert st.estimate(video, "final", None).clips == 2, "finals: one per shot, not per take"


def test_a_batch_with_no_clip_fails_and_a_picked_take_can_be_switched(world):
    db, video = world
    a = shot(db, video, 0, 3.0)
    st = stage(db)
    req = st.clip_request(video, "draft", None)
    with pytest.raises(RuntimeError, match="No draft clips were made: shot 1: boom"):
        st.save_clips(video, req, ClipResult([out(a, 1, "draft", None, 0, 0, 0, "fast", "boom")], 1, False))
    st.save_clips(video, req, ClipResult([out(a, 1, "draft", "s3://b/x.mp4", 5, 49, 16, "fast")], 1, False))
    other = uuid.uuid4()
    with pytest.raises(RuntimeError, match="not a take of this shot"):
        st.select_clip(a, other)
    take = scalar(db, "SELECT id FROM asset WHERE shot_id = %(s)s AND kind = 'video'", s=a)
    st.select_clip(a, take)
    with pytest.raises(RuntimeError, match="No shots"):
        stage(db).clip_request(seed_video(db), "draft", None)


def test_the_monthly_gpu_cap_stops_clips(world):
    db, video = world
    shot(db, video, 0, 4.0)
    db.execute("INSERT INTO cost_entry (id, video_id, provider_id, amount_inr, units, unit_type, estimated, note) "
               "VALUES (%(i)s, %(v)s, 'modal_h100', 100000, 1, 'gpu_seconds', true, 'test: cap already used')", {"i": uuid.uuid4(), "v": video})
    with pytest.raises(BudgetExceededError):
        stage(db).clip_request(video, "draft", None)


def test_omni_finals_are_hero_only_and_priced_by_seconds(world):
    db, video = world
    hero, plain = shot(db, video, 0, 4.2, priority="hero"), shot(db, video, 1, 4.0)
    st = stage(db, draft_provider="omni", final_provider="omni")
    drafts = st.jobs(video, "draft", None)
    assert [(j.mode, j.num_frames, j.width, j.height) for j in drafts] == [("omni", 5 * 24, 720, 1280)] * 2 + [("omni", 4 * 24, 720, 1280)]  # hero twice (5 s), plain once (4 s)
    est = st.estimate(video, "draft", None)
    assert "Gemini Omni, 720p" in est.summary and est.gpu_seconds == 0 and est.needs_approval
    db.execute("UPDATE shot SET draft_clip_asset_id = (SELECT id FROM asset WHERE video_id = %(v)s LIMIT 1), final_wanted = true", {"v": video})
    finals = st.jobs(video, "final", None)
    assert [j.shot_id for j in finals] == [str(hero)] and plain is not None, "Omni finals only for hero shots"
    fe = st.estimate(video, "final", None)
    assert fe.summary.endswith('Shots: #1. Mark "keep the draft" on any whose Wan draft already looks right (₹0), then approve.')
    req = st.clip_request(video, "final", None)
    assert (req.provider, req.resolution, req.omni_model) == ("omni", "720p", "gemini-omni-1.2-flash")
    res = st.save_clips(video, req, ClipResult([out(hero, 1, "final", "s3://b/f.mp4", 0, 120, 24, "omni", None, 3000)], 0, False))
    assert "Gemini Omni, 720p" in res.summary
    assert scalar(db, "SELECT count(*) FROM cost_entry WHERE video_id = %(v)s AND provider_id = 'gemini_api'", v=video) == 1
    assert scalar(db, "SELECT p.provider FROM asset a JOIN provenance p ON p.id = a.provenance_id WHERE a.shot_id = %(s)s AND a.clip_kind = 'final'", s=hero) == "gemini_omni"


def test_the_router_decides_wan_or_omni_and_health_is_reported(world):
    db, video = world
    shot(db, video, 0, 4.0)
    router = FakeRouter("modal_wan")
    st = stage(db, draft_provider="omni", router=router)
    req = st.clip_request(video, "draft", None)
    assert req.provider == "wan", "the router overrode the owner's preferred provider (e.g. Gemini cap used up)"
    cap, cands, preferred = router.chosen[-1]
    assert cap == "video.i2v.draft" and preferred == "gemini_omni" and [c[0] for c in cands] == ["modal_wan", "gemini_omni"]
    shot_id = req.jobs[0].shot_id
    with pytest.raises(RuntimeError):
        st.save_clips(video, req, ClipResult([out(shot_id, 1, "draft", None, 0, 0, 0, "fast", "boom")], 1, False))
    assert router.batches[-1] == ("modal_wan", 0, 1, "shot 1: boom"), "reported even though the stage failed"


# ---------------------------------------------------------------------------------- lanes (port of VideoLanesTest)

DIAGRAM = {"lane": "diagram", "diagramSpec": {"title": "CRT", "duration": 8.0, "elements": [], "steps": []}}


@pytest.fixture
def tv(scratch_db):
    scratch_db.execute("DELETE FROM cost_entry")
    return scratch_db, seed_video(scratch_db, duration=180, aspect="16:9")


def test_diagram_shots_are_drawn_not_generated_and_counted_in_the_estimate(tv):
    db, video = tv
    shot(db, video, 0, 4.0)
    d = shot(db, video, 1, 8.0, DIAGRAM, keyframe=False)
    st = stage(db)
    assert st.estimate(video, "draft", None).clips == 2, "1 AI clip + 1 diagram: the stage is not skipped"
    req = st.clip_request(video, "draft", None)
    drawn = [j for j in req.jobs if j.mode == "diagram"]
    assert len(drawn) == 1 and drawn[0].shot_id == str(d) and drawn[0].num_frames == 192 and drawn[0].width == 1920
    assert '"title":"CRT"' in drawn[0].prompt and sum(1 for j in req.jobs if j.mode != "diagram") == 1
    assert st.estimate(video, "final", None).clips == 0, "a drawn clip is final quality: no final render"


def test_a_diagram_only_batch_needs_no_gpu_and_no_approval_money(tv):
    db, video = tv
    shot(db, video, 0, 8.0, DIAGRAM, keyframe=False)
    req = stage(db).clip_request(video, "draft", None)
    assert len(req.jobs) == 1 and req.estimate_inr == 0 and req.containers == 0


def test_a_saved_diagram_clip_is_an_own_render_with_provenance_and_no_modal_cost(tv):
    db, video = tv
    d = shot(db, video, 0, 6.0, DIAGRAM, keyframe=False)
    st = stage(db)
    req = st.clip_request(video, "draft", None)
    st.save_clips(video, req, ClipResult([out(d, 1, "draft", "s3://b/diagram.mp4", 0, 144, 24, "diagram", None)], 0, False, 0))
    assert scalar(db, "SELECT p.provider FROM asset a JOIN provenance p ON p.id = a.provenance_id WHERE a.shot_id = %(s)s", s=d) == "diagram_manim"
    assert scalar(db, "SELECT count(*) FROM cost_entry WHERE video_id = %(v)s", v=video) == 0
    assert scalar(db, "SELECT draft_clip_asset_id FROM shot WHERE id = %(s)s", s=d) is not None
    assert st.diagram_jobs(video, "draft", None) == [], "already drawn: not redrawn unless picked"
    assert len(st.diagram_jobs(video, "draft", [d])) == 1


class RecordingPod(FakePod):
    def clip_seconds(self, vid, frames):
        return 1200.0

    def inr(self, seconds):
        return Decimal("58.50")


def test_broker_drafts_run_on_the_pod_and_the_default_stays_unchanged(tv):
    db, video = tv
    shot(db, video, 0, 4.0)
    pod = RecordingPod()
    st = stage(db, video_gpu=VideoGpu(pod, "broker", "omni", "", 5))
    est = st.estimate(video, "draft", None)
    assert est.clips == 1 and "rented GPU pod" in est.summary
    assert not [c for c in pod.calls if c[0] == "session"], "an estimate never rents"
    req = st.clip_request(video, "draft", None)
    assert (req.provider, req.jobs[0].width, req.jobs[0].height, req.gpu_url, req.containers) == ("pod", 1280, 720, "http://pod", 1)
    assert req.estimate_inr == Decimal("58.50")
    # finals are not on the broker here: the unchanged Omni/Wan route (no pod)
    assert st.estimate(video, "final", None).clips == 0
    plain = stage(db).clip_request(video, "draft", None)
    assert plain.gpu_url is None and plain.jobs[0].width == 832, "default route unchanged (16:9 drafts: long side first)"


def test_pod_clips_are_saved_without_per_clip_modal_cost_and_the_gpu_is_released(tv):
    db, video = tv
    a = shot(db, video, 0, 4.0)
    pod = RecordingPod()
    st = stage(db, video_gpu=VideoGpu(pod, "broker", "omni", "", 5))
    req = st.clip_request(video, "draft", None)
    st.save_clips(video, req, ClipResult([out(a, 1, "draft", "s3://b/c.mp4", 80, 65, 16, "fast")], 1, False, 1))
    assert scalar(db, "SELECT p.provider FROM asset a JOIN provenance p ON p.id = a.provenance_id WHERE a.shot_id = %(s)s", s=a) == "gpu_pod"
    assert scalar(db, "SELECT count(*) FROM cost_entry WHERE video_id = %(v)s AND provider_id LIKE 'modal%%'", v=video) == 0, \
        "the rental is booked when the broker ends it, not per clip or per start-up"
    st.release_gpu(video, "clip batch saved")
    assert ("release", "clip batch saved") in pod.calls


def test_a_videos_rental_is_booked_on_the_videos_ledger(tv):
    from studio.gpu.broker.records import PgBrokerRecords

    db, video = tv
    records = PgBrokerRecords(db, make_deps(db).pricing)
    records.cost(video, "runpod", 0.5, 0.34, "GPU rental pod-1 ended: clip batch saved")
    records.decision(video, "video.i2v.dream", [], "runpod", "cheapest")  # a Video: no dream routing row, no FK failure
    row = db.rows("SELECT video_id, dream_short_id, unit_type FROM cost_entry WHERE video_id = %(v)s", {"v": video})[0]
    assert row == {"video_id": video, "dream_short_id": None, "unit_type": "gpu_hour"}
