"""Research, story, music, narration and animatic stages on a scratch Postgres (port of the voice parts of ProductionPersistenceTest,
plus the stage logic the Java workflow test faked out)."""

import json
import uuid
from decimal import ROUND_HALF_UP, Decimal

import pytest

from studio.agents.types import Act, Concept, Script, ScriptLine
from studio.ledger.budget_guard import BudgetExceededError
from studio.media.types import (
    AnimaticResult,
    Fact,
    MusicResult,
    NarrationClip,
    NarrationResult,
    ResearchResult,
    Source,
    Usage,
)
from studio.media.wire import from_json
from studio.production.records import StoredScript
from studio.production.stages_m2a import AnimaticStage, MusicStage, ResearchStage, StoryStage, VoiceStage
from studio.tests.pworld import make_deps, scalar, seed_video


def script_of(db, video):
    return from_json(StoredScript, json.loads(scalar(db, "SELECT beat_sheet_json::text FROM script WHERE video_id = %(v)s", v=video)))


def line(db, video, index):
    return next(ln for ln in script_of(db, video).lines if ln.index == index)


def take(uri, seconds):
    return NarrationResult([NarrationClip(0, uri, seconds, Usage(10, 1000, 0, 1010), 1, True, "coverage 100%")], "gemini-3.8-flash-tts", "Charon")


@pytest.fixture
def world(scratch_db):
    scratch_db.execute("DELETE FROM cost_entry")  # the scratch database lives for the whole session: monthly caps start clean
    return scratch_db, seed_video(scratch_db)


# ---------------------------------------------------------------------------------------------------- voice


def test_narration_request_prices_the_words_and_shortens_the_style(world):
    db, video = world
    db.execute("UPDATE script SET beat_sheet_json = jsonb_set(beat_sheet_json, '{voiceStyle}', '\"Slow, hushed, awe-struck and warm. Very very patient indeed.\"')")
    r = VoiceStage(make_deps(db)).narration_request(video)
    assert r.style == "in a slow, hushed, awe-struck and warm. very very patient voice"  # first 8 words, lowercase
    assert [(x.index, x.text) for x in r.lines] == [(0, "A star is born.")] and r.voice_name == "Charon"
    assert r.model == "gemini-3.8-flash-tts" and r.check_model == "gemini-3.6-flash" and r.language == "en"


def test_a_missing_script_style_gets_the_default_voice_phrase(world):
    db, video = world
    db.execute("UPDATE script SET beat_sheet_json = beat_sheet_json - 'voiceStyle'")
    assert VoiceStage(make_deps(db)).narration_request(video).style == "in a calm documentary narrator's voice"


def test_line_redos_keep_the_voice_the_video_has_and_elevenlabs_credits_are_tracked(world):
    db, video = world
    v = VoiceStage(make_deps(db))
    assert v.current_provider(video) == "auto"
    v.save_narration(video, take("s3://projectv/videos/x/narration/line-000.wav", 2.0))
    assert v.current_provider(video) == "gemini"
    v.save_narration(video, NarrationResult([NarrationClip(0, "s3://projectv/videos/x/narration/line-000.wav", 2.1, None, 1, True, "coverage 100%")],
                                            "elevenlabs:eleven_v4_turbo", "George", 42))
    assert v.current_provider(video) == "elevenlabs"
    assert v.line_narration_request(video, [0], None).voice_provider == "elevenlabs"
    assert scalar(db, "SELECT count(*) FROM cost_entry WHERE video_id = %(v)s AND provider_id = 'elevenlabs' AND units = 42", v=video) == 1
    assert scalar(db, "SELECT count(*) FROM asset a JOIN provenance p ON p.id = a.provenance_id "
                      "WHERE a.video_id = %(v)s AND p.licence_id = '00000000-0000-0000-0000-00000000a008'", v=video) == 1


def test_edit_voice_and_switch_takes_round_trip_through_the_database(world):
    db, video = world
    v = VoiceStage(make_deps(db))
    edited = v.edit_line(video, 0, "A star ignites.", None)
    assert edited.edited is True and edited.original_text == "A star is born."
    assert scalar(db, "SELECT count(*) FROM preference_event WHERE video_id = %(v)s AND kind = 'edit_diff'", v=video) == 1

    v.save_line_narration(video, take("s3://b/t1.wav", 4.0), "in a calm voice")
    first = line(db, video, 0)
    assert first.audio_asset_id is not None and first.needs_voice is False and first.voice_status == "READY"

    v.save_line_narration(video, take("s3://b/t2.wav", 5.0), None)
    assert len(line(db, video, 0).audio_versions) == 2
    v.select_line_voice(video, 0, first.audio_asset_id)
    assert line(db, video, 0).audio_asset_id == first.audio_asset_id, "switched back to the first take"

    assert scalar(db, "SELECT count(*) FROM asset a JOIN provenance p ON p.id = a.provenance_id WHERE a.video_id = %(v)s AND a.kind = 'audio' "
                      "AND p.licence_id IS NOT NULL AND p.asset_id = a.id", v=video) == 2
    assert scalar(db, "SELECT sum(amount_inr) FROM cost_entry WHERE video_id = %(v)s", v=video) > 0
    with pytest.raises(RuntimeError):
        v.select_line_voice(video, 0, uuid.uuid4())
    with pytest.raises(LookupError):
        v.select_line_voice(video, 9, first.audio_asset_id)


def test_an_empty_line_edit_is_refused_and_an_unknown_line_not_found(world):
    db, video = world
    v = VoiceStage(make_deps(db))
    with pytest.raises(RuntimeError, match="can't be empty"):
        v.edit_line(video, 0, "   ", None)
    with pytest.raises(LookupError):
        v.edit_line(video, 7, "x", None)


def test_local_voices_record_no_cost_and_mark_lines_status(world):
    db, video = world
    v = VoiceStage(make_deps(db))
    v.save_narration(video, NarrationResult([NarrationClip(0, "s3://b/k.wav", 2.0, None, 3, False, "coverage 60%")], "kokoro:af", "Heart"))
    assert scalar(db, "SELECT count(*) FROM cost_entry WHERE video_id = %(v)s", v=video) == 0
    assert line(db, video, 0).voice_status == "IMPERFECT"
    v.mark_lines(video, [0], "REDOING", None)
    assert line(db, video, 0).voice_status == "REDOING"
    s = v.save_narration(video, NarrationResult([NarrationClip(0, "s3://b/k2.wav", 2.0, None, 3, False, "coverage 60%")], "kokoro:af", "Heart"))
    assert "2 re-voiced automatically; still imperfect after 3 takes: #0 (coverage 60%)" in s.summary


def test_line_redo_style_notes_replace_the_style_and_unspoken_lines_are_refused(world):
    db, video = world
    v = VoiceStage(make_deps(db))
    r = v.line_narration_request(video, [0, 1], "Slower and more hushed.")
    assert r.style == "in a voice that is slower and more hushed" and [x.index for x in r.lines] == [0]
    with pytest.raises(RuntimeError, match="None of those lines"):
        v.line_narration_request(video, [1], None)  # line 1 is a visual beat


# ---------------------------------------------------------------------------------------------------- research


def test_research_request_checks_the_cap_and_saves_facts_with_cost(world):
    db, video = world
    deps = make_deps(db)
    st = ResearchStage(deps)
    req = st.research_request(video, "focus on stars")
    assert (req.topic, req.max_facts, req.owner_notes, req.model) == ("p", 8, "focus on stars", "gemini-3.6-flash")
    facts = [Fact("Light takes 8 minutes.", [Source("NASA", "https://nasa.gov/x")], "confirmed"), Fact("A claim.", [], "unconfirmed")]
    r = ResearchResult(facts, [Source("NASA", "https://nasa.gov/x")], ["sun light"], "gemini-3.6-flash", Usage(1000, 500, 0, 1500))
    res = st.save_research(video, r)
    assert res.summary == ("2 facts (1 with sources) from 1 web sources. Source check: 1 confirmed on their page, 1 unconfirmed "
                           "(writer told to avoid those numbers).")
    row = db.rows("SELECT version, queries, model FROM research WHERE video_id = %(v)s", {"v": video})[0]
    assert row == {"version": 1, "queries": ["sun light"], "model": "gemini-3.6-flash"}
    assert [x.text for x in st.facts(video)] == ["Light takes 8 minutes.", "A claim."]
    spent = scalar(db, "SELECT amount_inr FROM cost_entry WHERE video_id = %(v)s AND note LIKE 'RESEARCH%%'", v=video)
    assert spent == (deps.pricing.text_inr(r.usage) + deps.pricing.grounding_inr(1)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)  # the column keeps 4 places
    st.save_research(video, r)
    assert scalar(db, "SELECT max(version) FROM research WHERE video_id = %(v)s", v=video) == 2


def test_claude_research_books_no_cost_here_and_says_so(world):
    db, video = world
    st = ResearchStage(make_deps(db))
    res = st.save_research(video, ResearchResult([Fact("x", [Source("a", "b")])], [], [], "claude:claude-sonnet-5-5", Usage(0, 0, 0, 0)))
    assert res.summary == "1 facts (1 with sources) from 0 web sources (Claude researcher)."
    assert scalar(db, "SELECT count(*) FROM cost_entry WHERE video_id = %(v)s", v=video) == 0


def test_research_is_refused_when_the_monthly_cap_is_spent(world):
    db, video = world
    deps = make_deps(db)
    deps.ledger.record(video, "gemini_api", deps.pricing.gemini_monthly_cap_inr() + Decimal(1), None, None, True, "spent")
    with pytest.raises(BudgetExceededError):
        ResearchStage(deps).research_request(video, None)


# ---------------------------------------------------------------------------------------------------- story


class FakeWriter:
    def __init__(self, script):
        self._script = script
        self.seen = []

    def concept(self, video_id, prompt, plan, facts, notes):
        self.seen.append(("concept", notes))
        return Concept(logline="Light is old.", bigIdea="Everything you see is the past.", clockLabel="Years", acts=[Act(title="A1", approxSec=30)],
                       emotionalArc="awe")

    def script(self, video_id, prompt, plan, concept, facts, notes):
        self.seen.append(("script", notes, concept.bigIdea))
        return self._script


def test_concept_then_script_are_stored_versioned_with_the_voice_and_word_counts(world):
    db, video = world
    db.execute("DELETE FROM script")
    s = Script(lines=[ScriptLine(text="Light is old, very old.", act="A1", purpose="hook", spoken=True, delivery="hushed"),
                      ScriptLine(text="Silence.", act="A1", purpose="beat", spoken=False)], voiceName="NotAVoice", voiceStyle="slow",
              musicBrief="strings")
    st = StoryStage(make_deps(db, FakeWriter(s)))
    c = st.concept(video, "warmer")
    assert c.summary == "Light is old. Big idea: Everything you see is the past."
    assert json.loads(scalar(db, "SELECT clock_definition::text FROM concept WHERE video_id = %(v)s", v=video)) == {
        "label": "Years", "logline": "Light is old.", "emotionalArc": "awe"}
    r = st.script(video, None)
    assert r.summary == "2 lines (1 spoken, 5 words), voice Charon."  # an unknown voice falls back to the default
    row = db.rows("SELECT version, narration_md, word_count, est_duration_sec FROM script WHERE video_id = %(v)s", {"v": video})[0]
    assert row == {"version": 1, "narration_md": "Light is old, very old.\n\n_[visual]_ Silence.\n\n", "word_count": 5, "est_duration_sec": 2}
    stored = script_of(db, video)
    assert stored.lines[0].delivery == "hushed" and stored.lines[0].spoken and not stored.lines[1].spoken
    st.script(video, None)
    assert scalar(db, "SELECT max(version) FROM script WHERE video_id = %(v)s", v=video) == 2


def test_no_narration_plan_makes_every_line_unspoken_and_an_empty_script_fails(world):
    db, _ = world
    video = seed_video(db, narration="none", with_script=False)
    s = Script(lines=[ScriptLine(text="Beat.", spoken=True)], voiceName="Charon")
    st = StoryStage(make_deps(db, FakeWriter(s)))
    with pytest.raises(RuntimeError, match="No concept yet"):
        st.script(video, None)
    st.concept(video, None)
    st.script(video, None)
    assert not script_of(db, video).lines[0].spoken
    empty = StoryStage(make_deps(db, FakeWriter(Script(lines=[]))))
    with pytest.raises(RuntimeError, match="empty script"):
        empty.script(video, None)


# ---------------------------------------------------------------------------------------------------- music


def test_music_request_and_cue(world):
    db, video = world
    st = MusicStage(make_deps(db))
    req = st.music_request(video, "no drums")
    assert req.max_segments == 3 and req.target_sec == 60.0 and req.model == "music"  # ceil(60/55)+1
    assert req.prompt == ("Instrumental film score, no vocals, no lyrics. About 60 seconds long.\nMood and style: strings\n"
                          "Video: Test (calm). It must leave room for a narrator's voice: avoid busy melodies in the mid range.\n"
                          "Owner's notes: no drums")
    res = st.save_music(video, MusicResult("s3://b/m.wav", 58.0, 96.0, [0.5, 1.1, 1.7], [0.5], 2, "lyria", [Usage(0, 0, 0, 0), Usage(0, 0, 0, 0)]))
    assert res.summary == "Score 58s at 96 BPM, 3 beats (2 Lyria generations)."
    assert scalar(db, "SELECT count(*) FROM music_cue WHERE video_id = %(v)s", v=video) == 1
    st.save_music(video, MusicResult("s3://b/m2.wav", 58.0, 96.0, [0.5], [0.5], 1, "lyria", None))
    assert scalar(db, "SELECT count(*) FROM music_cue WHERE video_id = %(v)s", v=video) == 1, "one cue per video"
    assert st.beat_times(video) == [0.5] and scalar(db, "SELECT count(*) FROM cost_entry WHERE video_id = %(v)s", v=video) == 2


# ---------------------------------------------------------------------------------------------------- animatic


def add_shot(db, video, ord_, start, dur, keyframe_uri=None, line_index=None, scene=[None]):
    if scene[0] is None or scene[1] != video:
        scene[:] = [uuid.uuid4(), video]
        db.execute("INSERT INTO scene (id, video_id, ord, title) VALUES (%(s)s, %(v)s, 0, 'S')", {"s": scene[0], "v": video})
    asset = None
    if keyframe_uri:
        asset = uuid.uuid4()
        db.execute("INSERT INTO asset (id, video_id, kind, uri_hot) VALUES (%(a)s, %(v)s, 'image', %(u)s)", {"a": asset, "v": video, "u": keyframe_uri})
    p = {"startSec": start, **({"lineIndex": line_index} if line_index is not None else {})}
    db.execute("INSERT INTO shot (id, scene_id, ord, duration_target_sec, description, prompt_json, keyframe_asset_id, overlay_text) "
               "VALUES (%(i)s, %(s)s, %(o)s, %(d)s, 'd', CAST(%(p)s AS jsonb), %(k)s, 'T')",
               {"i": uuid.uuid4(), "s": scene[0], "o": ord_, "d": dur, "p": json.dumps(p), "k": asset})


def test_animatic_holds_the_last_shot_so_no_word_is_cut(world):
    db, video = world
    add_shot(db, video, 0, 0.0, 3.0, "s3://b/k0.png", 0)
    add_shot(db, video, 1, 3.0, 2.0, None, None)  # no keyframe of its own: reuses the shot before
    # a spoken line voiced for 6 s, planned at 0.4 s: it ends at 6.4 s, past the 5 s of pictures
    s = script_of(db, video)
    voiced = [s.lines[0].with_new_take(__import__("studio.production.records", fromlist=["AudioVersion"]).AudioVersion(
        uuid.uuid4(), "s3://b/l0.wav", 6.0, "", None, None, s.lines[0].text), True), s.lines[1]]
    db.execute("UPDATE script SET beat_sheet_json = CAST(%(b)s AS jsonb)", {"b": json.dumps(__import__("studio.media.wire", fromlist=["to_json"]).to_json(
        StoredScript(1, voiced, "Charon", "calm", "strings", {"0": 0.4}, None)))})
    db.execute("INSERT INTO asset (id, video_id, kind, uri_hot) VALUES (%(a)s, %(v)s, 'audio', 's3://b/music.wav')", {"a": (m := uuid.uuid4()), "v": video})
    db.execute("INSERT INTO music_cue (id, video_id, asset_id, bpm, beat_times_json, sections_json) VALUES (%(i)s, %(v)s, %(a)s, 90, '[]', '{}')",
               {"i": uuid.uuid4(), "v": video, "a": m})
    req = AnimaticStage(make_deps(db)).animatic_request(video)
    assert [(x.index, x.image_uri) for x in req.shots] == [(1, "s3://b/k0.png"), (2, "s3://b/k0.png")]
    assert [(n.uri, n.start_sec, n.tempo) for n in req.narration] == [("s3://b/l0.wav", 0.4, 1.0)]
    assert req.shots[-1].duration_sec == pytest.approx(2.0 + (6.4 + 1.5 - 5.0))  # held until the voice ends + the tail
    assert req.total_sec == pytest.approx(7.9) and req.music_uri == "s3://b/music.wav" and req.aspect_ratio == "9:16"


def test_animatic_needs_a_keyframe_and_save_records_the_asset(world):
    db, video = world
    st = AnimaticStage(make_deps(db))
    with pytest.raises(RuntimeError, match="No keyframes"):
        st.animatic_request(video)
    res = st.save_animatic(video, AnimaticResult("s3://b/a.mp4", 31.6))
    assert res.summary.startswith("Animatic ready: 32s, 1080p")
    a = db.rows("SELECT a.width, a.height, p.model, p.ai_generated FROM video v JOIN asset a ON a.id = v.animatic_asset_id "
                "JOIN provenance p ON p.id = a.provenance_id WHERE v.id = %(v)s", {"v": video})[0]
    assert a == {"width": 1080, "height": 1920, "model": "ffmpeg animatic", "ai_generated": False}
