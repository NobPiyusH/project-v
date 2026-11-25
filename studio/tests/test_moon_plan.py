"""The first-live-Short plan (docs/shorts/moon-10x-closer-plan.json, reference style: ONE continuous 10 s shot = 2 chained 5 s beats, a shore plate,
the Moon as a separate layer) loads as the studio's own schemas and passes the planner validator. Guards against the file drifting from bank.py /
VideoPlan / the camera presets / tools/film4k/moon_layer.py. No network."""

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


def test_two_chained_five_second_beats_make_one_ten_second_shot_with_one_location_no_characters():
    sc = banked().scenes[0]
    assert [b.durationSec for b in sc.beats] == [5, 5] and sc.lengthSec == 10 and len(banked().scenes) == 1, "ONE continuous shot, at most 2 chained beats"
    assert PLAN["bank"].get("characters") is None and len(PLAN["bank"]["locations"]) == 1


def test_every_prompt_carries_the_bank_text_verbatim_and_the_file_is_not_stale():
    p = banked()
    loc = p.bank.locations[0].description
    for beat, stored in zip(p.scenes[0].beats, PLAN["resolvedPrompts"], strict=True):
        assert loc in stored["keyframePrompt"] and loc in stored["motionPrompt"]
        assert stored["keyframePrompt"] == beat_prompt(p.bank, beat, "image")
        assert stored["motionPrompt"] == beat_prompt(p.bank, beat, "video")


def test_the_in_plate_moon_fallback_has_its_own_resolved_prompts_with_the_same_bank_text():
    p = banked()
    fb = PLAN["moon"]["fallback"]
    assert PLAN["moon"]["mode"] == "layer" and fb["mode"] == "in_plate"
    assert all("no Moon" in b.prompt for b in p.scenes[0].beats), "layer mode: the plate sky is empty"
    assert all("Moon" in t and "no Moon" not in t for t in fb["beatPrompts"])
    for beat, text, stored in zip(p.scenes[0].beats, fb["beatPrompts"], fb["resolvedPrompts"], strict=True):
        alt = beat.model_copy(update={"prompt": text})
        assert stored["keyframePrompt"] == beat_prompt(p.bank, alt, "image") and stored["motionPrompt"] == beat_prompt(p.bank, alt, "video")


def test_video_plan_survives_the_planner_normaliser_unchanged(presets):
    plan = Planner(presets, None, "", "m", "m").normalise(VideoPlan(**PLAN["video"]), "x")
    assert (plan.format, plan.aspectRatio, plan.durationSec, plan.narration) == ("short", "9:16", 10, "full")
    assert plan.needsResearch is False and plan.visualTypes == ["ai_video"]


def test_generation_settings_are_legal_for_wan_and_the_camera_presets_exist():
    g = PLAN["generation"]
    assert (g["width"], g["height"], g["frames"], g["fps"]) == (720, 1280, 81, 16)
    assert g["width"] % 16 == 0 and g["height"] % 16 == 0 and (g["frames"] - 1) % 4 == 0
    sys.path.insert(0, str(ROOT / "tools" / "film4k"))
    from camera_presets import TESTED, plan_camera, preset

    assert g["cameraPresets"] == ["slow_push", "slow_push"], "reference style: locked-off or gentle; the tested slow push, not a pan or zoom"
    assert all(preset(n).engine == "fun_camera" and n in TESTED for n in g["cameraPresets"])
    assert plan_camera(1, 2, g["cameraPresets"]) == g["cameraPresets"]
    assert len(g["cameraPresets"]) == len(banked().scenes[0].beats)


def test_the_moon_layer_settings_drive_the_composite_helper_and_only_grow():
    """The plan's `moon.layer` block is exactly what tools/film4k/moon_layer.py takes; the Moon grows, rises, and the caption sits in the first seconds."""
    sys.path.insert(0, str(ROOT / "tools" / "film4k"))
    import moon_layer as ml

    lay, cap = PLAN["moon"]["layer"], PLAN["overlay"]["caption"]
    assert 1 <= lay["scaleTo"] / lay["scaleFrom"] <= 4 and lay["cyTo"] < lay["cyFrom"], "grows and rises"
    assert lay["diameterFrac"] * lay["scaleTo"] <= 0.8, "the grown Moon still fits the frame width"
    cmd = ml.composite_cmd("plate.mp4", "moon.png", "out.mp4", w=PLAN["generation"]["width"], h=PLAN["generation"]["height"], dur=PLAN["video"]["durationSec"],
                           fps=PLAN["generation"]["fps"], diameter_frac=lay["diameterFrac"], scale_from=lay["scaleFrom"], scale_to=lay["scaleTo"],
                           cx=lay["cx"], cy_from=lay["cyFrom"], cy_to=lay["cyTo"], ease=lay["ease"], glow=lay["glow"], glow_scale=lay["glowScale"],
                           caption=cap["text"], caption_start=cap["startSec"], caption_end=cap["endSec"])
    assert cmd[-1] == "out.mp4" and "drawtext" in cmd[cmd.index("-filter_complex") + 1]
    assert cap["text"] == "What if the Moon were 10x closer?" and cap["position"] == "top" and 0 <= cap["startSec"] < cap["endSec"] <= 5


def test_narration_is_about_twenty_five_words_and_fits_ten_seconds():
    words = sum(len(line["text"].split()) for line in PLAN["script"])
    assert 22 <= words <= 28, words
    assert words / PLAN["video"]["durationSec"] <= 3.0, "no faster than about 3 words a second"


def test_narration_lines_start_inside_their_own_beat():
    for line in PLAN["script"]:
        beat = banked().scenes[0].beats[line["beat"] - 1]
        assert beat.startSec <= line["startSec"] < beat.startSec + beat.durationSec


def test_a_beat_naming_an_unknown_location_would_be_rejected():
    bad = BankedPlan(bank=banked().bank, scenes=[ScenePlan(lengthSec=5, beats=[BeatPlan(prompt="x", locationIds=["nowhere"], durationSec=5)])])
    assert validate(bad) == ["scene 1 beat 1: unknown location 'nowhere'"]


def test_the_moon_shot_plan_goes_through_the_real_shot_stage_with_the_bank_pasted_in(scratch_db):
    """The plan's two beats as the Director would return them: stored shots carry the bank text word for word, 5 s each, camera moves kept."""
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
    video = seed_video(scratch_db, duration=10)
    db_concept(scratch_db, video)
    voiced_script(scratch_db, video, [3.4, 3.6])  # about what the two narration lines take spoken
    deps = make_deps(scratch_db, None, FakeDirector(shots))
    deps.director = type("D", (), {"plan": lambda self, *a: ShotPlan(shots=shots, bank=p.bank)})()
    st = ShotStage(deps)
    st.shot_plan(video, None)
    rows = st.shots(video)
    assert [r["camera_move"] for r in rows] == cams
    # A video WITHOUT an imported plan is timed from the narration; a video created from the plan file uses the plan's 5 s beats instead
    # (test_a_plan_video_is_timed_from_its_five_second_beats_not_from_the_narration). Only the total is checked here.
    assert abs(sum(float(r["duration_target_sec"]) for r in rows) - 10) < 0.6
    loc = p.bank.locations[0].description
    for r, stored in zip(rows, PLAN["resolvedPrompts"], strict=True):
        prompt = json.loads(r["prompt_json"])
        assert loc in prompt["keyframePrompt"] and loc in prompt["motionPrompt"]
        assert prompt["keyframePrompt"] == stored["keyframePrompt"]
        assert prompt["locationIds"] == ["moon-shore"]
