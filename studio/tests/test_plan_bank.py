"""Character/location bank: verbatim injection, validation, old plans still load, shot-stage wiring. No network."""

import json

import pytest

from studio.agents.director import BANK_RULE, DirectorAgent, TimedLine
from studio.agents.types import Concept, ShotPlan
from studio.planner.bank import (
    Bank,
    BankedPlan,
    BankEntry,
    BeatPlan,
    PlanRejected,
    ScenePlan,
    beat_prompt,
    ensure_valid,
    inject,
    references,
    validate,
)
from studio.production.shot_stage import ShotStage
from studio.tests.test_agents_s3b import FakeLlm, plan, presets
from studio.tests.test_shot_stage import FakeDirector, db_concept, planned, voiced_script, world  # noqa: F401

DRAGON = "A colossal dark bat-winged dragon, black leathery wings, ember-orange eyes, long neck."
CITY = "A flooded ruined city at dusk, leaning concrete towers, grey overcast sky."


def bank() -> Bank:
    return Bank(characters=[BankEntry(id="dragon", description=DRAGON, referenceImage="refs/dragon.png")],
                locations=[BankEntry(id="city", description=CITY)])


def test_every_beat_that_names_the_dragon_gets_the_identical_description():
    a = beat_prompt(bank(), BeatPlan(prompt="Wide view over the skyline", characterIds=["dragon"], locationIds=["city"]))
    b = beat_prompt(bank(), BeatPlan(prompt="Close on the creature banking", characterIds=["dragon"]))
    assert DRAGON in a and DRAGON in b and CITY in a and CITY not in b
    assert a.startswith("Wide view over the skyline.")


def test_video_prompt_adds_the_continuity_note_and_image_prompt_does_not():
    beat = BeatPlan(prompt="The creature turns away", characterIds=["dragon"], continuity="wings stay black, same dusk light")
    assert "Keep the same as the previous beat: wings stay black, same dusk light." in beat_prompt(bank(), beat, "video")
    assert "Keep the same" not in beat_prompt(bank(), beat, "image")


def test_a_beat_with_nothing_to_inject_is_unchanged():
    assert beat_prompt(None, BeatPlan(prompt="  plain words ")) == "plain words"
    assert beat_prompt(bank(), BeatPlan(prompt="plain words"), "video") == "plain words"


def test_repeated_ids_are_pasted_once_and_unknown_ids_raise():
    assert inject(bank(), ["dragon", "dragon"], None) == DRAGON
    with pytest.raises(PlanRejected, match="unknown character 'wyvern'"):
        inject(bank(), ["wyvern"], None)


def test_validator_rejects_unknown_ids_and_timings_that_do_not_fill_the_scene():
    beats = [BeatPlan(startSec=0, durationSec=5, characterIds=["dragon"]), BeatPlan(startSec=5, durationSec=4, locationIds=["moon"])]
    problems = validate(BankedPlan(bank=bank(), scenes=[ScenePlan(lengthSec=10, beats=beats)]))
    assert any("unknown location 'moon'" in p for p in problems)
    assert any("add up to 9s but the scene is 10s" in p for p in problems)
    with pytest.raises(PlanRejected):
        ensure_valid(BankedPlan(bank=bank(), scenes=[ScenePlan(lengthSec=10, beats=beats)]))


def test_validator_accepts_a_good_plan_and_catches_gaps_and_bank_errors():
    good = ScenePlan(lengthSec=10, beats=[BeatPlan(startSec=0, durationSec=4, characterIds=["dragon"]),
                                          BeatPlan(startSec=4, durationSec=6, locationIds=["city"])])
    assert validate(BankedPlan(bank=bank(), scenes=[good])) == []
    gap = ScenePlan(lengthSec=10, beats=[BeatPlan(startSec=0, durationSec=4), BeatPlan(startSec=5, durationSec=5)])
    assert any("starts at 5s but the previous beat ends at 4s" in p for p in validate(BankedPlan(scenes=[gap])))
    bad = Bank(characters=[BankEntry(id="a", description="x"), BankEntry(id="a", description=""), BankEntry(id="", description="y")])
    problems = validate(BankedPlan(bank=bad))
    assert "character id 'a' is used twice" in problems and "a character has no id" in problems and "character 'a' has no description" in problems


def test_references_lists_known_reference_images_once():
    assert references(bank(), ["dragon", "ghost"], ["city"]) == ["refs/dragon.png"]


def test_old_plans_without_any_bank_field_still_load():
    sp = ShotPlan.model_validate({"shots": [{"sceneTitle": "A", "durationSec": 3, "keyframePrompt": "k"}]})
    assert sp.bank is None and sp.shots[0].characterIds is None and sp.shots[0].claims is None
    assert BankedPlan.model_validate({}).scenes is None


def test_director_prompt_carries_the_bank_rule_unless_the_flag_is_off():
    lines, concept = [TimedLine(0, "Hello", True, 3.0)], Concept(logline="l", bigIdea="b")
    on, off = FakeLlm(), FakeLlm()
    DirectorAgent(on, presets(), "x").plan("v", plan(), concept, lines, 0, 30, None)
    DirectorAgent(off, presets(), "x", shot_bank=False).plan("v", plan(), concept, lines, 0, 30, None)
    assert BANK_RULE in on.calls[0][1] and BANK_RULE not in off.calls[0][1]


def banked_shots():
    return [planned(0, 0, 3, characterIds=["dragon"], locationIds=["city"], continuity="same dusk light", claims=[" Dragons are fiction. "]),
            planned(1, None, 3, characterIds=["dragon", "ghost"])]


def run_stage(db, video, shots, bank_, **kw):
    db_concept(db, video)
    voiced_script(db, video, [4.0])
    from studio.tests.test_shot_stage import make_deps
    deps = make_deps(db, None, FakeDirector(shots))
    deps.director = type("D", (), {"plan": lambda self, *a: ShotPlan(shots=shots, bank=bank_)})()
    st = ShotStage(deps, **kw)
    return st, st.shot_plan(video, None)


def test_shot_plan_pastes_the_bank_into_every_shot_that_uses_it(world):  # noqa: F811
    db, video = world
    st, result = run_stage(db, video, banked_shots(), bank())
    rows = [json.loads(r["prompt_json"]) for r in st.shots(video)]
    assert DRAGON in rows[0]["keyframePrompt"] and DRAGON in rows[1]["keyframePrompt"] and CITY in rows[0]["keyframePrompt"]
    assert "Keep the same as the previous beat: same dusk light." in rows[0]["motionPrompt"]
    assert rows[0]["characterIds"] == ["dragon"] and rows[1]["characterIds"] == ["dragon"], "the unknown id is dropped"
    assert rows[0]["bankReferences"] == ["refs/dragon.png"] and rows[0]["claims"] == ["Dragons are fiction."]
    assert "1 unknown bank id(s) were ignored" in result.summary


def test_shot_plan_leaves_prompts_alone_with_the_flag_off_or_no_bank(world):  # noqa: F811
    db, video = world
    st, _ = run_stage(db, video, banked_shots(), bank(), shot_bank=False)
    assert all(DRAGON not in json.loads(r["prompt_json"])["keyframePrompt"] for r in st.shots(video))
    st, _ = run_stage(db, video, banked_shots(), None)
    assert all("characterIds" not in json.loads(r["prompt_json"]) for r in st.shots(video))
