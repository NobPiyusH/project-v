"""Port of PlannerTest plus checks of the plan clean-up rules and the rule-based fallback."""

from types import SimpleNamespace

import pytest

from studio.planner.plan import VideoPlan
from studio.planner.planner import Planner, stages_for
from studio.planner.presets import StylePresets
from studio.tests.test_pricing import TEST_CONFIG
from studio.workflow.stage import Stage, StageMode

STORY = "claude:claude-sonnet-5-5"


class FakeLlm:
    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, 0

    def call_with_model(self, *a):
        self.calls += 1
        if self.error:
            raise self.error
        return self.answer


def planner(llm, key="k"):
    return Planner(StylePresets(TEST_CONFIG), llm, key, "gemini-x", STORY)


def raw():
    return VideoPlan(title="T", summary="s", format="short", durationSec=30, stylePreset="shorts_punchy", language="en",
                     needsResearch=False, narration="full", music="support", soundDesign=True, visualTypes=["ai_video"],
                     shortsCount=0, mustInclude=[], rationale="r")


def test_plan_comes_from_claude_first_and_source_names_the_model():
    o = planner(FakeLlm(SimpleNamespace(entity=raw(), model=STORY))).plan("v", "a short about owls", None)
    assert o.source == STORY and o.plan.title == "T" and o.fallback_reason is None


def test_gemini_answer_is_labelled_gemini():
    o = planner(FakeLlm(SimpleNamespace(entity=raw(), model="gemini-3.6-flash"))).plan("v", "owls", None)
    assert o.source == "gemini:gemini-3.6-flash"


def test_an_llm_failure_falls_back_to_rules():
    o = planner(FakeLlm(error=RuntimeError("PLAN: no model answered"))).plan("v", "a short about owls", None)
    assert o.source == "rules" and "RuntimeError" in o.fallback_reason


def test_no_gemini_key_means_rules_without_calling_the_llm():
    llm = FakeLlm()
    assert planner(llm, key="").plan("v", "owls", None).source == "rules"
    assert llm.calls == 0


def test_rule_based_fallback_reads_style_length_and_language():
    p = planner(None, key="").fallback("explain how black holes work in 3 minutes, in Hindi", "test").plan
    assert (p.stylePreset, p.durationSec, p.language, p.needsResearch) == ("explainer_clean", 180, "hi", True)
    s = planner(None, key="").fallback("a vertical reel of owls, 20 seconds", "test").plan
    assert (s.format, s.durationSec, s.aspectRatio, s.shortsCount) == ("short", 20, "9:16", 0)


def test_normalise_clamps_and_fills_gaps():
    n = planner(None).normalise(VideoPlan(format="weird", durationSec=99999, visualTypes=["junk"], narration="none",
                                          shortsCount=99, language="EN"), "My prompt")
    assert n.durationSec <= 3600 and n.visualTypes == ["ai_video"] and n.voice == "" and n.shortsCount == 10
    assert (n.language, n.title, n.summary) == ("en", "My prompt", "My prompt")


def test_stages_skip_what_the_plan_does_not_need():
    plan = VideoPlan(needsResearch=False, music="none", narration="none")
    s = stages_for(plan)
    assert Stage.PLAN in s and not {Stage.RESEARCH, Stage.MUSIC, Stage.NARRATION} & set(s)
    assert len(stages_for(VideoPlan(needsResearch=True, music="lead", narration="full"))) == len(Stage)


def test_stage_mode_parse_defaults_to_auto():
    assert StageMode.parse(None) is StageMode.AUTO and StageMode.parse(" review ") is StageMode.REVIEW
    with pytest.raises(ValueError):
        StageMode.parse("nope")
