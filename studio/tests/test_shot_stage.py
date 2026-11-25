"""Shot plan, keyframes, per-shot redo, the stock lane (port of StockLaneTest) and VideoGpu (port of VideoGpuTest)."""

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from studio.agents.types import PlannedShot, ShotPlan
from studio.media.types import KeyframeOutput, KeyframeResult, StockProvenance, StockResult, Usage
from studio.production.records import ImagePolicy, StoredLine, StoredScript
from studio.production.shot_stage import NO_WRITING, ShotStage, StockStage
from studio.production.support import diagram_shot, stock_shot
from studio.production.video_gpu import POD, PodSession, VideoGpu
from studio.tests.pworld import make_deps, scalar, seed_video

V = uuid.uuid4()


class FakeDirector:
    def __init__(self, shots):
        self.shots = shots
        self.calls = []

    def plan(self, video_id, plan, concept, lines, bpm, total, notes):
        self.calls.append((lines, bpm, total, notes))
        return ShotPlan(shots=self.shots)


class FakeRouter:
    def __init__(self, pick=None):  # None: the hero class goes to Gemini, the standard class to Z-Image
        self.pick, self.chosen, self.batches = pick, [], []

    def choose(self, video_id, stage, capability, candidates, preferred, log):
        self.chosen.append((capability, [(c.provider_id, c.estimate_inr) for c in candidates], preferred))
        pick = self.pick or ("gemini_image" if capability.endswith("hero") else "modal_zimage")
        return type("Choice", (), {"provider_id": pick})()

    def report_batch(self, provider_id, ok, failed, sample):
        self.batches.append((provider_id, ok, failed, sample))


@pytest.fixture
def world(scratch_db):
    scratch_db.execute("DELETE FROM cost_entry")
    return scratch_db, seed_video(scratch_db, duration=30)


def voiced_script(db, video, durations):
    lines = [StoredLine(i, f"Line {i}.", "Act 1", "p", True, None, f"s3://b/l{i}.wav", d, "READY", False, False, None, [], None, None)
             for i, d in enumerate(durations)]
    from studio.media.wire import to_json
    db.execute("UPDATE script SET beat_sheet_json = CAST(%(b)s AS jsonb) WHERE video_id = %(v)s",
               {"b": json.dumps(to_json(StoredScript(1, lines, "Charon", "calm", "strings", None, None))), "v": video})


def planned(i, line, dur, prio="standard", **kw):
    return PlannedShot(sceneTitle="Scene A" if i < 2 else "Scene B", lineIndex=line, durationSec=dur, shotType="wide", cameraMove="static",
                       description=f"d{i}", keyframePrompt=f"kp{i}", motionPrompt=f"mp{i}", priority=prio, **kw)


def stage_with(db, shots, **kw):
    deps = make_deps(db, None, FakeDirector(shots))
    deps.director = FakeDirector(shots)
    return ShotStage(deps, **kw), deps


def db_concept(db, video):
    db.execute("INSERT INTO concept (id, video_id, big_idea, clock_definition, acts_json, version) "
               "VALUES (%(i)s, %(v)s, 'idea', '{}', '[]', 1)", {"i": uuid.uuid4(), "v": video})


# ------------------------------------------------------------------------------------------------ shot plan


def test_the_plan_needs_a_concept_and_a_script_and_shots(world):
    db, video = world
    st, _ = stage_with(db, [planned(0, 0, 3)])
    with pytest.raises(RuntimeError, match="No concept yet"):
        st.shot_plan(video, None)
    db_concept(db, video)
    db.execute("DELETE FROM script")
    with pytest.raises(RuntimeError, match="No script yet"):
        st.shot_plan(video, None)
    video2 = seed_video(db, duration=30)
    db_concept(db, video2)
    empty, _ = stage_with(db, [])
    with pytest.raises(RuntimeError, match="no shots"):
        empty.shot_plan(video2, None)


def test_shot_plan_anchors_every_line_to_its_shot_and_stores_scenes_and_heroes(world):
    db, video = world
    db_concept(db, video)
    voiced_script(db, video, [4.0, 5.0])
    shots = [planned(0, 0, 3, "hero"), planned(1, 0, 4), planned(2, 1, 4, "hero"), planned(3, None, 3, "hero", sfxQuery=" rain ",
                                                                                           footageQuery="moon rock")]
    st, deps = stage_with(db, shots)
    res = st.shot_plan(video, "notes")
    assert deps.director.calls[0][3] == "notes" and deps.director.calls[0][0][0].duration_sec == 4.0
    rows = db.rows("SELECT s.ord, s.priority, s.duration_target_sec, s.prompt_json::text AS p, sc.title FROM shot s JOIN scene sc ON sc.id = s.scene_id "
                   "WHERE sc.video_id = %(v)s ORDER BY s.ord", {"v": video})
    assert [r["title"] for r in rows] == ["Scene A", "Scene A", "Scene B", "Scene B"]
    heroes = [r["ord"] for r in rows if r["priority"] == "hero"]
    assert len(heroes) == 1 and heroes == [0], "20% cap = 1 hero, the opening keeps it"
    p0, p2, p3 = (json.loads(r["p"]) for r in (rows[0], rows[2], rows[3]))
    starts = db.rows("SELECT beat_sheet_json->'lineStarts' AS ls, beat_sheet_json->'tempo' AS t FROM script WHERE video_id = %(v)s", {"v": video})[0]
    assert starts["ls"]["0"] == 0.4 and starts["ls"]["1"] == pytest.approx(0.4 + 4.0 + 0.5)
    assert p0["startSec"] == 0.0 and p2["startSec"] == pytest.approx(0.4 + 4.0 + 0.5 - 0.4, abs=0.001), "cut a beat before the first word"
    assert p3["sfxQuery"] == "rain" and p3["footageQuery"] == "moon rock" and "lane" not in p3
    total = sum(float(r["duration_target_sec"]) for r in rows)
    assert total == pytest.approx(30.0, abs=0.01) and res.summary.startswith("4 shots in 2 scenes over 30s (1 hero);")
    # a second plan replaces the first one's scenes
    st.shot_plan(video, None)
    assert scalar(db, "SELECT count(*) FROM scene WHERE video_id = %(v)s", v=video) == 2


def test_stock_and_diagram_lanes_only_mark_shots_when_switched_on(world):
    db, video = world
    db_concept(db, video)
    voiced_script(db, video, [4.0])
    shots = [planned(0, 0, 3, lane="stock"), planned(1, None, 3)]
    off, _ = stage_with(db, shots)
    off.shot_plan(video, None)
    assert not any(stock_shot(s) for s in off.shots(video)), "lane off: a stock pick is just an AI shot"
    on, _ = stage_with(db, shots, stock_lane=True)
    on.shot_plan(video, None)
    rows = on.shots(video)
    assert stock_shot(rows[0]) and json.loads(rows[0]["prompt_json"])["stockQuery"] == "d0" and not stock_shot(rows[1])
    assert not diagram_shot(rows[0])


# ------------------------------------------------------------------------------------------------ keyframes


def seeded_shots(db, video, priorities, lane=None):
    scene = uuid.uuid4()
    db.execute("INSERT INTO scene (id, video_id, ord, title) VALUES (%(s)s, %(v)s, 0, 'S')", {"s": scene, "v": video})
    ids = []
    for i, prio in enumerate(priorities):
        sid = uuid.uuid4()
        ids.append(sid)
        p = {"keyframePrompt": f"kp{i}", "referenceQuery": "Crab Nebula", "startSec": float(i)}
        if lane and i == 0:
            p["lane"] = lane
        db.execute("INSERT INTO shot (id, scene_id, ord, duration_target_sec, description, prompt_json, priority) "
                   "VALUES (%(i)s, %(s)s, %(o)s, 3, 'd', CAST(%(p)s AS jsonb), %(pr)s)", {"i": sid, "s": scene, "o": i, "p": json.dumps(p), "pr": prio})
    return ids


def test_keyframe_request_without_a_router_uses_the_pre_m3_rule(world):
    db, video = world
    ids = seeded_shots(db, video, ["hero", "standard"])
    st = ShotStage(make_deps(db))
    r = st.keyframe_request(video, "warmer light")
    assert [(j.provider, j.width, j.height, j.aspect_ratio) for j in r.jobs] == [("gemini_api", 768, 1344, "9:16"), ("modal_zimage", 768, 1344, "9:16")]
    assert r.jobs[0].prompt == f"kp0. Visual style: {st.d.presets.by_id_or_default('shorts_punchy').visual_style}{NO_WRITING}. Director's notes: warmer light"
    assert r.jobs[0].reference_query == "Crab Nebula" and r.jobs[1].reference_query == "", "references only help Gemini"
    assert r.jobs[0].seed > 0 and r.jobs[0].shot_id == str(ids[0]) and r.jobs[0].pod_model is None
    only = st.shot_keyframe_request(video, [ids[1]], None, ImagePolicy.PREMIUM)
    assert [j.provider for j in only.jobs] == ["gemini_api"]
    with pytest.raises(RuntimeError, match="None of those shots"):
        st.shot_keyframe_request(video, [uuid.uuid4()], None, ImagePolicy.AUTO)


def test_keyframe_request_skips_stock_and_diagram_shots_and_needs_shots(world):
    db, video = world
    st = ShotStage(make_deps(db))
    with pytest.raises(RuntimeError, match="run the shot plan first"):
        st.keyframe_request(video, None)
    seeded_shots(db, video, ["hero", "standard"], lane="stock")
    assert len(st.keyframe_request(video, None).jobs) == 1


def test_the_router_picks_the_provider_per_class_with_the_reserve_in_the_estimate(world):
    db, video = world
    seeded_shots(db, video, ["hero", "standard", "standard"])
    router = FakeRouter("modal_zimage")
    st = ShotStage(make_deps(db), router=router)
    r = st.keyframe_request(video, None)
    assert [j.provider for j in r.jobs] == ["modal_zimage"] * 3
    (cap1, c1, pref1), (cap2, c2, _) = router.chosen
    assert (cap1, cap2) == ("image.keyframe.hero", "image.keyframe.standard") and pref1 is None
    pricing = st.d.pricing
    assert c1[0] == ("gemini_image", pricing.image_inr("1K") + pricing.gemini_reserve_inr())
    assert c2[1] == ("modal_zimage", pricing.zimage_inr(2))


def test_save_keyframes_stores_assets_costs_and_reports_provider_health(world):
    db, video = world
    ids = seeded_shots(db, video, ["hero", "standard", "standard"])
    router = FakeRouter()
    st = ShotStage(make_deps(db), router=router)
    req = st.keyframe_request(video, None)
    router.batches.clear()
    res = st.save_keyframes(video, req, KeyframeResult([
        KeyframeOutput(req.jobs[0].shot_id, "s3://b/a.png", "gemini_api", "nano-banana", 7, 1.0, Usage(0, 0, 0, 0), None, ["Crab Nebula (NASA)"]),
        KeyframeOutput(req.jobs[1].shot_id, "s3://b/b.jpg", "modal_zimage", "z-image", 8, 2.0, None, None, None),
        KeyframeOutput(req.jobs[2].shot_id, None, "modal_zimage", None, None, None, None, "boom", None)], 12.5))
    assert res.summary == "2/3 keyframes (1 hero via Nano Banana, 1 via Z-Image on Modal); 1 failed: boom."
    assert scalar(db, "SELECT count(*) FROM shot WHERE keyframe_asset_id IS NOT NULL AND status = 'READY'") == 2
    assert db.rows("SELECT status, error FROM shot WHERE id = %(i)s", {"i": ids[2]})[0] == {"status": "FAILED", "error": "boom"}
    mimes = db.rows("SELECT mime_type FROM asset WHERE video_id = %(v)s AND kind = 'image' ORDER BY mime_type", {"v": video})
    assert [m["mime_type"] for m in mimes] == ["image/jpeg", "image/png"]
    notes = [r["note"] for r in db.rows("SELECT note FROM cost_entry WHERE video_id = %(v)s ORDER BY note", {"v": video})]
    assert notes == ["KEYFRAME hero (nano-banana)", "KEYFRAMES Z-Image (free Modal credit; excludes container start-up)"]
    assert ("gemini_image", 1, 0, None) in router.batches and ("modal_zimage", 1, 1, "boom") in router.batches
    versions = st.shot_versions(ids[0])
    assert len(versions) == 1 and versions[0]["active"] is True and versions[0]["provider"] == "gemini_api"


def test_a_batch_where_nothing_rendered_fails_but_still_reports_health(world):
    db, video = world
    seeded_shots(db, video, ["standard"])
    router = FakeRouter()
    st = ShotStage(make_deps(db), router=router)
    req = st.keyframe_request(video, None)
    router.batches.clear()
    with pytest.raises(RuntimeError, match="No keyframes were produced: out of credit"):
        st.save_keyframes(video, req, KeyframeResult([KeyframeOutput(req.jobs[0].shot_id, None, "modal_zimage", None, None, None, None,
                                                                    "out of credit", None)], 0))
    assert router.batches == [("gemini_image", 0, 0, None), ("modal_zimage", 0, 1, "out of credit")]
    assert db.rows("SELECT status FROM shot")[0]["status"] != "FAILED", "the stage failed, so its writes rolled back (as in Java)"


def test_shot_cards_owner_edits_and_version_switching(world):
    db, video = world
    ids = seeded_shots(db, video, ["standard"])
    st = ShotStage(make_deps(db))
    st.mark_shots(video, [ids[0], uuid.uuid4()], "REDOING", None)
    assert db.rows("SELECT status FROM shot WHERE id = %(i)s", {"i": ids[0]})[0]["status"] == "REDOING"
    st.edit_shot_prompt(ids[0], "  a quiet moon  ")
    p = json.loads(db.rows("SELECT prompt_json::text AS p FROM shot WHERE id = %(i)s", {"i": ids[0]})[0]["p"])
    assert p["keyframePrompt"] == "a quiet moon" and p["editedByOwner"] is True
    with pytest.raises(LookupError):
        st.edit_shot_prompt(uuid.uuid4(), "x")
    assert st.video_of_shot(ids[0]) == video
    with pytest.raises(LookupError):
        st.video_of_shot(uuid.uuid4())
    # a shot only accepts its own keyframe versions
    other = seeded_shots(db, video, ["standard"])[0]
    db.execute("INSERT INTO asset (id, video_id, kind, uri_hot, shot_id) VALUES (%(a)s, %(v)s, 'image', 's3://b/x.png', %(s)s)",
               {"a": (a := uuid.uuid4()), "v": video, "s": ids[0]})
    with pytest.raises(RuntimeError, match="not a version of this shot"):
        st.select_keyframe(other, a)
    st.select_keyframe(ids[0], a)
    row = db.rows("SELECT keyframe_asset_id, status FROM shot WHERE id = %(i)s", {"i": ids[0]})[0]
    assert row == {"keyframe_asset_id": a, "status": "READY"}


class FakePod:
    def __init__(self, enabled=True):
        self._enabled, self.calls, self.fail = enabled, [], None
        self.gate = None
        self.activity = None

    def enabled(self):
        return self._enabled

    def clip_seconds(self, vid, frames):
        self.calls.append(("clip_seconds", frames))
        return 100.0

    def picture_seconds(self, vid, n):
        self.calls.append(("picture_seconds", n))
        return 60.0

    def inr(self, seconds):
        return Decimal(seconds) * Decimal("0.5")

    def hold(self, vid):
        self.calls.append(("hold", vid))

    def unhold(self, vid):
        self.calls.append(("unhold", vid))

    def session(self, vid, hours):
        self.calls.append(("session", hours))
        if self.gate is not None:
            self.gate.wait(10)
        if self.fail:
            raise RuntimeError(self.fail)
        return PodSession("http://pod", 60)

    def release(self, vid, why):
        self.calls.append(("release", why))

    def last_activity(self, vid):
        return self.activity


def test_pod_keyframes_are_flux_on_the_rented_pod_and_released_after(world):
    db, video = world
    seeded_shots(db, video, ["hero", "standard"])
    pod = FakePod()
    router = FakeRouter()
    st = ShotStage(make_deps(db), router=router, video_gpu=VideoGpu(pod, "wan", "omni", "broker", 5))
    r = st.keyframe_request(video, None)
    assert [(j.provider, j.pod_model) for j in r.jobs] == [(POD, "flux2-klein")] * 2 and r.gpu_url == "http://pod" and r.ready_timeout_seconds == 60
    assert router.chosen == [("image.dream", [(POD, Decimal(30))], POD)]
    res = st.save_keyframes(video, r, KeyframeResult([KeyframeOutput(j.shot_id, "s3://b/k.png", "gpu_pod", "flux2-klein", 1, 1.0, None, None, None)
                                                      for j in r.jobs], 0, 20.0))
    assert "2 via FLUX.2 klein on a rented pod" in res.summary
    assert scalar(db, "SELECT count(*) FROM asset a JOIN provenance p ON p.id = a.provenance_id "
                      "WHERE a.video_id = %(v)s AND p.licence_id = '00000000-0000-0000-0000-00000000a013'", v=video) == 2
    st.release_gpu(video, "keyframes done")
    assert ("release", "keyframes done") in pod.calls


# ------------------------------------------------------------------------------------------------ VideoGpu (port of VideoGpuTest)


def test_defaults_keep_every_video_on_its_current_providers():
    g = VideoGpu(FakePod(), "wan", "omni", "default", 5)
    assert not (g.clips("draft") or g.clips("final") or g.keyframes())
    on = VideoGpu(FakePod(), "broker", "Broker", "broker", 5)
    assert on.clips("draft") and on.clips("final") and on.keyframes()


def test_clip_seconds_scale_with_pixels_against_the_reference_size():
    from studio.media.types import ClipJob

    def job(w, h):
        return ClipJob("s", 1, 1, "draft", "fast", "u", "p", w, h, 81, 1)

    pod = FakePod()
    g = VideoGpu(pod, "broker", "omni", "", 5)
    g.clip_seconds(V, [job(480, 832)])
    g.clip_seconds(V, [job(1280, 720)])
    import math
    assert pod.calls == [("clip_seconds", 81), ("clip_seconds", math.ceil(81 * math.pow(1280.0 * 720 / (480.0 * 832), 1.75)))]


def test_broker_route_needs_the_broker_on():
    pod = FakePod(enabled=False)
    with pytest.raises(RuntimeError, match="projectv.dream.gpu-provider=broker"):
        VideoGpu(pod, "broker", "omni", "", 5).session(V, 600)
    assert not [c for c in pod.calls if c[0] == "session"]


def test_session_holds_the_pod_and_release_lets_go_once():
    pod = FakePod()
    g = VideoGpu(pod, "broker", "omni", "", 5)
    assert g.session(V, 1800).url == "http://pod"
    g.release(V, "done")
    g.release(V, "again")
    assert pod.calls.count(("release", "done")) == 1 and ("release", "again") not in pod.calls and ("unhold", V) in pod.calls
    assert ("hold", V) in pod.calls and ("session", 0.5) in pod.calls


def test_a_failed_rental_leaves_no_hold():
    pod = FakePod()
    pod.fail = "daily cap"
    g = VideoGpu(pod, "broker", "omni", "", 5)
    with pytest.raises(RuntimeError, match="daily cap"):
        g.session(V, 900)
    assert ("unhold", V) in pod.calls
    g.release_idle(datetime.now(UTC) + timedelta(hours=1))
    assert not [c for c in pod.calls if c[0] == "release"]


def test_an_idle_hold_is_released_after_the_idle_minutes():
    pod = FakePod()
    used = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    pod.activity = used
    g = VideoGpu(pod, "broker", "omni", "", 5)
    g.session(V, 1800)
    g.release_idle(used + timedelta(minutes=4))
    assert not [c for c in pod.calls if c[0] == "release"]
    g.release_idle(used + timedelta(minutes=5))
    assert [c for c in pod.calls if c[0] == "release"]


def test_a_slow_pod_start_survives_an_attempt_and_the_retry_joins_it_instead_of_renting_again():
    pod = FakePod()
    pod.gate = threading.Event()
    g = VideoGpu(pod, "broker", "omni", "", 5)
    g.start_wait_seconds = 0.2
    with pytest.raises(RuntimeError, match="still starting"):  # attempt 1: pod still starting
        g.session(V, 600)
    g.release_idle(datetime.now(UTC) + timedelta(hours=1))  # a start in progress is never "idle"
    assert not [c for c in pod.calls if c[0] == "release"]
    pod.gate.set()
    g.start_wait_seconds = 10
    assert g.session(V, 600).url == "http://pod"  # attempt 2 joins the same start
    assert len([c for c in pod.calls if c[0] == "session"]) == 1


# ------------------------------------------------------------------------------------------------ stock lane (port of StockLaneTest)

STOCK = {"lane": "stock", "stockQuery": "living room lit television", "keyframePrompt": "k"}


def add_shot(db, video, ord_, prompt, scene):
    sid = uuid.uuid4()
    db.execute("INSERT INTO shot (id, scene_id, ord, duration_target_sec, description, prompt_json) "
               "VALUES (%(i)s, %(s)s, %(o)s, 5, 'a living room with a lit television', CAST(%(p)s AS jsonb))",
               {"i": sid, "s": scene, "o": ord_, "p": json.dumps(prompt)})
    return sid


@pytest.fixture
def tv(scratch_db):
    db = scratch_db
    video = seed_video(db, duration=180, aspect="16:9")
    scene = uuid.uuid4()
    db.execute("INSERT INTO scene (id, video_id, ord, title) VALUES (%(s)s, %(v)s, 0, 'S')", {"s": scene, "v": video})
    return db, video, scene


def stock_stage(db, lane, pexels, commons):
    return StockStage(make_deps(db), lane=lane, pexels=pexels, commons=commons)


def hit(source, licence, credit, attribution):
    return StockResult(True, None, ["living room tv"], [], "s3://b/stock.mp4", 1920, 1080, 5.0,
                       StockProvenance(source, "id-1", "https://page/1", "Jane Roe", licence, "https://lic", attribution, credit,
                                       "2026-11-22T10:00:00+00:00", "abc123"))


def test_the_planner_marks_stock_only_when_the_lane_says_so():
    assert planned(0, 0, 4, lane="stock").stock() and not planned(0, 0, 4).stock()


def test_switches_off_mean_no_requests(tv):
    db, video, scene = tv
    add_shot(db, video, 0, STOCK, scene)
    assert stock_stage(db, False, True, True).requests(video) == [], "master switch off"
    assert stock_stage(db, True, False, False).requests(video) == [], "no source enabled"
    req = stock_stage(db, True, True, True).requests(video)
    assert len(req) == 1 and req[0].description == "living room lit television"
    assert req[0].sources == ["pexels", "commons"] and req[0].duration_sec == 5.0 and req[0].shot_number == 1


def test_stock_shots_are_routed_away_from_keyframes_and_ai_clips(tv):
    db, video, scene = tv
    a = add_shot(db, video, 0, STOCK, scene)
    add_shot(db, video, 1, {"motionPrompt": "push"}, scene)
    rows = ShotStage(make_deps(db)).shots(video)
    assert sum(1 for r in rows if stock_shot(r)) == 1
    reqs = stock_stage(db, True, True, False).requests(video)
    assert len(reqs) == 1 and reqs[0].shot_id == str(a)


def test_a_found_clip_is_stored_with_provenance_and_used_by_the_shot(tv):
    db, video, scene = tv
    a = add_shot(db, video, 0, STOCK, scene)
    s = stock_stage(db, True, True, False)
    s.save(video, s.requests(video)[0], hit("pexels", "Pexels License", True, "Video by Jane Roe on Pexels (https://page/1)"))
    row = db.rows("SELECT p.provider, p.source_url, p.source_id, p.author, p.attribution_text, p.credit_required, p.fetched_at, l.name AS licence "
                  "FROM shot sh JOIN asset a ON a.id = sh.footage_asset_id JOIN provenance p ON p.id = a.provenance_id "
                  "JOIN licence l ON l.id = p.licence_id WHERE sh.id = %(s)s", {"s": a})[0]
    assert (row["provider"], row["source_url"], row["author"], row["licence"], row["credit_required"]) == (
        "pexels", "https://page/1", "Jane Roe", "Pexels License", True) and row["fetched_at"] is not None
    assert s.requests(video) == [], "already has its clip"
    assert db.rows("SELECT clip_status FROM shot WHERE id = %(s)s", {"s": a})[0]["clip_status"] == "READY"
    assert scalar(db, "SELECT a.sha256 FROM asset a WHERE a.shot_id = %(s)s", s=a) == "abc123"


def test_no_match_reverts_the_shot_to_the_ai_lane_and_never_fails(tv):
    db, video, scene = tv
    a = add_shot(db, video, 0, STOCK, scene)
    s = stock_stage(db, True, True, False)
    res = s.save(video, s.requests(video)[0], StockResult(False, "no candidates", ["q"], ["pexels: no PEXELS_API_KEY"]))
    assert "AI lane" in res.summary
    prompt = db.rows("SELECT prompt_json::text AS p FROM shot WHERE id = %(s)s", {"s": a})[0]["p"]
    assert '"ai_video"' in prompt and "no candidates" in prompt
    assert not stock_shot({"prompt_json": prompt}), "keyframes will now be made for it"
    assert s.requests(video) == []
    assert "no longer part" in s.save(video, type("R", (), {"shot_id": str(uuid.uuid4()), "shot_number": 1})(), None).summary


def test_an_error_only_keyframe_result_still_parses():
    """The media worker answers {"error", "provider", "shotId", "uri": null} when the pod is lost; that used to crash save_keyframes."""
    from studio.media.types import KeyframeResult
    from studio.media.wire import from_json
    import json
    r = from_json(KeyframeResult, json.loads('{"modalGpuSeconds":0.0,"podGpuSeconds":0.0,"results":[{"error":"RuntimeError: GPU pod lost","provider":"gpu_pod","shotId":"s1","uri":null}]}'))
    assert r.results[0].error and r.results[0].seed is None
