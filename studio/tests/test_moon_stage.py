"""The optional MOON_LAYER stage (docs/runbooks/first-live-short.md section 5b): plan parsing and stage planning (pure), and MoonLayerStage on a scratch
Postgres (request / save / skip rules). The composite itself is tested in workers/tests/test_moon_layer_activity.py."""

import json
from pathlib import Path

import pytest

from studio.planner import imported
from studio.planner.plan import VideoPlan
from studio.planner.planner import stages_for
from studio.production.moon_stage import KIND, MoonLayerStage
from studio.tests.pworld import make_deps, scalar, seed_video
from studio.workflow.stage import Stage

PLAN = json.loads((Path(__file__).resolve().parents[2] / "docs/shorts/moon-10x-closer-plan.json").read_text(encoding="utf-8"))
NO_MOON = {k: v for k, v in PLAN.items() if k != "moon"}


def test_the_plan_loads_its_moon_block_and_caption_and_the_stage_is_planned_only_with_it():
    imp = imported.parse(PLAN)
    assert imp.moon["mode"] == "layer" and imp.moon["layer"]["imagePath"].endswith("moon.png")
    assert imp.moonCaption["text"] == PLAN["overlay"]["caption"]["text"]
    assert imported.parse(NO_MOON).moon is None
    vp = VideoPlan(**PLAN["video"])
    with_moon = stages_for(vp, moon=True)
    assert Stage.MOON_LAYER not in stages_for(vp)
    assert with_moon.index(Stage.MOON_LAYER) == with_moon.index(Stage.ASSEMBLY) + 1


def test_a_malformed_moon_block_is_rejected_when_the_plan_is_loaded():
    for bad in ({"mode": "in_plate"}, {"mode": "layer"}, {"mode": "layer", "layer": {"imagePath": 5}}):
        with pytest.raises(ValueError, match="moon"):
            imported.parse({**PLAN, "moon": bad})


class _Inner:
    def normalise(self, plan, prompt):
        return plan


def test_the_imported_planner_adds_the_stage_only_for_a_moon_plan_and_only_with_the_flag(scratch_db):
    for doc, flag, want in ((PLAN, True, True), (PLAN, False, False), (NO_MOON, True, False)):
        video = _video(scratch_db, doc)
        out = imported.ImportedPlanner(_Inner(), scratch_db, flag).plan(video, "x", None)
        assert (Stage.MOON_LAYER in out.stages) is want


def _video(db, doc=None):
    video = seed_video(db, status="RUNNING")
    if doc is not False:
        db.execute("UPDATE video SET plan_json = plan_json || CAST(%(i)s AS jsonb) WHERE id = %(v)s", {"i": json.dumps({"imported": doc or PLAN}), "v": video})
    return video


def _master(db, video):
    from studio.assets.asset_store import NewAsset

    deps = make_deps(db)
    a = deps.assets.create(NewAsset(video, "video", "s3://b/master.mp4", "video/mp4", 1080, 1920, 10.0, "projectv", "m", None, None, None, False))
    db.execute("UPDATE video SET master_asset_id = %(a)s WHERE id = %(v)s", {"a": a, "v": video})
    return deps, a


def test_no_moon_block_or_flag_off_skips_cleanly(scratch_db):
    assert "skip" in MoonLayerStage(make_deps(scratch_db)).request(_video(scratch_db, NO_MOON))
    assert "skip" in MoonLayerStage(make_deps(scratch_db)).request(_video(scratch_db, False))
    assert "skip" in MoonLayerStage(make_deps(scratch_db), enabled=False).request(_video(scratch_db))


def test_a_moon_plan_without_a_master_fails_so_the_owner_decides(scratch_db):
    with pytest.raises(RuntimeError, match="no master"):
        MoonLayerStage(make_deps(scratch_db)).request(_video(scratch_db))


def test_save_keeps_the_plate_with_provenance_and_a_redo_composites_over_the_plate_again(scratch_db):
    db = scratch_db
    video = _video(db)
    deps, plate = _master(db, video)
    stage = MoonLayerStage(deps)
    req = stage.request(video)
    assert req["plateAssetId"] == str(plate) and req["plateUri"] == "s3://b/master.mp4" and req["imagePath"] == PLAN["moon"]["layer"]["imagePath"]
    assert req["layer"]["scaleTo"] == 2.2 and req["caption"]["text"] == PLAN["overlay"]["caption"]["text"]
    done = {"videoUri": "s3://b/videos/x/master/moon-layer.mp4", "durationSec": 10.0, "width": 1080, "height": 1920, "fps": 24, "imageSha256": "ab"}
    r = stage.save(video, req, done)
    master = scalar(db, "SELECT master_asset_id FROM video WHERE id = %(v)s", v=video)
    assert master != plate and "original plate is kept" in r.summary
    row = db.rows("SELECT clip_kind, derived_from FROM asset WHERE id = %(a)s", {"a": master})[0]
    assert row["clip_kind"] == KIND and row["derived_from"] == plate
    assert scalar(db, "SELECT count(*) FROM asset WHERE id = %(a)s", a=plate) == 1, "the plate stays"
    prov = db.rows("SELECT p.provider, p.ai_generated FROM asset a JOIN provenance p ON p.id = a.provenance_id WHERE a.id = %(a)s", {"a": master})[0]
    assert prov == {"provider": "projectv", "ai_generated": False}
    again = stage.request(video)
    assert again["plateAssetId"] == str(plate) and again["plateUri"] == "s3://b/master.mp4", "a retry starts from the plate, not the composite"
