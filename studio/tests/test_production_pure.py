"""S5a: ports of ShotTimingTest, NarrationTimingTest, StoredLineTest, RedoStopRuleTest, DiagramSpecsTest (the pure part), plus ClipProgress."""

import json
import uuid

import pytest

from studio.media.types import ProducerItemVerdict
from studio.media.wire import from_json, to_json
from studio.production import diagram_specs as ds
from studio.production import redo_stop_rule as rsr
from studio.production import timing as nt
from studio.production.records import AudioVersion, StoredLine, StoredScript
from studio.video.clip_progress import ClipProgress

# ---- ShotTimingTest


def beat_grid(bpm, total):
    out, t, i = [], 0.0, 0
    while t <= total:
        out.append(round(t * 1000) / 1000.0)
        i += 1
        t = i * 60.0 / bpm
    return out


def test_fits_exactly_to_total_and_snaps_cuts_to_beats():
    out = nt.fit([5.0, 7.3, 6.1, 4.4], 30, beat_grid(120, 30), 2.5)
    assert sum(d for _, d in out) == pytest.approx(30.0) and out[0][0] == 0.0
    for start, dur in out[1:]:
        assert abs(start * 2 - round(start * 2)) < 1e-6, f"cut {start} should be on a 0.5 s beat"
        assert dur >= 2.5


def test_never_makes_a_shot_shorter_than_the_minimum_and_works_without_beats():
    out = nt.fit([1.0, 1.0, 20.0], 22, [], 2.5)
    assert len(out) == 3 and sum(d for _, d in out) == pytest.approx(22.0)
    assert nt.fit([None, -3.0], 10, None, 1.0)[0][1] == pytest.approx(5.0)


def test_anchored_shots_start_exactly_when_their_line_does():
    rough = [4.0] * 6
    out = nt.fit_anchored(rough, {2: 9.3, 4: 17.1}, 25, [], 1.2)
    assert out[2][0] == pytest.approx(9.3) and out[4][0] == pytest.approx(17.1) and sum(d for _, d in out) == pytest.approx(25.0)
    safe = nt.fit_anchored(rough, {2: 9.3, 4: 17.1, 5: 3.0}, 25, [], 1.2)  # an anchor going backwards in time is ignored
    assert all(d > 0 for _, d in safe)


# ---- NarrationTimingTest

NIGHT_SKY = [5.3, 10.4, 8.8, 9.0, 12.1, 9.4, 5.9]  # the real "Why is the night sky dark?" Short that cut off its last sentence


def spoken(*durations):
    return [nt.Line(i, True, d) for i, d in enumerate(durations)]


def test_night_sky_short_fits_in_60s_without_cutting_words():
    r = nt.plan(spoken(*NIGHT_SKY), 60, True)
    assert 1.0 < r.tempo <= nt.MAX_TEMPO and r.speech_end + nt.TAIL <= 60.0 + 1e-6 and r.total_sec == 60.0 and r.warning is not None


def test_lines_never_overlap_and_breathe():
    r = nt.plan(spoken(4, 6, 5), 30, False)
    prev_end, d = -1.0, [4, 6, 5]
    for i in range(3):
        start = r.starts[i]
        if prev_end >= 0:
            assert start == pytest.approx(prev_end + nt.GAP), "a breath between lines, no drift"
        prev_end = start + d[i]
    assert r.tempo == 1.0 and r.warning is None and r.total_sec == 30.0


def test_long_video_is_lengthened_rather_than_cutting_speech():
    r = nt.plan(spoken(30, 30, 30), 60, False)
    assert r.total_sec >= r.speech_end + nt.TAIL - 1e-6 and r.tempo == 1.0 and "lengthened" in r.warning


def test_too_long_for_a_short_says_so_instead_of_cutting():
    r = nt.plan(spoken(40, 40), 60, True)
    assert r.tempo == pytest.approx(nt.MAX_TEMPO) and r.total_sec > 60 and "too long for a Short" in r.warning


def test_visual_beats_become_music_only_moments():
    r = nt.plan([nt.Line(0, True, 5), nt.Line(1, False, 0), nt.Line(2, True, 5)], 30, False)
    assert r.starts[1] + nt.VISUAL_BEAT == pytest.approx(r.starts[2])


def test_place_uses_planned_starts_and_pushes_later_when_the_line_before_still_speaks():
    lines = [StoredLine(0, "a", spoken=True, audio_uri="s3://x/a.wav", duration_sec=5.0),
             StoredLine(1, "b", spoken=True, audio_uri="s3://x/b.wav", duration_sec=4.0),
             StoredLine(2, "visual", spoken=False), StoredLine(3, "novoice", spoken=True, audio_uri=None),
             StoredLine(4, "c", spoken=True, audio_uri="s3://x/c.wav", duration_sec=2.0)]
    script = StoredScript(1, lines, line_starts={"0": 1.0, "1": 3.0}, tempo=1.0)
    placed = nt.place(script, {4: 20.0})
    assert [(p.line.index, p.start_sec) for p in placed] == [(0, 1.0), (1, 6.15), (4, 20.0)]
    assert placed[0].end_sec == pytest.approx(6.0)
    assert nt.place(None, {}) == []
    fast = nt.place(StoredScript(1, lines[:1], tempo=2.0), {0: 0.0})
    assert fast[0].end_sec == pytest.approx(0.25 + 2.5), "voice speed shortens the placed line"


# ---- StoredLineTest


def voiced(text):
    # A line voiced before versions existed (old JSON): audio set, no versions list.
    return StoredLine(4, text, "Act 1", "hook", True, uuid.uuid4(), "s3://b/old.wav", 5.0, None, None, False, None, None, None, None)


def test_editing_text_flags_the_line_as_needing_voice_and_keeps_the_original():
    line = voiced("A star is born.").with_edit("A star ignites.", True)
    assert line.needs_voice and line.edited and line.original_text == "A star is born."
    assert line.with_edit("A star ignites in silence.", True).original_text == "A star is born.", "the writer's original survives later edits"


def test_saving_the_same_text_changes_nothing():
    line = voiced("Same.").with_edit("Same.", True)
    assert not line.needs_voice and not line.edited and line.original_text is None


def test_a_new_take_keeps_the_old_take_as_a_version_and_clears_needs_voice():
    old = voiced("Old text").with_edit("New text", True)
    new_asset = uuid.uuid4()
    line = old.with_new_take(AudioVersion(new_asset, "s3://b/new.wav", 4.2, "now", "coverage 100%", None, "New text"), True)
    assert line.audio_asset_id == new_asset and len(line.audio_versions) == 2, "old take kept as a version"
    assert not line.needs_voice and line.voice_status == "READY"
    back = line.with_active(line.audio_versions[1])
    assert back.audio_asset_id == old.audio_asset_id, "switching back to the old take works"
    assert back.needs_voice, "the old take says the old text, so it no longer matches"


def test_editing_a_line_back_to_what_its_voice_says_clears_needs_voice():
    line = voiced("A star is born.").with_edit("A star ignites.", True)
    assert line.needs_voice
    reverted = line.with_edit("A star is born.", True)
    assert not reverted.needs_voice and not reverted.edited and reverted.original_text is None


def test_an_imperfect_take_is_marked_so():
    line = voiced("x").with_new_take(AudioVersion(uuid.uuid4(), "u", 1, "now", "only 60% spoken", None, "x"), False)
    assert line.voice_status == "IMPERFECT"


def test_stored_script_json_round_trips_with_camel_case_and_old_data():
    a = uuid.uuid4()
    script = StoredScript(2, [voiced("hi").with_new_take(AudioVersion(a, "u", 1.5, "t", None, None, "hi"), True)], "Charon", "calm",
                          "brief", {"0": 1.5}, 1.04)
    data = to_json(script)
    assert data["voiceName"] == "Charon" and data["lineStarts"] == {"0": 1.5} and data["lines"][0]["audioVersions"][0]["assetId"] == str(a)
    back = from_json(StoredScript, json.loads(json.dumps(data)))
    assert back == script
    old = from_json(StoredScript, {"version": 1, "lines": [{"index": 0, "text": "t", "spoken": True, "futureField": 1}]})
    assert old.lines[0].text == "t" and old.tempo is None and old.lines[0].versions() == []


# ---- RedoStopRuleTest


def weak(*problems):
    return [ProducerItemVerdict(f"shot-{i + 1}", 4, False, p, "fix") for i, p in enumerate(problems)]


def test_the_first_redo_and_a_new_issue_are_left_alone():
    assert rsr.decide("redo", weak("visible human figure"), "", "n", [], None, False, False).action == rsr.Action.NONE
    other = rsr.decide("redo", weak("melted rail"), "", "n", weak("visible human figure"), "", True, False)
    assert other.action == rsr.Action.NONE and other.verdict == "redo"
    assert rsr.decide("approve", [], "", None, weak("x"), "", True, False).action == rsr.Action.NONE


def test_people_twice_in_a_row_get_one_firm_prompt_fix_then_the_owner_is_asked():
    first = rsr.decide("redo", weak("a woman walks across the frame"), "", "remove the woman", weak("visible human figures"),
                       "two shots contain visible human figures", True, False)
    assert first.action == rsr.Action.PROMPT_FIX and first.cause.key == "people" and first.verdict == "redo"
    assert first.redo_notes.startswith("RECURRING ISSUE (visible people)") and first.redo_notes.endswith("remove the woman")
    assert "silhouette" in first.cause.negative
    again = rsr.decide("redo", weak("silhouette of a man"), "", "remove him", weak("a woman walks across the frame"), "", True, True)
    assert again.action == rsr.Action.ASK and again.verdict == "ask" and "even after a prompt fix" in again.note


def test_an_unknown_repeated_issue_goes_straight_to_the_owner():
    r = rsr.decide("redo", weak("the bridge colour shifts mid clip"), "", "n", weak("bridge colour shifts in the clip"), "", True, False)
    assert r.action == rsr.Action.ASK and r.cause is None and r.verdict == "ask" and "bridge colour shifts" in r.note


def test_the_summary_names_the_cause_when_items_have_no_problem_text():
    assert rsr.issues([], "Two shots contain visible human figures.") == ["people"]
    assert rsr.issues(weak("letters on the sign"), "") == ["text"]


def test_the_rule_reads_the_previous_review_from_the_database(scratch_db):
    video = uuid.uuid4()
    scratch_db.execute("INSERT INTO video (id, prompt, status) VALUES (%(i)s, 'x', 'RUNNING')", {"i": video})
    cols = scratch_db.rows("SELECT column_name FROM information_schema.columns WHERE table_name = 'producer_decision'")
    assert {"items_json", "summary", "stage", "stop_rule", "video_id"} <= {c["column_name"] for c in cols}
    assert rsr.check(scratch_db, "video_id", video, "KEYFRAMES", "approve", [], "", None).action == rsr.Action.NONE
    first = rsr.check(scratch_db, "video_id", video, "KEYFRAMES", "redo", weak("visible human figures"), "", "n")
    assert first.action == rsr.Action.NONE, "no earlier review on this step"
    scratch_db.execute(
        "INSERT INTO producer_decision (id, video_id, stage, attempt, verdict, items_json, summary) "
        "VALUES (%(id)s, %(v)s, 'KEYFRAMES', 1, 'redo', CAST(%(items)s AS jsonb), 'people again')",
        {"id": uuid.uuid4(), "v": video, "items": json.dumps([{"id": "shot-1", "score": 3, "ok": False, "problem": "visible human figures", "fix": "f"}])})
    second = rsr.check(scratch_db, "video_id", video, "KEYFRAMES", "redo", weak("a man in frame"), "", "n")
    assert second.action == rsr.Action.PROMPT_FIX and second.cause.key == "people"
    with pytest.raises(ValueError):
        rsr.check(scratch_db, "id; DROP TABLE video", video, "KEYFRAMES", "redo", [], "", None)


# ---- DiagramSpecsTest


def good():
    return ds.SceneSpec(title="t", duration=8.0,
                        elements=[{"id": "gun", "type": "box", "x": -4, "y": 0, "w": 2, "h": 1, "text": "Gun"},
                                  {"id": "b", "type": "beam", "x1": -3, "y1": 0, "x2": 3, "y2": 0}],
                        steps=[{"at": 0.5, "dur": 1.0, "do": "show", "target": "gun"}, {"at": 2.0, "dur": 3.0, "do": "flow", "target": "b"}])


def bad():
    return ds.SceneSpec(title="t", duration=8.0, elements=[{"id": "gun", "type": "box", "x": 40, "y": 0, "w": 2, "h": 1}],
                        steps=[{"at": 0.5, "dur": 1.0, "do": "teleport", "target": "gun"}])


class FakeLlm:
    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    def call(self, video_id, purpose, system, user, tp, preferred=None):
        self.prompts.append((purpose, user))
        a = self.answers[min(len(self.prompts) - 1, len(self.answers) - 1)]
        if isinstance(a, Exception):
            raise a
        return a


VID = uuid.uuid4()


def test_valid_spec_is_used_as_is():
    llm = FakeLlm(good())
    m = ds.DiagramSpecs(llm, True).spec_for(VID, "TV", "Electrons are fired", "a gun fires", 7.2)
    assert m["duration"] == 8.0 and "fallback" not in m and m["version"] == 1 and len(llm.prompts) == 1


def test_invalid_spec_gets_exactly_one_repair_with_the_errors_shown():
    llm = FakeLlm(bad(), good())
    m = ds.DiagramSpecs(llm, True).spec_for(VID, "TV", "line", "desc", 8)
    assert "fallback" not in m and [p for p, _ in llm.prompts] == ["DIAGRAM_SPEC", "DIAGRAM_SPEC_REPAIR"]
    assert "outside the frame" in llm.prompts[1][1] and "teleport" in llm.prompts[1][1]


def test_still_invalid_after_repair_falls_back_to_a_text_card():
    llm = FakeLlm(bad())
    m = ds.DiagramSpecs(llm, True).spec_for(VID, "TV", "Magnets steer the beam", "Beam steering", 6)
    assert m["fallback"] is True and m["lines"] == ["Magnets steer the beam"] and m["title"] == "Beam steering" and len(llm.prompts) == 2


def test_a_failing_model_falls_back_without_retrying():
    llm = FakeLlm(RuntimeError("model down"))
    m = ds.DiagramSpecs(llm, True).spec_for(VID, "TV", "line", "desc", 6)
    assert m["fallback"] is True and "model down" in m["fallbackReason"] and len(llm.prompts) == 1


def test_validate_rules():
    assert ds.validate(good().model_dump() | {"duration": 8.0}) == []
    errs = ds.validate({"duration": 20, "elements": [{"id": "a", "type": "flow"}, {"id": "a", "type": "text", "x": 0, "y": 0},
                                                      {"id": "c", "type": "circle", "x": 0, "y": 5, "r": 1}],
                        "steps": [{"at": 0, "dur": 20, "do": "mix", "target": "c"}, {"at": 0, "dur": 1, "do": "flow", "target": "zzz"}]})
    text = "\n".join(errs)
    for needle in ("duration must be a number between 5 and 12", "type must be one of [arrow, beam, box, callout, circle", "is duplicated",
                   "needs text", "|y| <= 4", "ends after the duration 12.0s", "is not an element id"):
        assert needle in text, needle
    assert ds.validate({"duration": 8, "elements": []}) == ["elements must be a non-empty list"]


def test_fallback_trims_long_text():
    m = ds.fallback("x" * 120, "d" * 80, 6.0, None)
    assert len(m["title"]) == 60 and m["title"].endswith("...") and len(m["lines"][0]) == 90 and m["fallbackReason"] == "no spec"
    assert ds.fallback(None, None, 6.0, "why")["title"] == "Diagram" and ds.fallback("  ", "d", 6.0, "w")["lines"] == []


# ---- ClipProgress


def test_clip_progress_stop_and_batch_rules():
    p, v = ClipProgress(), uuid.uuid4()
    assert p.get(v) is None and not p.stop_requested(v)
    p.report(v, [{"shot": 1}], False)
    p.request_stop(v)
    assert p.get(v).clips == [{"shot": 1}] and p.stop_requested(v)
    p.report(v, [], True)
    assert p.get(v).finished and not p.stop_requested(v), "a finished batch clears its Stop"
    p.request_stop(v)
    p.start_batch(v)
    assert p.get(v) is None and not p.stop_requested(v), "a new batch starts clean"
