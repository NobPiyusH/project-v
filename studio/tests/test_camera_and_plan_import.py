"""First live Short blockers (25 Nov 2026): camera presets on the pod video route, a video that starts from a plan file, the plan's own beat
timing, and the $3.50/day GPU cap. Fakes only: no network, no pod."""

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.api.video_wiring import build_video_context
from studio.config import Settings
from studio.gpu.broker.config import BrokerConfig
from studio.media.wire import to_json
from studio.planner import imported
from studio.planner.imported import ImportedDirector, ImportedPlanner, ImportedWriter
from studio.production import camera
from studio.production.shot_stage import ShotStage
from studio.production.video_gpu import VideoGpu
from studio.production.video_stage import VideoStage
from studio.tests.pworld import make_deps, seed_video
from studio.tests.test_shot_stage import FakeDirector, db_concept, voiced_script
from studio.tests.test_video_api import FakeGateway, FakeLlm, Urls, make_world
from studio.tests.test_video_stage import RecordingPod, shot

ROOT = Path(__file__).resolve().parents[2]
PLAN = json.loads((ROOT / "docs/shorts/moon-10x-closer-plan.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------------------- camera assignment (pure)

def test_a_move_that_names_a_tested_preset_is_kept_and_free_text_gets_a_varied_tested_one():
    got = camera.assign(["zoom_in", "Pan Left", "slow push-in on the town", None, "drift-right"])
    assert got[:2] == ["zoom_in", "pan_left"] and got[4] == "drift_right"
    assert all(n in camera.presets_module().TESTED for n in got)
    assert all(a != b for a, b in zip(got, got[1:], strict=False)), "never the same move twice in a row"


def test_an_untested_preset_is_refused_when_a_plan_named_it_and_replaced_when_it_was_free_text():
    with pytest.raises(ValueError, match="not tested"):
        camera.assign(["zoom_in", "pan_down"], strict=True)
    got = camera.assign(["zoom_in", "pan down"])
    assert got[1] != "pan_down" and got[1] in camera.presets_module().TESTED
    assert camera.assign(["pan_down"], allow_untested=True, strict=True) == ["pan_down"]


# ---------------------------------------------------------------------------------------------------- the video stage (pod route)

def vertical(db, n=3, **kw):
    video = seed_video(db, duration=15, aspect="9:16")
    ids = [shot(db, video, i, 5.0, priority="hero" if i == 0 else "standard") for i in range(n)]
    return video, ids


def cam_stage(db, **kw):
    kw.setdefault("final_provider", "wan")
    return VideoStage(make_deps(db), video_gpu=VideoGpu(RecordingPod(), "broker", "broker", "", 5), camera_presets=True, **kw)


def set_moves(db, ids, moves):
    for i, m in zip(ids, moves, strict=True):
        db.execute("UPDATE shot SET camera_move = %(m)s WHERE id = %(i)s", {"m": m, "i": i})


def test_camera_clips_are_720x1280_81_frames_one_take_each_and_chain_two_beats_at_a_time(scratch_db):
    video, ids = vertical(scratch_db)
    set_moves(scratch_db, ids, ["zoom_in", "pan_left", "zoom_in"])
    st = cam_stage(scratch_db)
    req = st.clip_request(video, "draft", None)
    assert [(j.camera, j.width, j.height, j.num_frames, j.mode, j.take) for j in req.jobs] == [
        ("zoom_in", 720, 1280, 81, "camera", 1), ("pan_left", 720, 1280, 81, "camera", 1), ("zoom_in", 720, 1280, 81, "camera", 1)]
    assert (req.provider, req.chain, req.chain_group) == ("pod", True, 2), "beat 3 restarts from its own keyframe (chains of two)"
    wire = to_json(req)
    assert wire["jobs"][0]["camera"] == "zoom_in" and wire["chainGroup"] == 2 and wire["chain"] is True
    assert st.estimate(video, "final", None).clips == 0, "a camera clip is final quality: no final pass unless a shot is picked"


def test_camera_estimate_uses_the_measured_beat_time_not_the_svi_scaling(scratch_db):
    video, _ = vertical(scratch_db)
    st = cam_stage(scratch_db)
    jobs = st.jobs(video, "draft", None)
    from studio.production import video_gpu as g

    assert st.video_gpu.clip_seconds(video, jobs) >= g.CAMERA_SETUP_SECONDS + 3 * g.CAMERA_SECONDS_PER_81


def test_free_text_camera_moves_still_get_tested_presets_and_a_single_shot_redo_does_not_chain(scratch_db):
    video, ids = vertical(scratch_db)
    set_moves(scratch_db, ids, ["slow push-in", "pan left", None])
    st = cam_stage(scratch_db)
    jobs = st.jobs(video, "draft", None)
    assert [j.camera for j in jobs][1] == "pan_left" and all(j.camera for j in jobs)
    redo = st.clip_request(video, "draft", [ids[1]])
    assert len(redo.jobs) == 1 and redo.chain is False, "no previous clip in the batch: start from the shot's own keyframe"


def test_flag_off_horizontal_videos_and_diagram_lane_keep_the_old_route(scratch_db):
    video, ids = vertical(scratch_db)
    off = VideoStage(make_deps(scratch_db), video_gpu=VideoGpu(RecordingPod(), "broker", "broker", "", 5), final_provider="wan")
    assert not any(j.camera for j in off.jobs(video, "draft", None))
    wide = seed_video(scratch_db, duration=60, aspect="16:9")
    shot(scratch_db, wide, 0, 4.0)
    assert not any(j.camera for j in cam_stage(scratch_db).jobs(wide, "draft", None)), "Fun Camera is tested vertically only"


def test_the_untested_preset_in_a_plan_is_refused_by_the_stage_too(scratch_db):
    video, ids = vertical(scratch_db)
    set_moves(scratch_db, ids, ["zoom_in", "pan_down", "zoom_in"])
    scratch_db.execute("UPDATE video SET plan_json = plan_json || CAST(%(i)s AS jsonb) WHERE id = %(v)s",
                       {"v": video, "i": json.dumps({"imported": PLAN})})
    with pytest.raises(ValueError, match="not tested"):
        cam_stage(scratch_db).jobs(video, "draft", None)


# ---------------------------------------------------------------------------------------------------- plan import

def test_the_moon_plan_parses_with_five_second_beats_and_its_script_starts():
    imp = imported.parse(PLAN)
    assert [(b.startSec, b.durationSec, b.camera) for b in imp.beats] == [(0.0, 5.0, "slow_push"), (5.0, 5.0, "slow_push")]
    assert imp.total_sec() == 10 and imp.line_starts() == {0: 0.3, 1: 5.2}
    assert imp.script().lines[1].text == PLAN["script"][1]["text"]
    sp = imp.shot_plan()
    assert [s.cameraMove for s in sp.shots] == ["slow_push", "slow_push"] and sp.bank.locations[0].id == "moon-shore"


def test_a_bad_plan_lists_every_problem():
    bad = json.loads(json.dumps(PLAN))
    bad["generation"]["cameraPresets"] = ["slow_push", "pan_down"]
    bad["script"][0]["beat"] = 9
    bad["scenes"][0]["beats"][0]["locationIds"] = ["nowhere"]
    with pytest.raises(ValueError) as e:
        imported.parse(bad)
    assert "not tested" in str(e.value) and "unknown location" in str(e.value) and "beat 9 does not exist" in str(e.value)
    bad["generation"]["allowUntested"] = True
    bad["generation"]["cameraPresets"] = ["slow_push", "pan_down"]
    bad["script"][0]["beat"] = 1
    bad["scenes"][0]["beats"][0]["locationIds"] = ["moon-shore"]
    assert imported.parse(bad).beats[1].camera == "pan_down"


def test_plan_files_are_read_only_from_docs_shorts():
    assert imported.read_plan_file("moon-10x-closer-plan.json")["title"].startswith("What if the Moon")
    for name in ("../../.env", "C:/Windows/win.ini", "docs/HANDOVER.md", "nope.json"):
        with pytest.raises(ValueError):
            imported.read_plan_file(name)


@pytest.fixture
def api(scratch_db):
    make_world(scratch_db)
    s = Settings(video_routes=True)
    gw = FakeGateway()
    v = build_video_context(scratch_db, s, llm=FakeLlm(), gateway=gw, media_urls=Urls(), researcher=object())
    return type("A", (), {"c": TestClient(create_app(db=scratch_db, settings=s, video_ctx=v), raise_server_exceptions=False),
                          "v": v, "db": scratch_db, "gw": gw})()


def test_post_videos_with_a_plan_file_stores_it_and_the_stages_use_it_without_an_llm(api):
    # line 2 of the file runs ~6.3 s in a 4.8 s beat (the narration check rejects it, test_narration_check.py): shortened here to fit
    fitted = json.loads(json.dumps(PLAN))
    fitted["script"][1]["text"] = "Tides would climb far higher, flooding coasts twice a day."
    r = api.c.post("/api/videos", json={"plan": fitted, "defaultMode": "MANUAL"})
    assert r.status_code == 201, r.text
    vid = uuid.UUID(r.json()["id"])
    row = api.db.rows("SELECT prompt, plan_json -> 'imported' ->> 'title' AS t FROM video WHERE id = %(v)s", {"v": vid})[0]
    assert row["t"] == PLAN["title"] and row["prompt"] == PLAN["video"]["title"]
    assert api.c.get(f"/api/videos/{vid}").status_code == 200, "a video with only the imported plan can be read before PLAN ran"
    planner = api.v.planner
    assert isinstance(planner, ImportedPlanner)
    out = planner.plan(vid, "x", None)
    assert out.source == "imported plan" and (out.plan.durationSec, out.plan.aspectRatio) == (10, "9:16")
    assert [s.name for s in out.stages][:2] == ["PLAN", "CONCEPT"], "no research: the plan says so"
    # the PLAN activity writes its plan next to the imported file, not over it
    from studio.workflow.activities import ControlActivities

    acts = ControlActivities(api.db, planner, researcher=None, inspector=None, creative=None, research=None, story=None, music=None,
                             voice=None, shots=None, stock=None, animatic=None, video=None, footage=None, assembly=None, finish=None, sharpen=None)
    acts.plan(str(vid), "x", None, 1)
    kept = api.db.rows("SELECT plan_json ? 'imported' AS kept, plan_json -> 'plan' ->> 'title' AS t FROM video WHERE id = %(v)s", {"v": vid})[0]
    assert kept == {"kept": True, "t": PLAN["video"]["title"]}
    assert imported.load(api.db, vid) is not None


def test_post_videos_rejects_a_bad_or_foreign_plan_and_creates_nothing(api):
    n = api.db.rows("SELECT count(*) AS n FROM video")[0]["n"]
    assert api.c.post("/api/videos", json={"planFile": "../../.env"}).status_code == 400
    bad = json.loads(json.dumps(PLAN))
    bad["generation"]["cameraPresets"] = ["slow_push", "fast_push"]
    r = api.c.post("/api/videos", json={"plan": bad})
    assert r.status_code == 400 and "not tested" in r.json()["detail"]
    assert api.db.rows("SELECT count(*) AS n FROM video")[0]["n"] == n
    assert api.c.post("/api/videos", json={"prompt": "a calm sea"}).status_code == 201, "the prompt route is unchanged"


def test_without_an_imported_plan_the_wrappers_use_the_real_planner_writer_and_director(scratch_db):
    video = seed_video(scratch_db)
    marker = object()

    class Inner:
        def plan(self, *a):
            return marker

        concept = script = plan

    for wrapper in (ImportedPlanner(Inner(), scratch_db), ImportedDirector(Inner(), scratch_db)):
        assert wrapper.plan(video, 1, 2, 3, 4, 5, 6) is marker if isinstance(wrapper, ImportedDirector) else wrapper.plan(video, "p", None) is marker
    w = ImportedWriter(Inner(), scratch_db)
    assert w.concept(video, 1, 2, 3, 4) is marker and w.script(video, 1, 2, 3, 4, 5) is marker


# ---------------------------------------------------------------------------------------------------- timing from the plan's beats

def test_a_plan_video_is_timed_from_its_five_second_beats_not_from_the_narration(scratch_db):
    video = seed_video(scratch_db, duration=10)
    scratch_db.execute("UPDATE video SET plan_json = plan_json || CAST(%(i)s AS jsonb) WHERE id = %(v)s",
                       {"v": video, "i": json.dumps({"imported": PLAN})})
    db_concept(scratch_db, video)
    voiced_script(scratch_db, video, [3.4, 3.6])  # narration shorter than the 5 s beats
    imp = imported.parse(PLAN)
    deps = make_deps(scratch_db, None, FakeDirector(imp.shot_plan().shots))
    deps.director = type("D", (), {"plan": lambda self, *a: imp.shot_plan()})()
    ShotStage(deps).shot_plan(video, None)
    rows = ShotStage(deps).shots(video)
    assert [float(r["duration_target_sec"]) for r in rows] == [5.0, 5.0]
    assert [json.loads(r["prompt_json"])["startSec"] for r in rows] == [0.0, 5.0]
    assert [r["camera_move"] for r in rows] == ["slow_push", "slow_push"]
    starts = json.loads(scratch_db.rows("SELECT beat_sheet_json::text AS b FROM script WHERE video_id = %(v)s ORDER BY version DESC LIMIT 1",
                                        {"v": video})[0]["b"])
    assert starts["lineStarts"] == {"0": 0.3, "1": 5.2}, "each line starts inside its own beat, as the plan says"


# ---------------------------------------------------------------------------------------------------- GPU cap

def test_the_studio_daily_gpu_cap_is_three_fifty():
    cfg = BrokerConfig.load(ROOT / "config")
    assert cfg.daily_cap_usd == 3.50
    assert cfg.total_cap_usd == 20 and cfg.cap_usd("runpod") == 12, "monthly caps unchanged"


def test_the_creative_directors_shot_plan_review_is_skipped_for_a_plan_video(scratch_db):
    """It is a paid LLM call that rewrites weak shot prompts in place: it must not touch the owner's plan."""
    from studio.production.creative_director import CreativeDirector

    video, _ = vertical(scratch_db)
    scratch_db.execute("UPDATE video SET plan_json = plan_json || CAST(%(i)s AS jsonb) WHERE id = %(v)s", {"v": video, "i": json.dumps({"imported": PLAN})})

    class Boom:
        def review_shot_plan(self, *a):
            raise AssertionError("no LLM call for an imported plan")

    assert CreativeDirector(make_deps(scratch_db), Boom(), "m").review_shot_plan(video, 1) == []


def test_a_scene_that_changes_camera_move_inside_it_gets_a_warning():
    from studio.planner import imported

    doc = {"generation": {"cameraPresets": ["slow_push", "slow_push", "pan_right", "drift_right"]},
           "scenes": [{"beats": [{}, {}]}, {"beats": [{}, {}]}]}
    w = imported.camera_warnings(doc)
    assert len(w) == 1 and w[0].startswith("scene 2 changes camera move") and "pan_right then drift_right" in w[0]
    assert imported.camera_warnings({"scenes": [{"beats": [{}]}]}) == []
