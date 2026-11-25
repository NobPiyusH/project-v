"""POST /api/plans/validate: the dashboard's dry run of a plan file (26 Nov 2026). Scratch Postgres, fakes, nothing spent or stored."""

import json
import uuid
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import create_app  # noqa: E402
from studio.api.video_wiring import build_video_context  # noqa: E402
from studio.config import Settings  # noqa: E402
from studio.tests.dworld import FakeGateway, FakeLlm, make_world  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PLAN = json.loads((ROOT / "docs/shorts/moon-10x-closer-plan.json").read_text(encoding="utf-8"))


@pytest.fixture
def api(scratch_db):
    make_world(scratch_db)
    s = Settings(video_routes=True)
    gw = FakeGateway()
    v = build_video_context(scratch_db, s, llm=FakeLlm(), gateway=gw, media_urls=object(), researcher=object())
    return type("A", (), {"c": TestClient(create_app(db=scratch_db, settings=s, video_ctx=v), raise_server_exceptions=False),
                          "db": scratch_db, "gw": gw, "v": v})()


def _videos(api) -> int:
    return api.db.rows("SELECT count(*) AS n FROM video")[0]["n"]


def test_a_good_plan_gets_a_summary_and_nothing_is_created(api):
    before = _videos(api)
    r = api.c.post("/api/plans/validate", json={"plan": PLAN})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True and j["totalSec"] == 10 and (j["width"], j["height"]) == (720, 1280)
    assert [b["durationSec"] for b in j["beats"]] == [5, 5] and [b["camera"] for b in j["beats"]] == ["slow_push", "slow_push"]
    assert j["cameraPresets"] == ["slow_push"] and j["moonLayer"] is True
    assert [ln["beat"] for ln in j["lines"]] == [1, 2] and j["lines"][0]["startSec"] == 0.3
    paid = {p["id"]: p for p in j["paidStages"]}
    assert {"MUSIC", "NARRATION", "CRITIC", "KEYFRAMES", "DRAFT_GEN"} <= set(paid)
    assert paid["MUSIC"]["leaveOut"] is True and paid["CRITIC"]["leaveOut"] is False
    e = j["estimate"]
    assert e["clips"] == 2 and 0 < e["inrLow"] <= e["inrHigh"] and e["minutes"] >= 1
    assert _videos(api) == before


def test_a_plan_file_name_works_and_a_foreign_one_is_an_error_message_not_a_crash(api):
    assert api.c.post("/api/plans/validate", json={"planFile": "moon-10x-closer-plan.json"}).json()["ok"] is True
    for name in ("../../.env", "nope.json"):
        j = api.c.post("/api/plans/validate", json={"planFile": name}).json()
        assert j["ok"] is False and j["error"]


def test_a_bad_plan_says_what_is_wrong_in_words(api):
    before = _videos(api)
    bad = json.loads(json.dumps(PLAN))
    bad["generation"]["cameraPresets"] = ["slow_push", "fast_push"]
    j = api.c.post("/api/plans/validate", json={"plan": bad}).json()
    assert j["ok"] is False and "not tested" in j["error"]
    j = api.c.post("/api/plans/validate", json={"plan": {"video": {}}}).json()
    assert j == {"ok": False, "error": "plan has no 'scenes'"}
    assert api.c.post("/api/plans/validate", json={"plan": [1]}).json()["ok"] is False
    assert api.c.post("/api/plans/validate", content=b"{nope").status_code == 400
    assert _videos(api) == before


def test_the_paid_stages_have_their_own_route_and_the_java_stage_shape_is_untouched(api):
    paid = api.c.get("/api/stages/paid").json()
    assert paid["MUSIC"] and paid["CRITIC"] and paid["NARRATION"] and "PLAN" not in paid and "CONCEPT" not in paid
    assert all("paid" not in s for s in api.c.get("/api/stages").json())


def test_leaving_music_out_of_the_plan_removes_the_stage(api):
    doc = json.loads(json.dumps(PLAN))
    doc["video"]["music"] = "none"
    vid = api.c.post("/api/videos", json={"plan": doc}).json()["id"]
    out = api.v.planner.plan(uuid.UUID(vid), "x", None)
    assert "MUSIC" not in [s.name for s in out.stages]
