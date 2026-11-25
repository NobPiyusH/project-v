"""Narration length check at plan time: the Moon plan's two lines (about 12 s of speech for a 10 s plan) are caught before anything is made."""

import json
from pathlib import Path

import pytest

from studio.planner import imported, narration

ROOT = Path(__file__).resolve().parents[2]
MOON = json.loads((ROOT / "docs/shorts/moon-10x-closer-plan.json").read_text(encoding="utf-8"))
# The repo plan was shortened after this check caught it (25 Nov 2026); these tests keep the line exactly as it ran live (31% over its beat).
MOON["script"][1]["text"] = "Tides would climb far higher, flooding coasts twice a day beneath one enormous Moon."


def test_estimate_uses_words_per_second_and_punctuation_pauses():
    assert narration.estimate_sec("one two three four five") == 2.0  # 5 words / 2.5
    assert narration.estimate_sec("one two, three four five.") == 2.7  # + a comma (0.3) + the closing full stop (0.4)
    assert narration.estimate_sec("One two. Three four five.") == 2.8  # two sentence pauses
    assert narration.estimate_sec("") == 0.0


def test_language_override_is_slower_for_hindi():
    text = "one two three four five six seven"
    assert narration.estimate_sec(text, "hi") > narration.estimate_sec(text, "en") > narration.estimate_sec(text, "es")
    assert narration.estimate_sec(text, "xx") == narration.estimate_sec(text, "en")  # unknown language: default rate


def test_levels_ok_warn_fail_and_plain_message():
    texts = ["a b c d e f g h i j", "a b c d e f g h i j", "a b c d e f g h i j"]  # 10 words = 4.0 s
    c = narration.check_lines(texts, [4.0, 3.5, 2.5])  # on time; 14% over; 60% over
    assert [ln.level for ln in c.lines] == ["ok", "warn", "fail"]
    assert c.lines[1].message == "line 2 needs about 4 s but the beat is 3.5 s: shorten it by about 2 words"
    assert c.problems == [c.lines[2].message] and "line 3 needs about 4 s but the beat is 2.5 s" in c.problems[0]


def test_moon_plan_lines_run_long_for_their_beats():
    nc = imported.parse(MOON).narrationCheck
    assert nc is not None and len(nc["lines"]) == 2
    assert nc["estimatedTotalSec"] > 10 * 1.10  # the live run: about 12 s of speech in a 10 s plan (estimate 11.4 s)
    assert nc["totalOverPct"] > 10
    assert [ln["level"] for ln in nc["lines"]] == ["warn", "fail"]
    assert nc["warnings"][0].startswith("line 1 needs about ") and "the beat is 4.7 s" in nc["warnings"][0]
    assert nc["problems"] == ["line 2 needs about 6.3 s but the beat is 4.8 s: shorten it by about 4 words"]


def test_validate_doc_warns_between_10_and_25_percent_and_fails_above():
    assert imported.validate_doc(MOON)["ok"] is False  # the file as it ran live: line 2 is 31% over its beat
    fitted = json.loads(json.dumps(MOON))
    fitted["script"][1]["text"] = "Tides would climb far higher, flooding coasts twice a day beneath one Moon."  # 13 words: 5.9 s in 4.8 s
    warned = imported.validate_doc(fitted)
    assert warned["ok"] is True and len(warned["warnings"]) == 2 and warned["problems"] == []
    imported.initial_plan_json(fitted)  # a warning does not block creation
    with pytest.raises(ValueError, match="line 2 needs about 6.3 s"):
        imported.initial_plan_json(MOON)
    imported.parse(MOON)  # an already stored plan still loads: the check is enforced at creation only
    fitted["script"][1]["text"] = "Tides would climb far higher, flooding coasts twice a day."
    fine = imported.validate_doc(fitted)
    assert fine["ok"] is True and len(fine["warnings"]) == 1  # only line 1 (5.5 s in 4.7 s)


def test_validate_doc_returns_problems_for_a_broken_plan_instead_of_raising():
    out = imported.validate_doc({"video": {}})
    assert out["ok"] is False and out["problems"] and out["narration"] is None


def test_rule_based_plans_compare_the_whole_script_with_the_planned_length():
    c = narration.check_spoken_total(["word " * 40], 10.0)  # 16 s of speech in 10 s
    assert c.lines[0].level == "fail" and c.problems[0].startswith("the narration needs about 16 s but the video is 10 s")
    assert narration.check_spoken_total(["word " * 20], 10.0).problems == []
