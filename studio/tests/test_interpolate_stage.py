"""The optional INTERPOLATE stage (docs/runbooks/first-live-short.md section 5c): InterpolateStage on a scratch Postgres (request / save / skip rules), and
its interplay with the Moon layer. The RIFE run itself is tested in workers/tests/test_interpolate_activity.py and tools/film4k/tests/test_interpolate.py."""

import json
from pathlib import Path

import pytest

from studio.assets.asset_store import NewAsset
from studio.production.interpolate_stage import KIND, InterpolateStage
from studio.production.moon_stage import KIND as MOON_KIND
from studio.production.moon_stage import MoonLayerStage
from studio.tests.pworld import make_deps, scalar, seed_video

PLAN = json.loads((Path(__file__).resolve().parents[2] / "docs/shorts/moon-10x-closer-plan.json").read_text(encoding="utf-8"))
DONE = {"videoUri": "s3://b/videos/x/master/interpolated.mp4", "durationSec": 10.0, "width": 1080, "height": 1920, "fpsIn": 24.0, "fpsOut": 48.0,
        "framesIn": 240, "framesOut": 480, "model": "rife-v4.6", "tool": "rife-ncnn-vulkan", "seconds": 90.0}


def _master(db, uri="s3://b/master.mp4"):
    video = seed_video(db, status="RUNNING")
    deps = make_deps(db)
    a = deps.assets.create(NewAsset(video, "video", uri, "video/mp4", 1080, 1920, 10.0, "projectv", "m", None, None, None, False))
    db.execute("UPDATE video SET master_asset_id = %(a)s WHERE id = %(v)s", {"a": a, "v": video})
    return video, deps, a


def test_flag_off_or_unknown_value_skips_cleanly(scratch_db):
    video, deps, _ = _master(scratch_db)
    for mode in ("off", "", "OFF", "remote", None):
        assert "skip" in InterpolateStage(deps, mode).request(video)


def test_flag_on_without_a_master_fails_so_the_owner_decides(scratch_db):
    video = seed_video(scratch_db, status="RUNNING")
    with pytest.raises(RuntimeError, match="no master"):
        InterpolateStage(make_deps(scratch_db), "local").request(video)


def test_save_keeps_the_original_with_provenance_and_a_redo_interpolates_the_original_again(scratch_db):
    db = scratch_db
    video, deps, src = _master(db)
    stage = InterpolateStage(deps, "Local")
    req = stage.request(video)
    assert req["sourceAssetId"] == str(src) and req["sourceUri"] == "s3://b/master.mp4" and req["factor"] == 2
    r = stage.save(video, req, DONE)
    master = scalar(db, "SELECT master_asset_id FROM video WHERE id = %(v)s", v=video)
    assert master != src and "24 to 48 fps" in r.summary
    row = db.rows("SELECT clip_kind, derived_from FROM asset WHERE id = %(a)s", {"a": master})[0]
    assert row["clip_kind"] == KIND and row["derived_from"] == src
    assert scalar(db, "SELECT count(*) FROM asset WHERE id = %(a)s", a=src) == 1, "the original master stays"
    prov = db.rows("SELECT p.provider, p.model, p.prompt, p.ai_generated FROM asset a JOIN provenance p ON p.id = a.provenance_id WHERE a.id = %(a)s", {"a": master})[0]
    assert prov["provider"] == "projectv" and prov["ai_generated"] is False and "rife-v4.6" in prov["model"]
    assert json.loads(prov["prompt"]) == {"tool": "rife-ncnn-vulkan", "model": "rife-v4.6", "fpsIn": 24.0, "fpsOut": 48.0, "framesIn": 240, "framesOut": 480}
    again = stage.request(video)
    assert again["sourceAssetId"] == str(src) and again["sourceUri"] == "s3://b/master.mp4", "a retry starts from the original, not the interpolation"


def test_a_moon_redo_after_interpolation_composites_over_the_original_plate(scratch_db):
    db = scratch_db
    video, deps, plate = _master(db)
    db.execute("UPDATE video SET plan_json = plan_json || CAST(%(i)s AS jsonb) WHERE id = %(v)s", {"i": json.dumps({"imported": PLAN}), "v": video})
    moon = MoonLayerStage(deps)
    moon.save(video, moon.request(video), {"videoUri": "s3://b/moon.mp4", "durationSec": 10.0, "width": 1080, "height": 1920, "fps": 24, "imageSha256": "ab"})
    assert scalar(db, "SELECT clip_kind FROM asset WHERE id = (SELECT master_asset_id FROM video WHERE id = %(v)s)", v=video) == MOON_KIND
    inter = InterpolateStage(deps, "local")
    req = inter.request(video)
    assert req["sourceUri"] == "s3://b/moon.mp4", "interpolation runs on the Moon composite"
    inter.save(video, req, DONE)
    again = moon.request(video)
    assert again["plateAssetId"] == str(plate) and again["plateUri"] == "s3://b/master.mp4"
