"""The video pipeline routes (rest of ApiController, ProducerController) on the real app factory with fakes: scratch Postgres, fake LLM, fake Temporal
gateway, no network, nothing spent. Every response is compared, key by key, with the shape the live Java API returned (recorded once as key names and
JSON types only, in fixtures/java_video_shapes.json: no owner data is stored)."""

import json
import uuid
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import create_app  # noqa: E402
from studio.api.video_wiring import build_video_context  # noqa: E402
from studio.config import Settings  # noqa: E402
from studio.tests.dworld import FakeGateway, FakeLlm, asset, make_world  # noqa: E402
from studio.tests.pworld import seed_video  # noqa: E402
from studio.tests.test_dream_api import conforms  # noqa: E402

JAVA = json.loads((Path(__file__).parent / "fixtures" / "java_video_shapes.json").read_text(encoding="utf-8"))


class Urls:
    def presign(self, uri, ttl):
        return f"http://127.0.0.1:8333/signed?u={uri}&ttl={ttl}"


@pytest.fixture
def a(scratch_db):
    db = scratch_db
    make_world(db)
    s = Settings(video_routes=True)
    gw = FakeGateway()
    v = build_video_context(db, s, llm=FakeLlm(), gateway=gw, media_urls=Urls(), researcher=object())
    c = TestClient(create_app(db=db, settings=s, video_ctx=v), raise_server_exceptions=False)
    return type("A", (), {"c": c, "v": v, "db": db, "gw": gw})()


def shape_ok(key, py, optional=()):
    """optional: keys Java only sends sometimes (e.g. budget.modal, once the media worker has reported Modal billing)."""
    java = {k: v for k, v in JAVA[key]["shape"].items() if k not in optional} if isinstance(JAVA[key]["shape"], dict) else JAVA[key]["shape"]
    assert conforms(java, py) == [], key


def with_shots(db, video):
    scene, shot = uuid.uuid4(), uuid.uuid4()
    img, clip = asset(db, "s3://b/k.png"), asset(db, "s3://b/c.mp4")
    db.execute("INSERT INTO scene (id, video_id, ord, title) VALUES (%(s)s, %(v)s, 0, 'S')", {"s": scene, "v": video})
    prompt = {"keyframePrompt": "k", "motionPrompt": "m", "lineIndex": 0, "startSec": 0.0, "sfxQuery": None}
    db.execute("INSERT INTO shot (id, scene_id, ord, duration_target_sec, description, prompt_json, keyframe_asset_id, draft_clip_asset_id) "
               "VALUES (%(i)s, %(s)s, 0, 3, 'd', CAST(%(p)s AS jsonb), %(k)s, %(c)s)", {"i": shot, "s": scene, "k": img, "c": clip, "p": json.dumps(prompt)})
    db.execute("INSERT INTO research (id, video_id, version, facts_json, sources_json, queries, model) VALUES (%(i)s, %(v)s, 1, '[]', '[]', ARRAY['q'], 'm')",
               {"i": uuid.uuid4(), "v": video})
    db.execute("INSERT INTO concept (id, video_id, big_idea, clock_definition, acts_json, version) VALUES (%(i)s, %(v)s, 'b', "
               "CAST('{\"logline\": \"L\", \"label\": \"c\"}' AS jsonb), CAST('[{\"title\": \"A\", \"approxSec\": 30}]' AS jsonb), 1)",
               {"i": uuid.uuid4(), "v": video})
    db.execute("INSERT INTO music_cue (id, video_id, asset_id, bpm) VALUES (%(i)s, %(v)s, %(a)s, 80)", {"i": uuid.uuid4(), "v": video, "a": asset(db, "s3://b/m.wav")})
    db.execute("UPDATE script SET beat_sheet_json = jsonb_set(beat_sheet_json, '{lineStarts}', '{\"0\": 0.0}') WHERE video_id = %(v)s", {"v": video})
    return shot, img, clip


def test_the_video_routes_are_off_by_default_so_java_keeps_serving_them(scratch_db):
    c = TestClient(create_app(db=scratch_db, settings=Settings()), raise_server_exceptions=False)
    assert c.get("/api/videos").status_code == 404 and c.get("/api/stages").status_code == 404 and c.get("/api/budget").status_code == 404
    assert Settings().video_routes is False


def test_the_read_routes_match_the_java_shapes(a):
    db = a.db
    video = seed_video(db)
    shot, img, clip = with_shots(db, video)
    lst = a.c.get("/api/videos").json()
    shape_ok("/api/videos", [r for r in lst if r["id"] == str(video)])
    detail = a.c.get(f"/api/videos/{video}").json()
    detail.pop("budget", None)  # additive: the per-video cap, not in the recorded Java shape
    shape_ok("/api/videos/{id}", detail)
    prod = a.c.get(f"/api/videos/{video}/production").json()
    shape_ok("/api/videos/{id}/production", prod)
    assert prod["shots"][0]["id"] == str(shot)
    shape_ok("/api/videos/{id}/producer", a.c.get(f"/api/videos/{video}/producer").json())
    shape_ok("/api/videos/{id}/clips/progress", a.c.get(f"/api/videos/{video}/clips/progress").json())
    assert a.c.get(f"/api/videos/{video}/clips/progress").json() == {"clips": [], "finished": True}
    shape_ok("/api/shots/{shot}/versions", a.c.get(f"/api/shots/{shot}/versions").json())
    shape_ok("/api/shots/{shot}/clips", a.c.get(f"/api/shots/{shot}/clips").json())
    shape_ok("/api/assets/{id}", a.c.get(f"/api/assets/{img}").json())
    shape_ok("/api/budget", a.c.get("/api/budget").json(), optional=("modal",))
    shape_ok("/api/stages", a.c.get("/api/stages").json())
    shape_ok("/api/system", a.c.get("/api/system").json())
    shape_ok("/api/producer/settings", a.c.get("/api/producer/settings").json())
    shape_ok("/api/producer/rules", a.c.get("/api/producer/rules").json())
    st = a.c.get("/api/stages").json()
    assert st[0]["id"] == "PLAN" and any(s["redoWith"] for s in st), "REDO_WITH dependencies are sent"
    assert a.c.get("/api/system").json()["planner"] == "rules (no GEMINI_API_KEY)"
    r = a.c.get(f"/api/assets/{clip}/content", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("http://127.0.0.1:8333/signed?u=s3://b/c.mp4&ttl=1800")


def test_create_starts_the_python_workflow_and_stage_decisions_signal_it(a):
    r = a.c.post("/api/videos", json={"prompt": "a calm sea", "defaultMode": "REVIEW", "modes": {"SCRIPT": "MANUAL"}, "reviewMinutes": 15})
    assert r.status_code == 201
    vid = r.json()["id"]
    wf, wid, _req = a.gw.started[0]
    assert wf == "VideoProductionWorkflowPy" and vid in wid
    assert a.db.rows("SELECT review_minutes FROM video WHERE id = %(i)s", {"i": uuid.UUID(vid)})[0]["review_minutes"] == 15
    assert a.c.post("/api/videos", json={"prompt": "  "}).status_code == 400
    n = len(a.gw.signals)
    assert a.c.post(f"/api/videos/{vid}/stages/PLAN/approve", json={"notes": "ok"}).status_code == 200
    assert a.c.post(f"/api/videos/{vid}/stages/PLAN/reject").status_code == 200, "the body is optional"
    assert a.c.put(f"/api/videos/{vid}/modes/SCRIPT", json={"mode": "AUTO"}).status_code == 200
    assert a.c.put(f"/api/videos/{vid}/modes", json={"mode": "AUTO"}).status_code == 200
    assert a.c.put(f"/api/videos/{vid}/review-minutes", json={"minutes": 5}).status_code == 200
    assert a.c.post(f"/api/videos/{vid}/pause").status_code == 200 and a.c.post(f"/api/videos/{vid}/resume").status_code == 200
    assert [s[1] for s in a.gw.signals[n:n + 2]] == ["approve", "reject"] and len(a.gw.signals) > n + 5


def test_refusals_are_a_409_and_missing_rows_a_404_with_an_error_body_like_java(a):
    video = seed_video(a.db, status="RUNNING")
    r = a.c.post(f"/api/videos/{video}/stages/SCRIPT/rerun")
    assert r.status_code == 409 and "still in production" in r.json()["error"]
    missing = a.c.get(f"/api/videos/{uuid.uuid4()}")
    assert missing.status_code == 404 and "error" in missing.json()
    assert a.c.post(f"/api/videos/{video}/shots/redo", json={"shotIds": []}).status_code == 409
    assert a.c.post(f"/api/videos/{video}/stages/NOPE/approve").status_code == 500


def test_shots_clips_footage_and_script_lines(a):
    db = a.db
    video = seed_video(db)
    shot, img, _clip = with_shots(db, video)
    assert a.c.put(f"/api/shots/{shot}/prompt", json={"keyframePrompt": "a pink pier"}).status_code == 200
    assert a.c.put(f"/api/shots/{shot}/prompt", json={"keyframePrompt": " "}).status_code == 400
    assert a.c.put(f"/api/shots/{shot}/final-wanted?wanted=true").status_code == 200
    assert a.c.put(f"/api/shots/{shot}/keyframe", json={"assetId": str(img)}).status_code in (200, 409)
    r = a.c.put(f"/api/videos/{video}/script/lines/0", json={"text": "A star is reborn.", "spoken": True})
    assert r.status_code == 200 and r.json()["text"] == "A star is reborn."
    assert a.c.post(f"/api/videos/{video}/shots/{shot}/footage", json={}).status_code == 409, "give search words or pick a clip"
    assert a.c.delete(f"/api/shots/{shot}/footage").status_code == 200
    assert a.c.post(f"/api/videos/{video}/clips/stop").status_code == 200 and a.v.clip_progress.stop_requested(video)
    assert a.c.get(f"/api/internal/clips/{video}/stop").json() == {"stop": True}
    assert a.c.post("/api/internal/clips/progress", json={"videoId": str(video), "clips": [{"shotId": "s"}], "finished": True}).status_code == 200
    assert a.c.get(f"/api/videos/{video}/clips/progress").json()["finished"] is True
    assert a.c.get(f"/api/internal/clips/{video}/stop").json() == {"stop": False}, "a finished batch clears the stop"


def test_budget_shows_modal_billing_once_the_worker_reports_it(a):
    assert "modal" not in a.c.get("/api/budget").json()
    assert a.c.post("/api/internal/modal-billing", json={"creditsUsedUsd": 12.5, "meteredUsd": 3}).status_code == 200
    m = a.c.get("/api/budget").json()["modal"]
    assert m["creditsUsedUsd"] == 12.5 and m["creditLeftUsd"] == m["freeCreditUsd"] - 12.5 and m["fxInrPerUsd"] > 0


def test_the_ai_producer_settings_and_rules_routes(a):
    c = a.c
    assert c.put("/api/producer/settings", json={"trust": "advise"}).status_code == 200
    s = c.get("/api/producer/settings").json()
    assert s["trust"] == "advise" and s["deciding"] is False
    c.put("/api/producer/settings", json={"trust": "auto"})
    assert c.post("/api/producer/rules", json={"stage": "SCRIPT", "text": "never open with a question"}).status_code == 200
    rules = c.get("/api/producer/rules").json()
    assert any("never open with a question" in json.dumps(r) for r in rules)
    rid = rules[0]["id"]
    assert c.put(f"/api/producer/rules/{rid}", json={"enabled": False}).status_code == 200
    assert c.delete(f"/api/producer/rules/{rid}").status_code == 200
    assert c.put("/api/producer/settings", json={"trust": "sometimes"}).status_code == 500


def test_resume_and_the_paid_redo_switch_routes(a):
    video = seed_video(a.db, status="RUNNING")
    assert a.c.post(f"/api/videos/{video}/stages/DRAFT_GEN/resume").status_code == 409, "nothing stored: Resume is refused like any other refusal"
    a.db.execute("INSERT INTO stage_run (id, video_id, stage, attempt, mode, status, held_reason, can_resume, has_stored_result) "
                 "VALUES (%(i)s, %(v)s, 'DRAFT_GEN', 1, 'AUTO', 'FAILED', 'save crashed', true, true)", {"i": uuid.uuid4(), "v": video})
    assert a.c.post(f"/api/videos/{video}/stages/DRAFT_GEN/resume").status_code == 200
    assert a.gw.signals[-1][1] == "resume_stage"
    run = a.c.get(f"/api/videos/{video}").json()["stageRuns"][0]
    assert run["canResume"] is True and run["heldReason"] == "save crashed"
    assert a.c.put(f"/api/videos/{video}/auto-redo-paid", json={"enabled": True}).status_code == 200
    assert a.c.get(f"/api/videos/{video}").json()["auto_redo_paid"] is True
    created = a.c.post("/api/videos", json={"prompt": "x", "autoRedoPaid": True}).json()["id"]
    assert a.c.get(f"/api/videos/{created}").json()["auto_redo_paid"] is True
