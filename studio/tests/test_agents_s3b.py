"""S3b: Writer, Director, Creative Director prompts and typed outputs; Claude researcher parsing. Offline: fake LLM, stand-in
response objects. (Java had only a live researcher test; these are new.)"""

from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from studio.agents.creative_director import RUBRIC, CreativeDirectorAgent
from studio.agents.director import DIAGRAM_RULE, STOCK_RULE, DirectorAgent, TimedLine
from studio.agents.jfmt import fixed, jround
from studio.agents.researcher import ClaudeResearcher, parse
from studio.agents.types import Concept, CreativeReview, PlannedShot, Script, ShotPlan
from studio.agents.writer import VOICES, WriterAgent, facts_text, notes
from studio.media.types import Fact, ImageReviewResult, ResearchRequest, Usage
from studio.media.wire import from_json, to_json
from studio.planner.plan import VideoPlan
from studio.planner.presets import StylePresets
from studio.tests.test_pricing import TEST_CONFIG, at


class FakeLlm:
    def __init__(self):
        self.calls = []

    def call(self, video_id, purpose, system, user, tp, preferred=None):
        self.calls.append((purpose, system, user, tp, preferred))
        return tp()


def plan(narration="full", duration=60, fmt="long_form"):
    return VideoPlan(title="Owls", durationSec=duration, tone="calm", language="en", narration=narration, format=fmt,
                     stylePreset="documentary_story", aspectRatio="16:9", mustInclude=["owl", "moon"])


def presets():
    return StylePresets(TEST_CONFIG)


def test_java_number_formatting_rounds_half_up():
    assert fixed(0.5, 0) == "1" and fixed(2.5, 0) == "3" and fixed(0.125, 2) == "0.13" and fixed(7.25, 1) == "7.3"
    assert jround(2.5) == 3 and jround(-2.5) == -2


def test_voices_list_matches_the_cast():
    assert VOICES[0] == "Charon" and len(VOICES) == 20 and "Aoede" in VOICES


def test_concept_goes_to_claude_first_with_facts_and_owner_notes():
    llm = FakeLlm()
    facts = [Fact("Owls turn heads 270 degrees", [], "unconfirmed"), Fact("Silent flight", [])]
    WriterAgent(llm, presets(), "claude:x").concept("v", "owls", plan(), facts, "make it warmer")
    purpose, system, user, tp, preferred = llm.calls[0]
    assert (purpose, tp, preferred) == ("CONCEPT", Concept, "claude:x")
    assert "Must include: owl, moon" in user and "[numbers not confirmed" in user and "- Silent flight" in user
    assert "The owner rejected the previous version with these notes; follow them exactly:\nmake it warmer" in user
    assert "head writer of a premium documentary studio" in system


@pytest.mark.parametrize("narration,needle", [
    ("none", "There is NO narration. Write 7 visual beats"),
    ("sparse", "about 45 words in total, in 6-8 short lines"),
    ("full", "at most 98 words in total"),  # round(60 * 2.1 * 0.78) = 98
])
def test_script_length_rule_follows_the_narration_mode(narration, needle):
    llm = FakeLlm()
    WriterAgent(llm, presets(), "").script("v", "owls", plan(narration), Concept(logline="l", bigIdea="b"), None, None)
    purpose, system, user, tp, _ = llm.calls[0]
    assert purpose == "SCRIPT" and tp is Script and needle in system
    assert "Charon: informative, calm, deep" in system and "Clock: none" in user and "(no research for this video)" in user


def test_notes_from_the_inspector_are_passed_through_unchanged():
    assert notes("The Inspector found X") == "The Inspector found X\n" and notes("  ") == ""
    assert facts_text(None) == "(no research for this video)"


def test_director_prompt_beats_pacing_lines_and_optional_lane_rules():
    llm = FakeLlm()
    lines = [TimedLine(0, "Hello", True, 3.25), TimedLine(1, "(music)", False, 2.0)]
    concept = Concept(logline="l", bigIdea="b", clockLabel="Years")
    DirectorAgent(llm, presets(), "claude:x").plan("v", plan(fmt="short"), concept, lines, 120, 30, None)
    _, system, user, tp, preferred = llm.calls[0]
    assert tp is ShotPlan and preferred == "claude:x"
    assert "fast cuts: shots of 1.5-3 seconds" in system and "exactly 30 seconds" in system
    assert "about 120 BPM" in system and "(0.50 s)" in system and "10-20% of shots that matter most" in system
    assert DIAGRAM_RULE not in system and STOCK_RULE not in system
    assert "On-screen clock: Years" in user and "0 | voice | 3.3 | Hello" in user and "1 | visual | 2.0 | (music)" in user
    llm = FakeLlm()
    DirectorAgent(llm, presets(), "", diagram_lane=True, stock_lane=True).plan("v", plan(), concept, lines, 0, 30, None)
    system = llm.calls[0][1]
    assert DIAGRAM_RULE in system and STOCK_RULE in system and "cinematic pacing" in system and "(0.50 s)" in system


def test_planned_shot_lane_helpers():
    assert PlannedShot(lane="Diagram").diagram() and PlannedShot(lane="stock").stock() and not PlannedShot().stock()


def test_creative_director_sends_the_shared_rubric():
    llm = FakeLlm()
    CreativeDirectorAgent(llm).review_shot_plan("v", "ctx")
    purpose, system, user, tp, preferred = llm.calls[0]
    assert (purpose, user, tp, preferred) == ("CREATIVE_REVIEW", "ctx", CreativeReview, None)
    assert RUBRIC in system and system.endswith("Return only the JSON object.") and "Judge MEANING, not words." in system


def test_typed_review_round_trips_through_the_worker_contract():
    review = CreativeReview.model_validate({"video": {"arc": 4}, "acts": [{"act": "A", "shotsToFix": [2]}],
                                            "shots": [{"shot": 2, "fixPrompt": "x"}]})
    res = ImageReviewResult(review, "m", Usage(1, 2, 0, 3), 5)
    data = to_json(res)
    assert data["imagesSent"] == 5 and data["review"]["acts"][0]["shotsToFix"] == [2]
    back = from_json(ImageReviewResult, data)
    assert back.review.shots[0].fixPrompt == "x" and back.review.video.arc == 4
    # unknown / missing LLM fields get Java's defaults
    assert CreativeReview.model_validate({"shots": [{"shot": 1}]}).shots[0].literal is False


# ---- researcher


def block(**kw):
    return NS(**kw)


def research_message(pause=False):
    cite = NS(type="web_search_result_location", url="https://a.org/x", title="A page")
    content = [
        block(type="web_search_tool_result", content=[NS(url="https://a.org/x", title="A page"), NS(url="https://b.org/y", title="B page")]),
        block(type="text", text="- Owls fly silently.\n", citations=[cite]),
        block(type="text", text="- Their eyes cannot move.\n2) Heads turn far.", citations=None),
    ]
    usage = NS(input_tokens=1000, output_tokens=500, server_tool_use=NS(web_search_requests=2))
    return NS(content=content, usage=usage, stop_reason="pause_turn" if pause else "end_turn")


def test_parse_links_each_fact_to_the_pages_cited_and_orders_sources():
    r = parse([research_message()], "claude:m")
    assert [f.text for f in r.facts] == ["Owls fly silently.", "Their eyes cannot move.", "Heads turn far."]
    assert [s.uri for s in r.facts[0].sources] == ["https://a.org/x"] and r.facts[1].sources == []
    assert [s.uri for s in r.sources] == ["https://a.org/x", "https://b.org/y"]  # cited first, then the rest found
    assert (r.usage.prompt_tokens, r.usage.output_tokens, r.usage.total_tokens) == (1000, 500, 1500)


def test_parse_without_facts_is_a_model_problem():
    empty = NS(content=[block(type="text", text="No bullets here.", citations=None)], usage=NS(input_tokens=1, output_tokens=1))
    with pytest.raises(RuntimeError, match="returned no facts"):
        parse([empty], "claude:m")


class FakeClaude:
    def __init__(self, responses, configured=True):
        self.responses, self.sent, self._c = list(responses), [], configured

    def configured(self):
        return self._c

    def send(self, **params):
        self.sent.append(params)
        return self.responses.pop(0)


class Ledger:
    def __init__(self):
        self.rows = []

    def record(self, *a):
        self.rows.append(a)


class Budget:
    def __init__(self, ok=True):
        self.ok = ok

    def can_spend_claude(self, x):
        return self.ok


REQ = ResearchRequest("Owls", "About owls", ["moon"], "en", 8, None, "be kind")


def test_research_continues_a_paused_turn_once_and_books_every_turn():
    claude, ledger = FakeClaude([research_message(pause=True), research_message()]), Ledger()
    r = ClaudeResearcher(claude, ledger, Budget(), at("2026-11-19"), "claude:claude-sonnet-5-5").research("v", REQ)
    assert len(claude.sent) == 2 and len(ledger.rows) == 2 and r.facts
    first = claude.sent[0]
    assert first["model"] == "claude-sonnet-5-5" and first["tools"][0]["max_uses"] == 5
    assert "Topic: Owls" in first["messages"][0]["content"] and "Owner notes: be kind" in first["messages"][0]["content"]
    assert claude.sent[1]["messages"][1]["role"] == "assistant"
    _, provider, amount, units, unit_type, estimated, note = ledger.rows[0]
    p = at("2026-11-19")
    assert provider == "anthropic" and units == Decimal(1500) and estimated is False and "2 web searches" in note
    assert amount == p.claude_inr(1000, 500) + p.claude_search_inr(2)


def test_research_refuses_without_a_key_or_when_the_claude_cap_is_reached():
    p = at("2026-11-19")
    with pytest.raises(RuntimeError, match="no Claude backup researcher"):
        ClaudeResearcher(FakeClaude([], configured=False), Ledger(), Budget(), p).research("v", REQ)
    with pytest.raises(RuntimeError, match="monthly cap reached"):
        ClaudeResearcher(FakeClaude([]), Ledger(), Budget(ok=False), p).research("v", REQ)
