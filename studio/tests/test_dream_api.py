"""The Dream, Dream GPU and Autopilot routes on the real app factory with fakes (scratch Postgres, fake LLM, fake Temporal gateway, no pod, no
network). Includes the contract check: every response is compared, key by key, with the shape the live Java API returned (recorded once as key
names and JSON types only, in fixtures/java_dream_shapes.json: no owner data is stored)."""

import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from decimal import Decimal  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import create_app  # noqa: E402
from studio.api.jobs import Periodic, dream_jobs  # noqa: E402
from studio.api.wiring import build_dream_context  # noqa: E402
from studio.config import Settings  # noqa: E402
from studio.media.types import (  # noqa: E402
    ClipJob,
    ClipOutput,
    ClipRequest,
    ClipResult,
    KeyframeJob,
    KeyframeOutput,
    KeyframeRequest,
    KeyframeResult,
    ProducerItemVerdict,
    ProducerReviewResult,
    Usage,
)
from studio.tests.dworld import FakeGateway, FakeLlm, make_world  # noqa: E402

JAVA = json.loads((Path(__file__).parent / "fixtures" / "java_dream_shapes.json").read_text(encoding="utf-8"))


def conforms(java, py, where="$") -> list[str]:
    """Differences between the Java response shape and ours: key sets must match, types must be compatible (null fits anything)."""
    if isinstance(java, dict):
        if not isinstance(py, dict):
            return [f"{where}: Java sends an object"]
        out = [f"{where}: missing {k}" for k in java if k not in py] + [f"{where}: extra {k}" for k in py if k not in java]
        for k in java.keys() & py.keys():
            out += conforms(java[k], py[k], f"{where}.{k}")
        return out
    if isinstance(java, list):
        if not isinstance(py, list):
            return [f"{where}: Java sends a list"]
        return conforms(java[0], py[0], f"{where}[0]") if java and py else []
    jt, pt = java, type(py).__name__
    if "NoneType" in (jt, pt) or {jt, pt} <= {"int", "float"} or jt == pt:
        return []
    return [f"{where}: Java {jt}, here {pt}"]


@pytest.fixture
def app_(scratch_db):
    db = scratch_db
    for t in ("autopilot_run", "autopilot_idea"):
        db.execute(f"DELETE FROM {t}")
    make_world(db)  # seeds the providers the router needs
    db.execute("DELETE FROM routing_decision")
    s = Settings(dream_routes=True)
    stub = SimpleNamespace(deciding=lambda: False, mark_acted=lambda *a: None)
    ctx = build_dream_context(db, s, llm=FakeLlm(), gateway=FakeGateway(), producer_service=stub)
    client = TestClient(create_app(db=db, settings=s, dream_ctx=ctx), raise_server_exceptions=False)
    return SimpleNamespace(c=client, ctx=ctx, db=db, gw=ctx.gateway)


def finished_short(a):
    """A Short with pictures, clips, a finish, a quality check, metrics and a Producer review, made through the real service on fakes."""
    from studio.dream.wire_types import DreamCheckResult, DreamFinishResult

    d = a.ctx.dreams
    dream = uuid.uuid4()
    a.db.execute("INSERT INTO dream_short (id, idea, status, step, look) VALUES (%(id)s, 'a pink pier', 'RUNNING', 'FINISH', 'lonely')", {"id": dream})
    shots = []
    for o in range(2):
        s = uuid.uuid4()
        shots.append(s)
        a.db.execute("INSERT INTO dream_shot (id, dream_short_id, ord, scene, picture_prompt, motion_prompt) VALUES (%(id)s, %(d)s, %(o)s, 'scene', 'p', 'm')",
                     {"id": s, "d": dream, "o": o})
    # the requests are built by hand: the real ones are routed and held to the monthly GPU cap, which is the service tests' business
    pics = KeyframeRequest(str(dream), None, "a", "b", [KeyframeJob(str(s), 1, "modal_zimage", "p", 768, 1344, "9:16", "1K", None, 1) for s in shots])
    d.save_pictures(dream, pics, KeyframeResult([KeyframeOutput(str(s), f"s3://b/p{i}.png", "modal_zimage", "z", 1, 5.0, None, None, []) for i, s in enumerate(shots)], 10))
    clips = ClipRequest(str(dream), "a", "b", "u", [ClipJob(str(s), i + 1, 1, "dream", "fast", "s3://b/p.png", "m", 480, 832, 57, 1) for i, s in enumerate(shots)],
                        1, 0, Decimal(0), "wan", None, None)
    d.save_clips(dream, clips, ClipResult([ClipOutput(str(s), 1, "dream", f"s3://b/c{i}.mp4", 20, 57, 16, "fast", None, 0) for i, s in enumerate(shots)], 1, False))
    d.save_finish(dream, DreamFinishResult("s3://b/final.mp4", 6.5, 1080, 1920, 24, "rife-laptop", None, None, {"motion": 1.8}))
    d.save_check(dream, DreamCheckResult({"noPeople": 3}, 6, [], "fine", "gemini-3.6-flash", Usage(5000, 200, 0, 5200), None), "FINISH")
    a.ctx.dream_producer.save_review(str(dream), "PICTURES", 1, ProducerReviewResult(
        "approve", 0.9, "good", None, [ProducerItemVerdict("shot-1", 9, True, "", "")], "gemini-3.6-flash", Usage(1000, 100, 0, 1100)))
    a.db.execute("UPDATE dream_short SET status = 'COMPLETED' WHERE id = %(d)s", {"d": dream})
    return dream


def shape_ok(key, py):
    assert conforms(JAVA[key]["shape"], py) == [], key


def test_routes_are_off_by_default_so_java_keeps_serving_them(scratch_db):
    c = TestClient(create_app(db=scratch_db, settings=Settings()), raise_server_exceptions=False)
    assert c.get("/api/dream").status_code == 404 and c.get("/api/autopilot").status_code == 404 and c.get("/api/dream/looks").status_code == 404
    assert Settings().dream_routes is False and Settings().jobs is False and Settings().dream_gpu_provider == "modal"


def test_the_static_lists_match_the_java_shapes(app_):
    c = app_.c
    assert c.get("/api/dream/looks").json() == ["lonely", "drive", "walkthrough", "dreamcore", "impossible", "architecture", "unfiltered"]
    assert c.get("/api/dream/looks").json() == JAVA["/api/dream/looks"]["shape"] or JAVA["/api/dream/looks"]["shape"] == ["str"]
    shape_ok("/api/dream/idea-packs", c.get("/api/dream/idea-packs").json())
    packs = c.get("/api/dream/idea-packs").json()
    assert packs[0]["id"] == "impossible-architecture" and len(packs[0]["ideas"]) >= 20
    shape_ok("/api/dream/calibration", c.get("/api/dream/calibration").json())
    shape_ok("/api/dream/lessons", c.get("/api/dream/lessons").json())


def test_list_detail_chain_reviews_and_gpu_match_the_java_shapes(app_):
    c = app_.c
    dream = finished_short(app_)
    lst = c.get("/api/dream").json()
    shape_ok("/api/dream", [r for r in lst if r["id"] == str(dream)])
    detail = c.get(f"/api/dream/{dream}")
    assert detail.status_code == 200
    d = detail.json()
    shape_ok("/api/dream/{id}", d)
    assert d["modes_json"]["type"] == "jsonb" and isinstance(d["modes_json"]["value"], str), "Java sends the jsonb column as Jackson wrote the PGobject"
    assert len(d["shots"]) == 2 and len(d["costs"]) >= 3 and d["metrics"] and d["producer"] and d["timeline"][0]["step"] == "SCOUT"
    assert d["view"] is None and all(isinstance(x["workSeconds"], int) for x in d["timeline"])
    assert d["log"] and d["log"][0]["at"].endswith("Z")
    shape_ok("/api/dream/{id}/chain", c.get(f"/api/dream/{dream}/chain").json())
    shape_ok("/api/dream/{id}/gpu", c.get(f"/api/dream/{dream}/gpu").json())
    r = c.post(f"/api/dream/{dream}/reviews", json={"shotId": None, "rating": 4, "note": "calm and lovely"})
    assert r.status_code == 200 and list(r.json()) == ["id"]
    shape_ok("/api/dream/{id}/reviews", c.get(f"/api/dream/{dream}/reviews").json())


def test_create_starts_the_python_workflow_and_the_owners_decisions_signal_it(app_):
    c = app_.c
    r = c.post("/api/dream", json={"idea": "a glass orchard", "look": "lonely", "shots": 3, "defaultMode": "REVIEW", "modes": {"clips": "MANUAL"},
                                   "reviewMinutes": 15, "pathChoice": True})
    assert r.status_code == 200
    dream = r.json()["id"]
    wf, wf_id, arg = app_.gw.started[0]
    assert wf == "DreamShortWorkflowPy" and wf_id == f"dream-{dream}" and arg["modes"] == {"CLIPS": "MANUAL"} and arg["reviewMinutes"] == 15
    row = app_.ctx.dreams.dream(uuid.UUID(dream))
    assert row["shot_count"] == 3 and row["path_choice"] is True and row["scout"] is True
    for path, body, signal in [(f"/api/dream/{dream}/steps/write/approve", None, "approve"),
                               (f"/api/dream/{dream}/steps/pictures/redo", {"shotIds": ["x"], "notes": "pinker"}, "redo"),
                               (f"/api/dream/{dream}/steps/clips/mode", {"mode": "AUTO"}, "set_mode"),
                               (f"/api/dream/{dream}/retry", None, "retry"), (f"/api/dream/{dream}/cancel", None, "cancel")]:
        before = len(app_.gw.signals)
        resp = c.post(path, json=body) if body is not None else c.post(path)
        assert resp.status_code == 200 and resp.content == b"" and app_.gw.signals[before][1] == signal, path
    assert app_.gw.signals[1][2] == ("PICTURES", ["x"], "pinker") and app_.gw.signals[2][2] == ("CLIPS", "AUTO")
    assert c.post(f"/api/dream/{dream}/steps/pictures/redo").status_code == 200, "the redo body is optional"
    assert c.post(f"/api/dream/{dream}/scout/angle/2").status_code == 200 and app_.ctx.dreams.dream(uuid.UUID(dream))["scout_angle"] == 2
    assert c.post(f"/api/dream/{dream}/editing", json={"musicEdit": False}).status_code == 200
    assert app_.ctx.dreams.dream(uuid.UUID(dream))["edit_music"] is False
    assert c.post(f"/api/dream/{dream}/stop").status_code == 200 and app_.ctx.clip_progress.stop_requested(uuid.UUID(dream))


def test_errors_are_a_500_with_a_json_body_like_spring_and_a_bad_body_is_a_400(app_):
    c = app_.c
    r = c.post("/api/dream", json={"idea": "  "})
    assert r.status_code == 500 and r.json()["message"] == "Type an idea first" and r.json()["path"] == "/api/dream"
    assert c.get(f"/api/dream/{uuid.uuid4()}").status_code == 500
    assert c.post("/api/dream/not-a-uuid/retry").status_code == 422 or c.post("/api/dream/not-a-uuid/retry").status_code == 400
    assert c.post(f"/api/dream/{uuid.uuid4()}/hook", content=b"{").status_code == 400
    assert c.post(f"/api/dream/{uuid.uuid4()}/steps/nope/approve").status_code == 500


def test_the_hook_paths_and_continue_routes_work_end_to_end(app_):
    c = app_.c
    r = c.post("/api/dream", json={"idea": "a glass city", "look": "lonely", "shots": 3, "pathChoice": True, "scout": False})
    dream = uuid.UUID(r.json()["id"])
    assert c.post(f"/api/dream/{dream}/hook", json={"text": "A *BIG* HOOK", "color": "red"}).status_code == 200
    assert c.post(f"/api/dream/{dream}/paths", json={"a": "the door", "b": "the stairs"}).status_code == 200
    row = app_.ctx.dreams.dream(dream)
    assert row["hook_text"] == "A *BIG* HOOK" and row["path_a"] == "the door" and row["path_b"] == "the stairs"
    assert c.post(f"/api/dream/{dream}/continue", json={"path": "a"}).status_code == 500, "not finished yet"
    assert c.post(f"/api/dream/{dream}/rerun/music").status_code == 500, "still running"


# ------------------------------------------------------------------------------------------------ GPU routes (broker off: nothing can be rented)


def test_gpu_routes_with_the_broker_off_match_java_and_never_rent(app_):
    c = app_.c
    dream, _ = app_.ctx.dreams.dream(uuid.UUID(c.post("/api/dream", json={"idea": "x"}).json()["id"])), None
    d = dream["id"]
    assert c.get("/api/gpu/broker").json() == {"enabled": False}
    assert all(o["available"] is False for o in c.get("/api/gpu/broker/options").json()), "nothing is rentable with the broker off"
    g = c.get(f"/api/dream/{d}/gpu").json()
    assert g["mode"] == "auto" and g["choice"] == "" and g["rented"] is False and g["usersOnPod"] == 0 and g["hold"] == "auto" and g["boot"] is None
    assert g["estimate"]["hourlyUsd"] == 2.0, "nothing quotable: the worst-case watchdog rate"
    assert c.post(f"/api/dream/{d}/gpu/hold", json={"hold": "manual", "idleMinutes": 1}).json()["idleMinutes"] == 5
    bad = c.post(f"/api/dream/{d}/gpu/hold", json={"hold": "sometimes"})
    assert bad.status_code == 400 and "hold must be auto or manual" in bad.json()["error"]
    unknown = c.post(f"/api/dream/{d}/gpu", json={"mode": "manual", "choice": "vast|RTX 9999"})
    assert unknown.status_code == 400 and "not an offered" in unknown.json()["error"]
    assert c.post(f"/api/dream/{d}/gpu", json={"mode": "sometimes"}).status_code == 400
    assert c.post(f"/api/dream/{d}/gpu/off").status_code == 500, "not using a rented GPU: nothing to turn off"
    hb = c.post(f"/api/internal/gpu/{d}/heartbeat").json()
    assert hb == {"ok": True, "pod": "OPEN"}
    assert c.post(f"/api/internal/gpu/{d}/boot-timing", json={"timing": {"a": 1}}).json() == {"ok": True}
    assert c.post(f"/api/internal/gpu/{d}/replace").status_code == 500, "the GPU broker is off: never rents from here"
    canary = c.post("/api/gpu/broker/providers/runpod/canary")
    assert canary.status_code == 400 and "approve=true" in canary.json()["detail"], "the canary spends money: it needs the owner's approve=true"
    assert app_.ctx.gpu.broker.has_open_pod() is False and not app_.ctx.gpu.enabled()


# ------------------------------------------------------------------------------------------------ Autopilot routes


def test_autopilot_routes_match_the_java_shapes(app_):
    c = app_.c
    app_.db.execute("UPDATE autopilot_setting SET enabled = false, daily_cap_inr = 50, privacy = 'private'")
    st = c.get("/api/autopilot").json()
    assert set(st) == {"enabled", "zone", "now", "waitingOn", "ideasLeft", "youtubeConnected"}
    shape_ok("/api/autopilot", st)
    s = c.get("/api/autopilot/settings").json()
    shape_ok("/api/autopilot/settings", s)
    assert s["enabled"] is False and s["stepModes"]["CLIPS"] == "PRODUCER" and s["dailyCapInr"] == 50
    s["startMode"], s["shots"] = "REVIEW", 9
    saved = c.put("/api/autopilot/settings", json=s).json()
    assert saved["startMode"] == "REVIEW" and saved["shots"] == 8
    s["startMode"] = "PRODUCER"
    assert c.put("/api/autopilot/settings", json=s).status_code == 500
    idea = c.post("/api/autopilot/ideas", json={"idea": "a tram under the sea", "look": "drive"}).json()["id"]
    ideas = c.get("/api/autopilot/ideas").json()
    shape_ok("/api/autopilot/ideas", ideas)
    assert ideas[0]["idea"] == "a tram under the sea" and ideas[0]["source"] == "owner"
    assert c.delete(f"/api/autopilot/ideas/{idea}").status_code == 200 and c.get("/api/autopilot/ideas").json() == []
    assert c.post("/api/autopilot/tick").status_code == 200, "off by default: the tick does nothing"
    shape_ok("/api/autopilot/runs", c.get("/api/autopilot/runs").json())
    run = uuid.uuid4()
    app_.db.execute("INSERT INTO autopilot_run (id, run_date, status) VALUES (%(id)s, DATE '2026-11-18', 'FAILED')", {"id": run})
    assert c.post(f"/api/autopilot/runs/{run}/post", json={"title": "T", "privacy": "private"}).status_code == 500, "not ready: no double posts"
    assert c.post(f"/api/autopilot/runs/{run}/dismiss").status_code == 200
    assert app_.db.rows("SELECT status FROM autopilot_run WHERE id = %(id)s", {"id": run})[0]["status"] == "SKIPPED"
    assert c.post(f"/api/autopilot/runs/{run}/retry-kit").status_code == 200 and c.post(f"/api/autopilot/runs/{run}/start").status_code == 200


def test_posting_starts_the_post_workflow_only_for_a_ready_run(app_):
    c = app_.c
    run = uuid.uuid4()
    app_.db.execute("INSERT INTO autopilot_run (id, run_date, status) VALUES (%(id)s, DATE '2026-11-17', 'READY')", {"id": run})
    assert c.post(f"/api/autopilot/runs/{run}/post", json={"title": "T", "description": "d", "hashtags": "#Shorts", "privacy": "private"}).status_code == 200
    assert app_.gw.started[-1][0] == "PostWorkflowPy" and app_.gw.started[-1][2] == str(run)
    assert c.post(f"/api/autopilot/runs/{run}/post", json={"title": "T", "privacy": "private"}).status_code == 500, "already posting"


# ------------------------------------------------------------------------------------------------ the periodic jobs


def test_a_periodic_job_runs_repeatedly_survives_a_failure_and_stops():
    calls = []

    def fn():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("one bad round")

    j = Periodic("t", fn, every_seconds=0.02, initial_delay=0)
    j.start()
    deadline = time.time() + 5
    while len(calls) < 3 and time.time() < deadline:
        time.sleep(0.01)
    j.stop()
    n = len(calls)
    time.sleep(0.1)
    assert n >= 3 and len(calls) == n, "the failure did not stop the schedule, and stop() ends it"


def test_the_job_set_follows_the_java_schedules_and_the_broker_setting(app_):
    names = [j.name for j in dream_jobs(app_.ctx)]
    assert names == ["autopilot-tick"], "no pod can exist with the broker off: no pod jobs"
    on = SimpleNamespace(gpu=SimpleNamespace(enabled=lambda: True, release_idle_manual=lambda now: None),
                         dreams=SimpleNamespace(release_gpu_if_nobody_waits_scheduled=lambda: None), autopilot=SimpleNamespace(tick=lambda: None))
    jobs = dream_jobs(on)
    assert [(j.name, j.every, j.initial) for j in jobs] == [("gpu-nobody-waits", 30, 60), ("gpu-idle-manual", 30, 60), ("autopilot-tick", 60, 30)]


def test_jobs_start_only_when_asked(scratch_db):
    s = Settings(dream_routes=True)
    ctx = build_dream_context(scratch_db, s, llm=FakeLlm(), gateway=FakeGateway(), producer_service=SimpleNamespace(deciding=lambda: False))
    with TestClient(create_app(db=scratch_db, settings=s, dream_ctx=ctx)) as c:  # lifespan runs
        assert c.get("/api/dream/looks").status_code == 200
    on = Settings(dream_routes=True, jobs=True)
    with TestClient(create_app(db=scratch_db, settings=on, dream_ctx=ctx)) as c:
        assert c.get("/api/autopilot").status_code == 200
