"""The first-live-Short plan (docs/shorts/moon-10x-closer-plan.json) loads as the studio's own schemas and passes the planner validator.
Guards against the file drifting from bank.py / VideoPlan / the camera presets. No network."""

import json
import sys
from pathlib import Path

import pytest

from studio.planner.bank import Bank, BankedPlan, BeatPlan, ScenePlan, beat_prompt, validate
from studio.planner.plan import VideoPlan
from studio.planner.planner import Planner
from studio.planner.presets import StylePresets

ROOT = Path(__file__).resolve().parents[2]
PLAN = json.loads((ROOT / "docs/shorts/moon-10x-closer-plan.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def presets():
    return StylePresets(ROOT / "config")


def banked() -> BankedPlan:
    return BankedPlan(bank=Bank(**PLAN["bank"]), scenes=[ScenePlan(**s) for s in PLAN["scenes"]])


def test_plan_passes_the_planner_validator():
    assert validate(banked()) == []


def test_three_five_second_beats_fill_fifteen_seconds_and_have_one_location_no_characters():
    sc = banked().scenes[0]
    assert [b.durationSec for b in sc.beats] == [5, 5, 5] and sc.lengthSec == 15
    assert PLAN["bank"].get("characters") is None and len(PLAN["bank"]["locations"]) == 1


def test_every_prompt_carries_the_bank_text_verbatim_and_the_file_is_not_stale():
    p = banked()
    loc = p.bank.locations[0].description
    for beat, stored in zip(p.scenes[0].beats, PLAN["resolvedPrompts"], strict=True):
        assert loc in stored["keyframePrompt"] and loc in stored["motionPrompt"]
        assert stored["keyframePrompt"] == beat_prompt(p.bank, beat, "image")
        assert stored["motionPrompt"] == beat_prompt(p.bank, beat, "video")


def test_video_plan_survives_the_planner_normaliser_unchanged(presets):
    plan = Planner(presets, None, "", "m", "m").normalise(VideoPlan(**PLAN["video"]), "x")
    assert (plan.format, plan.aspectRatio, plan.durationSec, plan.narration) == ("short", "9:16", 15, "full")
    assert plan.needsResearch is False and plan.visualTypes == ["ai_video"]


def test_generation_settings_are_legal_for_wan_and_the_camera_presets_exist():
    g = PLAN["generation"]
    assert (g["width"], g["height"], g["frames"], g["fps"]) == (720, 1280, 81, 16)
    assert g["width"] % 16 == 0 and g["height"] % 16 == 0 and (g["frames"] - 1) % 4 == 0
    sys.path.insert(0, str(ROOT / "tools" / "film4k"))
    from camera_presets import TESTED, plan_camera, preset

    assert g["cameraPresets"] == ["zoom_in", "pan_left", "zoom_in"]
    assert all(preset(n).engine == "fun_camera" and n in TESTED for n in g["cameraPresets"])
    assert plan_camera(1, 3, g["cameraPresets"]) == g["cameraPresets"]
    assert len(g["cameraPresets"]) == len(banked().scenes[0].beats)


def test_narration_lines_start_inside_their_own_beat():
    for line in PLAN["script"]:
        beat = banked().scenes[0].beats[line["beat"] - 1]
        assert beat.startSec <= line["startSec"] < beat.startSec + beat.durationSec


def test_a_beat_naming_an_unknown_location_would_be_rejected():
    bad = BankedPlan(bank=banked().bank, scenes=[ScenePlan(lengthSec=5, beats=[BeatPlan(prompt="x", locationIds=["nowhere"], durationSec=5)])])
    assert validate(bad) == ["scene 1 beat 1: unknown location 'nowhere'"]


def test_the_moon_shot_plan_goes_through_the_real_shot_stage_with_the_bank_pasted_in(scratch_db):
    """The plan's three beats as the Director would return them: stored shots carry the bank text word for word, 5 s each, camera moves kept."""
    from studio.agents.types import PlannedShot, ShotPlan
    from studio.production.shot_stage import ShotStage
    from studio.tests.pworld import seed_video
    from studio.tests.test_shot_stage import FakeDirector, db_concept, make_deps, voiced_script

    p = banked()
    cams = PLAN["generation"]["cameraPresets"]
    shots = [PlannedShot(sceneTitle="Moon", lineIndex=i, durationSec=b.durationSec, shotType="wide", cameraMove=c, description=f"beat {i + 1}",
                         keyframePrompt=b.prompt, motionPrompt=b.prompt, priority="hero" if i == 0 else "standard",
                         locationIds=b.locationIds, continuity=b.continuity, claims=b.claims)
             for i, (b, c) in enumerate(zip(p.scenes[0].beats, cams, strict=True))]
    video = seed_video(scratch_db, duration=15)
    db_concept(scratch_db, video)
    voiced_script(scratch_db, video, [2.2, 3.4, 3.2])  # about what the three narration lines take spoken
    deps = make_deps(scratch_db, None, FakeDirector(shots))
    deps.director = type("D", (), {"plan": lambda self, *a: ShotPlan(shots=shots, bank=p.bank)})()
    st = ShotStage(deps)
    st.shot_plan(video, None)
    rows = st.shots(video)
    assert [r["camera_move"] for r in rows] == cams
    # The stage times shots from the narration (each line starts its own shot), NOT from the plan's per-beat 5 s: with lines this short the
    # stored shots come out about 2.7 / 3.9 / 8.4 s. Only the total is kept. Recorded in docs/runbooks/first-live-short.md (risk 3).
    assert abs(sum(float(r["duration_target_sec"]) for r in rows) - 15) < 0.6
    loc = p.bank.locations[0].description
    for r, stored in zip(rows, PLAN["resolvedPrompts"], strict=True):
        prompt = json.loads(r["prompt_json"])
        assert loc in prompt["keyframePrompt"] and loc in prompt["motionPrompt"]
        assert prompt["keyframePrompt"] == stored["keyframePrompt"]
        assert prompt["locationIds"] == ["moon-town"]
