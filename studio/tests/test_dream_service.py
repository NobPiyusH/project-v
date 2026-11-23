"""Dream Shorts against a scratch Postgres: provenance, cost entries and routing decisions linked to the Short (rule 6). Port of
DreamServicePersistenceTest, DreamChoosePathTest (the part that does not need a workflow), and the DreamProducer cases."""

import json
import uuid
from decimal import Decimal

import pytest

from studio.dream import looks
from studio.dream.branches import COMMENTS, PathPick
from studio.dream.dream_studio import NewDream, WorkflowNotFoundError
from studio.dream.lessons import DreamLessons
from studio.dream.llm_types import DreamWorld, PlannedDreamShot
from studio.dream.service import scene_seed
from studio.dream.wire_types import (
    CheckIssue,
    DreamBedResult,
    DreamCheckResult,
    DreamFinishResult,
    DreamScoutRequest,
    DreamScoutResult,
    ScoutVideo,
)
from studio.media.types import (
    ClipOutput,
    ClipResult,
    KeyframeOutput,
    KeyframeResult,
    MusicResult,
    ProducerItemVerdict,
    ProducerReviewResult,
    Usage,
)
from studio.tests.dworld import asset, count, make_world, seed_short


@pytest.fixture
def w(scratch_db):
    scratch_db.execute("DELETE FROM routing_decision")
    scratch_db.execute("DELETE FROM gpu_timing")
    scratch_db.execute("DELETE FROM calibration")
    return make_world(scratch_db)


def kf(shot, uri, seed=1):
    return KeyframeOutput(str(shot), uri, "modal_zimage", "z", seed, 5.0, None, None, [])


def two_pictures(w, dream, shots):
    pics = w.dreams.picture_request(dream, None, None)
    w.dreams.save_pictures(dream, pics, KeyframeResult([kf(shots[0], "s3://b/p1.png"), kf(shots[1], "s3://b/p2.png", 2)], 10))
    return pics


def test_pictures_then_clips_then_finish_are_recorded_and_linked(w):
    db = w.db
    dream, (s1, s2) = seed_short(db)
    pics = w.dreams.picture_request(dream, None, None)
    assert len(pics.jobs) == 2 and pics.video_id == str(dream)
    assert count(db, "SELECT count(*) FROM routing_decision WHERE dream_short_id = :d AND capability = 'image.dream' "
                     "AND chosen_provider_id = 'modal_zimage'", d=dream) == 1
    w.dreams.save_pictures(dream, pics, KeyframeResult([kf(s1, "s3://b/p1.png"), kf(s2, "s3://b/p2.png", 2)], 10))
    assert count(db, "SELECT count(*) FROM asset a JOIN provenance p ON p.id = a.provenance_id WHERE a.dream_short_id = :d "
                     "AND p.licence_id IS NOT NULL", d=dream) == 2
    assert count(db, "SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND provider_id = 'modal_l40s'", d=dream) == 1
    assert w.dreams.picture_request(dream, None, None).jobs == [], "pictures are not made twice"

    clips = w.dreams.clip_request(dream, None)
    assert len(clips.jobs) == 2 and clips.jobs[0].num_frames == 57 and clips.jobs[0].width == 480 and clips.containers == 1
    assert clips.hold_seconds == 0, "the owner reviews: no warm hold"
    db.execute("UPDATE dream_short SET producer_decides = true WHERE id = %(d)s", {"d": dream})
    assert w.dreams.hold_seconds(dream) == 90, "the AI Producer runs it: the GPU stays warm for its redo"
    db.execute("UPDATE dream_short SET producer_decides = false WHERE id = %(d)s", {"d": dream})
    w.dreams.save_clips(dream, clips, ClipResult([ClipOutput(str(s1), 1, "dream", "s3://b/c1.mp4", 20, 57, 16, "fast", None, 0),
                                                  ClipOutput(str(s2), 1, "dream", "s3://b/c2.mp4", 20, 57, 16, "fast", None, 0)], 1, False))
    assert count(db, "SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND provider_id = 'modal_h100'", d=dream) == 3

    fin = w.dreams.finish_request(dream)
    assert [c.uri for c in fin.clips] == ["s3://b/c1.mp4", "s3://b/c2.mp4"] and fin.clips[0].fps == 16.0
    w.dreams.save_finish(dream, DreamFinishResult("s3://b/final.mp4", 6.5, 1080, 1920, 24, "rife-laptop", None, None,
                                                  {"motion": 1.8, "colour": 26.0}))
    assert db.rows("SELECT final_asset_id FROM dream_short WHERE id = %(d)s", {"d": dream})[0]["final_asset_id"] is not None
    assert count(db, "SELECT count(*) FROM dream_metric WHERE dream_short_id = :d AND (metrics->>'colour')::float = 26", d=dream) == 1
    chk = w.dreams.check_request(dream)
    assert "Shot 1: scene" in chk.brief and chk.video_uri == "s3://b/final.mp4"
    said = w.dreams.save_check(dream, DreamCheckResult({"noPeople": 3}, 6, [CheckIssue(3, 7.5, "a woman walks into frame", "redo clip 3")],
                                                       "one person appears", "gemini-3.6-flash", Usage(5000, 200, 0, 5200), None), "FINISH")
    assert said == "Quality check: 6/10, 1 issue - a woman walks into frame."
    assert count(db, "SELECT count(*) FROM dream_short WHERE id = :d AND (check_json->>'overall')::int = 6", d=dream) == 1
    assert w.dreams.save_check(dream, DreamCheckResult({}, 0, [], "", None, None, "no network"), "FINISH") == "Quality check unavailable this time."
    assert len(w.dreams.picture_check_request(dream).picture_uris) == 2, "both pictures go to the picture check"
    assert w.dreams.save_check(dream, DreamCheckResult({"noPeople": 10}, 9, [], "fine", "gemini-3.6-flash", None, None),
                               "PICTURES").startswith("Picture check: 9/10")
    assert count(db, "SELECT count(*) FROM dream_short WHERE id = :d AND (picture_check_json->>'overall')::int = 9", d=dream) == 1
    on = w.dreams.finish_request(dream)
    assert on.music_edit and on.ambience and on.pacing, "the editing rules are on by default"
    assert on.clips[0].scene == "scene"
    db.execute("UPDATE dream_short SET edit_ambience = false WHERE id = %(d)s", {"d": dream})
    assert not w.dreams.finish_request(dream).ambience and w.dreams.finish_request(dream).pacing
    assert count(db, "SELECT count(*) FROM routing_decision WHERE dream_short_id = :d", d=dream) == 4  # + the quality check


def test_a_new_picture_makes_the_old_clip_and_final_stale(w):
    dream, (s1, s2) = seed_short(w.db)
    two_pictures(w, dream, (s1, s2))
    clips = w.dreams.clip_request(dream, None)
    w.dreams.save_clips(dream, clips, ClipResult([ClipOutput(str(s1), 1, "dream", "s3://b/c1.mp4", 20, 57, 16, "fast", None, 0)], 1, False))
    redo = w.dreams.picture_request(dream, [str(s1)], "more pink")
    assert len(redo.jobs) == 1 and redo.jobs[0].prompt.endswith("Owner's notes: more pink") and redo.jobs[0].attempt == 2
    w.dreams.save_pictures(dream, redo, KeyframeResult([kf(s1, "s3://b/p1b.png", 3)], 5))
    assert w.db.rows("SELECT clip_asset_id FROM dream_shot WHERE id = %(s)s", {"s": s1})[0]["clip_asset_id"] is None
    with pytest.raises(RuntimeError):
        w.dreams.finish_request(dream)  # a shot without a clip can't be finished


def test_music_is_routed_costed_against_gemini_and_used_by_the_finish(w):
    db = w.db
    dream, _ = seed_short(db)
    req = w.dreams.music_request(dream, "more strings")
    assert req.max_segments == 1 and "no vocals" in req.prompt and req.prompt.endswith("Owner's notes: more strings")
    assert req.target_sec == pytest.approx(7.625, abs=0.01)  # two 3.5625 s clips - one 0.5 s dissolve + 1 s tail
    assert count(db, "SELECT count(*) FROM routing_decision WHERE dream_short_id = :d AND capability = 'music.dream' "
                     "AND chosen_provider_id = 'gemini_lyria'", d=dream) == 1
    w.dreams.save_music(dream, MusicResult("s3://b/score.wav", 7, 80, [], [], 1, "lyria-3.5", []))
    assert count(db, "SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND provider_id = 'gemini_api' AND unit_type = 'song'", d=dream) == 1
    db.execute("UPDATE dream_shot SET clip_asset_id = (SELECT music_asset_id FROM dream_short WHERE id = %(d)s) WHERE dream_short_id = %(d)s", {"d": dream})
    assert w.dreams.finish_request(dream).music_uri == "s3://b/score.wav"


def test_the_owners_track_is_the_free_default_with_provenance_and_fresh_pieces(w):
    db = w.db
    db.execute("DELETE FROM provenance WHERE provider = 'owner_music_bed'")
    dream, _ = seed_short(db)
    assert w.dreams.music_source(dream) == "bed"
    req = w.dreams.music_bed_request(dream)
    assert req.bed_uri == "s3://projectv/music-beds/my track.m4a" and req.target_sec == pytest.approx(7.625, abs=0.01)
    assert req.seed == f"{dream}#0" and req.model == "gemini-3.6-flash" and req.candidates == 6
    assert "a pink pier" in req.brief and "Shots, in order: scene | scene" in req.brief
    said = w.dreams.save_music_bed(dream, DreamBedResult("s3://b/piece.wav", 8.1, 754.25, 3600, "my track.m4a", "gemini-3.6-flash",
                                                         Usage(3000, 60, 0, 3060), "soft and floating", 6))
    assert "12:34" in said and "best of 6 pieces by ear: soft and floating" in said
    assert count(db, "SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND provider_id = 'local_laptop' AND amount_inr = 0", d=dream) == 1
    assert count(db, "SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND provider_id = 'gemini_api' AND amount_inr > 0 "
                     "AND unit_type = 'token'", d=dream) == 1
    assert count(db, "SELECT count(*) FROM routing_decision WHERE dream_short_id = :d AND stage = 'DREAM_MUSIC_PICK'", d=dream) == 1
    assert count(db, "SELECT count(*) FROM routing_decision WHERE dream_short_id = :d AND capability = 'music.dream' "
                     "AND chosen_provider_id = 'local_laptop'", d=dream) == 1
    assert count(db, "SELECT count(*) FROM dream_short d JOIN asset a ON a.id = d.music_asset_id JOIN provenance p ON p.id = a.provenance_id "
                     "WHERE d.id = :d AND p.provider = 'owner_music_bed' AND p.model = 'my track.m4a' AND p.seed = 754250 AND NOT p.ai_generated "
                     "AND p.licence_id = '00000000-0000-0000-0000-00000000a012' "
                     "AND p.prompt LIKE 'piece from 12:34 to 12:42 of s3://projectv/music-beds/my track.m4a'", d=dream) == 1
    nxt = w.dreams.music_bed_request(dream)
    assert nxt.seed == f"{dream}#1", "New music asks for another piece"
    assert 754.25 in nxt.used_starts_sec, "pieces other Shorts used are avoided"
    db.execute("UPDATE dream_shot SET clip_asset_id = (SELECT music_asset_id FROM dream_short WHERE id = %(d)s) WHERE dream_short_id = %(d)s", {"d": dream})
    assert w.dreams.finish_request(dream).music_name == "your track (my track.m4a)"
    db.execute("UPDATE dream_short SET music_source = 'lyria' WHERE id = %(d)s", {"d": dream})
    assert w.dreams.music_source(dream) == "lyria"


def test_the_new_looks_have_their_own_shot_length_and_joins(w):
    assert looks.LOOK_ORDER[0] == "lonely"
    lonely, walk = looks.look("lonely"), looks.look("walkthrough")
    assert lonely.dissolve_sec == 0, "hard cuts"
    assert w.dreams.short_seconds(5, lonely) == pytest.approx(5 * 57 / 16.0 / 0.7 + 1, abs=0.01)  # slowed to 70% on the laptop
    assert w.dreams.short_seconds(4, walk) == pytest.approx(4 * 81 / 16.0 / 0.75 - 3 * 0.35 + 1, abs=0.01)
    assert "no drone" in lonely.motion_style and "no small text" in lonely.picture_style
    assert "nobody is ever visible" in lonely.motion_style and "a person " not in lonely.motion_style
    assert lonely.handheld == "stand" and "DIFFERENT place" in lonely.structure % 5
    assert looks.look("nope") == lonely, "unknown looks fall back to the default"
    drive = looks.look("drive")
    assert drive.dissolve_sec == 0 and "not a 3D render" in drive.picture_style and "not a 3D render" in lonely.picture_style


def test_real_gpu_timings_replace_the_config_guess_and_costs_are_checked_against_the_estimate(w):
    db = w.db
    dream, (s1, _) = seed_short(db)
    ev = w.dreams.evidence
    kind = "wan.fast.720p"
    ev.gpu_timing(dream, kind, 57, 1, True)  # too few warm clips yet: nothing changes
    assert ev.recalibrate(kind) == []
    db.execute("DELETE FROM gpu_timing WHERE kind = %(k)s", {"k": kind})
    ev.gpu_timing(dream, kind, 57, 70, False)
    ev.gpu_timing(dream, kind, 57, 84, False)
    ev.gpu_timing(dream, kind, 57, 140, True)
    assert len(ev.recalibrate(kind)) == 2
    per81 = (70 + 84) / 2.0 * 81 / 57
    assert ev.calibrated(kind + ".per81") == pytest.approx(per81, abs=0.01)
    assert ev.calibrated(kind + ".first_extra") == pytest.approx(140 - 77, abs=0.01)  # the first clip took 63 s longer
    db.execute("UPDATE dream_short SET quality = '720p' WHERE id = %(d)s", {"d": dream})
    pics = w.dreams.picture_request(dream, None, None)
    w.dreams.save_pictures(dream, pics, KeyframeResult([kf(s1, "s3://b/p1.png")], 5))
    minutes = w.dreams.clip_estimate(dream, None).minutes
    assert minutes == pytest.approx((per81 * 57 / 81 + ev.calibrated(kind + ".first_extra") + 140) / 60, abs=0.01)
    ev.cost_check(dream, "CLIPS", 25, 47)
    w.dreams.status(dream, "CLIPS", "FAILED_WAITING", "GPU cap reached")
    kinds = [e["kind"] for e in ev.events(dream)]
    assert "cost" in kinds and "failure" in kinds
    assert any('"ratio": 1.88' in str(e["data"]) or '"ratio":1.88' in str(e["data"]) for e in ev.events(dream))


def test_the_writer_follows_the_picked_angle_and_avoids_what_exists(w):
    dream, _ = seed_short(w.db)
    j = json.dumps({"source": "youtube", "videos": [{"url": "u", "shows": "a neon kiosk in a field at dusk"}],
                    "brief": {"cliches": ["neon kiosk at dusk"], "gaps": ["rain at dawn"],
                              "angles": [{"title": "One", "idea": "first", "why": "a"},
                                         {"title": "Dawn rain", "idea": "a phone box at dawn in rain", "why": "b"}]}})
    w.db.execute("UPDATE dream_short SET scout_angle = 2, scout_json = CAST(%(j)s AS jsonb) WHERE id = %(d)s", {"j": j, "d": dream})
    for_writer = w.dreams.scouted(w.dreams.dream(dream)).for_writer()
    assert "build EVERY shot around it" in for_writer and "Dawn rain - a phone box at dawn in rain" in for_writer
    assert "Avoid these clichés: neon kiosk at dusk" in for_writer and "Do not copy" in for_writer
    assert w.dreams.scout_wanted(dream), "the Scout is on by default"
    assert w.dreams.scouted(w.dreams.dream(dream)).chosen().title == "Dawn rain", "the picked angle replaces the look's structure"


def test_a_review_note_becomes_a_lesson_that_goes_into_the_prompts_only_once_approved(w):
    db = w.db
    dream, (s1, _) = seed_short(db)
    lessons = DreamLessons(db)
    w.dreams.lessons_ = lessons
    lessons.review(dream, s1, 2, "a man appeared filming with a phone")
    lessons.review(dream, None, 4, "  ")
    assert count(db, "SELECT count(*) FROM dream_review WHERE dream_short_id = :d", d=dream) == 2
    mine = [lesson for lesson in lessons.lessons() if lesson["dream_short_id"] == dream]
    assert len(mine) == 1 and mine[0]["target"] == "WRITER", "only a note becomes a lesson"
    assert "a man appeared" not in lessons.for_prompt("WRITER"), "proposed lessons stay out of the prompts"
    lessons.update(mine[0]["id"], "never describe anyone filming", None, "ACTIVE")
    assert "never describe anyone filming" in lessons.for_prompt("WRITER")
    assert "never describe anyone filming" not in lessons.for_prompt("PICTURE"), "writer rules stay with the writer"
    lessons.update(mine[0]["id"], None, None, "RETIRED")
    assert "never describe anyone filming" not in lessons.for_prompt("WRITER")
    with pytest.raises(ValueError):
        lessons.review(dream, None, 6, "x")


def test_an_idea_with_no_similar_shorts_carries_on_without_angles_instead_of_stopping(w):
    dream, _ = seed_short(w.db)
    req = DreamScoutRequest(str(dream), "idea", "gemini", 6, "lonely")
    said = w.dreams.save_scout(dream, req, DreamScoutResult([], [], "youtube", 2, "no Shorts found for this idea"))
    assert "no similar Shorts" in said and "no angle" in said
    assert count(w.db, "SELECT count(*) FROM dream_short WHERE id = :d AND scout_json IS NOT NULL", d=dream) == 0


def test_a_batch_with_no_clips_fails_so_the_step_waits_for_the_owner(w):
    dream, _ = seed_short(w.db)
    with pytest.raises(RuntimeError):
        w.dreams.clip_request(dream, None)


def test_estimate_asks_when_over_the_auto_approve_amount(w):
    dream, (s1, _) = seed_short(w.db)
    w.dreams.save_pictures(dream, w.dreams.picture_request(dream, None, None), KeyframeResult([kf(s1, "s3://b/p1.png")], 5))
    est = w.dreams.clip_estimate(dream, None)
    assert est.needs_approval and est.inr > 0 and est.summary.startswith("1 clips on Modal Wan")
    w.db.execute("UPDATE dream_short SET quality = '720p' WHERE id = %(d)s", {"d": dream})
    # measured 19 Nov: ~133 s per 81 frames at 720p, +64 s warm-up for the first clip, +140 s container start
    assert w.dreams.clip_estimate(dream, None).minutes == pytest.approx((133.0 * 57 / 81 + 64 + 140) / 60, abs=0.01)


def test_producer_reviews_are_kept_with_the_short_and_autopilot_shorts_are_held_to_the_daily_cap(w):
    db = w.db
    dream, (_, s2) = seed_short(db)
    db.execute("DELETE FROM autopilot_run")
    db.execute("UPDATE producer_decision SET owner_decision = NULL WHERE false")
    db.execute("UPDATE dream_short SET producer_decides = true WHERE id = %(d)s", {"d": dream})
    assert w.producer.decides(str(dream))
    advice = w.producer.save_review(str(dream), "PICTURES", 1, ProducerReviewResult(
        "redo", 0.8, "shot 2 melts", "keep the pier straight",
        [ProducerItemVerdict("shot-1", 8, True, "", ""), ProducerItemVerdict("shot-2", 4, False, "melted rail", "straight rail")],
        "gemini-3.6-flash", Usage(1000, 200, 0, 1200)))
    assert advice.weak_items == ["shot-2"]
    assert w.producer.shot_ids(str(dream), advice.weak_items) == [str(s2)]
    assert count(db, "SELECT count(*) FROM producer_decision WHERE dream_short_id = :d AND stage = 'DREAM_PICTURES' AND video_id IS NULL", d=dream) == 1
    assert count(db, "SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND note LIKE 'PRODUCER review%%'", d=dream) == 1
    w.producer.record_owner_decision(dream, "PICTURES", "reject", "yes, fix the rail")
    assert count(db, "SELECT count(*) FROM producer_decision WHERE dream_short_id = :d AND agreed", d=dream) == 1

    # not an autopilot Short: the Producer's Rs 100 per Short; an autopilot Short: also the daily cap (Rs 30 here)
    assert w.producer.limit(dream) == Decimal(100)
    db.execute("UPDATE autopilot_setting SET daily_cap_inr = 30")
    db.execute("INSERT INTO autopilot_run (id, run_date, status, dream_short_id) VALUES (%(id)s, DATE '2026-01-01', 'RUNNING', %(d)s)",
               {"id": uuid.uuid4(), "d": dream})
    assert w.producer.limit(dream) == Decimal(30)
    assert w.producer.spend_decision(str(dream), Decimal(25)).approve
    assert not w.producer.spend_decision(str(dream), Decimal(35)).approve, "over the daily cap: asks"
    assert count(db, "SELECT count(*) FROM producer_decision WHERE dream_short_id = :d AND acted = 'approved_spend'", d=dream) == 1


def test_the_same_clip_issue_twice_gets_one_prompt_fix_on_the_clips_then_the_owner_is_asked(w):
    db = w.db
    dream, (s1, s2) = seed_short(db)
    two_pictures(w, dream, (s1, s2))

    def people(problem):
        return ProducerReviewResult("redo", 0.8, "shot 1 has people", "remove them",
                                    [ProducerItemVerdict("shot-1", 4, False, problem, "empty pier")], "gemini-3.6-flash", None)

    assert w.producer.save_review(str(dream), "CLIPS", 1, people("visible human figures")).verdict == "redo"
    assert w.dreams.clip_jobs(dream, None)[0].negative is None, "first redo: an ordinary re-roll"
    fix = w.producer.save_review(str(dream), "CLIPS", 2, people("a man stands on the pier"))
    assert fix.verdict == "redo" and fix.redo_notes.startswith("RECURRING ISSUE (visible people)")
    job = w.dreams.clip_jobs(dream, None)[0]
    assert "human figure" in job.negative, "the redo clip carries the firm negative prompt"
    assert job.prompt.startswith("walk forward ") and "empty of people" in job.prompt
    ask = w.producer.save_review(str(dream), "CLIPS", 3, people("silhouette in the fog"))
    assert ask.verdict == "ask", "the fix did not hold: ask the owner instead of re-rolling"
    assert count(db, "SELECT count(*) FROM producer_decision WHERE dream_short_id = :d AND stop_rule = 'prompt_fix' AND stop_cause = 'people'", d=dream) == 1
    assert count(db, "SELECT count(*) FROM producer_decision WHERE dream_short_id = :d AND stop_rule = 'ask' AND verdict = 'redo'", d=dream) == 1


def test_a_watched_short_is_remembered_and_offered_to_the_next_scout(w):
    dream, _ = seed_short(w.db)
    seen = ScoutVideo("https://www.youtube.com/shorts/AbCdEfGhIjK", "t", "c", 10, 20.0, "a pier", "soft", "handheld", 4, 2.0, "piano", "fog", "AI", "calm", None)
    failed = ScoutVideo("https://youtu.be/ZZZZZZZZZZZ", "t", "c", 1, 20.0, None, None, None, None, None, None, None, None, None, "TimeoutError: slow")
    w.dreams.scout.remember_watched([seen, failed], "gemini-3.6-flash")
    known = w.dreams.scout_request(dream, None).known
    assert known["AbCdEfGhIjK"].shows == "a pier"
    assert "ZZZZZZZZZZZ" not in known, "a failed watch is tried again next time"


def test_unfiltered_uses_the_owners_words_one_line_one_shot_and_no_writer(w):
    db = w.db
    dream, _ = seed_short(db, look="unfiltered", idea="a pink pier at dawn\nwalk to the end\n\nthe door opens")
    said = w.dreams.write_shots(dream, None)
    assert said.startswith("Unfiltered: 3 shots") and w.llm.calls == []
    rows = db.rows("SELECT scene, picture_prompt, motion_prompt FROM dream_shot WHERE dream_short_id = %(d)s ORDER BY ord", {"d": dream})
    assert [r["picture_prompt"] for r in rows] == ["a pink pier at dawn", "walk to the end", "the door opens"]
    assert all(r["scene"] == r["motion_prompt"] for r in rows)
    assert w.dreams.check_request(dream) is None and w.dreams.picture_check_request(dream) is None


# ------------------------------------------------------------------------------------------------ choose the path


def world(paths):
    return DreamWorld(title="Glass City", world="a city of twisted glass", palette="teal and amber", light="blue hour", hero="a red kiosk",
                      shots=[PlannedDreamShot(scene="s1", motion="m1"), PlannedDreamShot(scene="s2", motion="m2"),
                             PlannedDreamShot(scene="an arch, a door on the left, stairs on the right", motion="m3")], paths=paths)


def form(path_choice):
    return NewDream("a glass city", "lonely", 3, "480p", "REVIEW", None, 10, "bed", False, True, True, True, None, None, None, None, True,
                    None, None, None, None, path_choice)


def write(w, dream, paths):
    w.llm.calls.clear()
    w.llm.answers["dream.write"] = world(paths)
    w.dreams.write_shots(dream, None)
    purpose, system, user, tp, _ = w.llm.calls[0]
    assert purpose == "dream.write" and tp is DreamWorld
    return system, user


def row(w, dream):
    return w.dreams.dream(dream)


def finish(w, dream):
    for s in w.db.rows("SELECT id FROM dream_shot WHERE dream_short_id = %(d)s ORDER BY ord", {"d": dream}):
        w.db.execute("UPDATE dream_shot SET picture_asset_id = %(p)s, clip_asset_id = %(c)s WHERE id = %(s)s",
                     {"p": asset(w.db, f"s3://b/p-{s['id']}.png"), "c": asset(w.db, f"s3://b/c-{s['id']}.mp4"), "s": s["id"]})
    w.db.execute("UPDATE dream_short SET final_asset_id = %(a)s, status = 'COMPLETED' WHERE id = %(d)s",
                 {"a": asset(w.db, f"s3://b/final-{dream}.mp4"), "d": dream})
    w.dreams.save_branch(dream)


def test_choose_the_path_is_off_by_default_and_nothing_changes(w):
    dream = w.studio.create(form(False))
    assert row(w, dream)["path_choice"] is False and row(w, dream)["episode"] == 1
    assert '"paths"' not in write(w, dream, ["the door", "the stairs"])[0], "the writer is not asked"
    assert row(w, dream)["path_a"] is None, "options the Short did not ask for are never stored"
    finish(w, dream)
    assert w.dreams.finish_request(dream).path_a is None, "no end card"
    assert row(w, dream)["branch_json"] is None
    with pytest.raises(RuntimeError):
        w.studio.continue_dream(dream, PathPick.owner("A"))


def test_on_the_writer_writes_two_paths_the_owner_can_edit_and_the_finish_burns_them(w):
    dream = w.studio.create(form(True))
    assert '"paths"' in write(w, dream, ['"the door"', "the stairs"])[0]
    assert row(w, dream)["path_a"] == "the door" and row(w, dream)["path_b"] == "the stairs"
    w.studio.set_paths(dream, "the red door", "the long stairs")  # the owner's edit in review/manual
    write(w, dream, ["x", "y"])
    assert row(w, dream)["path_a"] == "the red door", "a rewrite never replaces the owner's options"
    with pytest.raises(ValueError):
        w.dreams.set_paths(dream, "ok", "  ")
    finish(w, dream)
    fin = w.dreams.finish_request(dream)
    assert fin.path_a == "the red door" and fin.path_b == "the long stairs"
    assert fin.keep_order, "the card asks about the LAST shot, so pacing never reshuffles it"


def test_continue_starts_episode_two_from_the_parents_last_frame_in_the_same_world(w):
    db = w.db
    parent = w.studio.create(form(True))
    write(w, parent, ["the door", "the stairs"])
    with pytest.raises(RuntimeError):
        w.studio.continue_dream(parent, PathPick.owner("B"))  # not finished yet
    finish(w, parent)
    last_clip = db.rows("SELECT clip_asset_id FROM dream_shot WHERE dream_short_id = %(d)s ORDER BY ord DESC LIMIT 1", {"d": parent})[0]["clip_asset_id"]
    branch = json.dumps(row(w, parent)["branch_json"])
    assert "a city of twisted glass" in branch and str(last_clip) in branch and "the stairs" in branch

    child = w.studio.continue_dream(parent, PathPick.owner("B"))
    c = row(w, child)
    assert c["parent_id"] == parent and c["episode"] == 2 and c["chosen_path"] == "the stairs" and c["pick_source"] == "owner"
    assert c["start_clip_asset_id"] == last_clip
    assert scene_seed(parent, row(w, parent)) == int(c["scene_seed"]), "same world seed"
    assert c["path_choice"] is True, "the series keeps going"
    assert c["scout"] is False and c["chain_shots"] is True, "the parent's settings are inherited"

    user = write(w, child, ["the roof", "the well"])[1]
    assert "EPISODE 2" in user and "a city of twisted glass" in user and "THE STAIRS" in user
    assert "stairs on the right" in user, "the writer knows where the last episode ended"

    # shot 1 has no picture: its clip starts from the last frame of the parent's last clip
    jobs = w.dreams.clip_jobs(child, None)
    assert [j.shot_number for j in jobs] == [1, 2, 3], "no pictures needed: shot 1 from the parent, the rest from the clip before"
    assert jobs[0].image_uri is None
    last_shot = db.rows("SELECT id FROM dream_shot WHERE clip_asset_id = %(a)s", {"a": last_clip})[0]["id"]
    assert jobs[0].chain_from_uri == f"s3://b/c-{last_shot}.mp4"

    finish(w, child)
    third = w.studio.continue_dream(child, PathPick("A", COMMENTS, {"A": 9, "B": 4}))
    assert row(w, third)["pick_source"] == "comments", "the comment vote plugs in as just another picker"
    chain = w.studio.chain(third)
    assert [r["episode"] for r in chain] == [1, 2, 3] and [r["id"] for r in chain] == [parent, child, third]


def test_studio_starts_the_workflow_and_signals_the_owners_decisions(w):
    dream = w.studio.create(NewDream("a pink pier", "lonely", 3, "720p", "REVIEW", {"clips": "MANUAL"}, 15))
    wf, wf_id, arg = w.gateway.started[0]
    assert wf == "DreamShortWorkflowPy" and wf_id == f"dream-{dream}" and arg["dreamId"] == str(dream) and arg["modes"] == {"CLIPS": "MANUAL"}
    r = row(w, dream)
    assert r["quality"] == "720p" and r["review_minutes"] == 15 and r["shot_count"] == 3 and r["scout"] is True
    assert r["picture_model"] == "flux2-klein" and json.dumps(r["modes_json"]).count("REVIEW") == 5
    w.studio.approve(dream, "WRITE")
    w.studio.redo(dream, "PICTURES", ["x"], "more pink")
    assert [s[1] for s in w.gateway.signals] == ["approve", "redo"]
    w.gateway.gone.add(f"dream-{dream}")
    w.studio.cancel(dream)  # the workflow already ended: not an error, the Short is marked cancelled
    assert row(w, dream)["status"] == "CANCELLED"
    with pytest.raises(ValueError):
        w.studio.create(NewDream("  "))
    with pytest.raises(ValueError):
        w.studio.create(NewDream("x", idea_pack="impossible-architecture", idea_id="nope"))
    assert issubclass(WorkflowNotFoundError, LookupError)
    seeded = w.studio.create(NewDream(None, "lonely", idea_pack="impossible-architecture", idea_id="looping-bridge", hook_title=True))
    s = row(w, seeded)
    assert s["look"] == "architecture" and s["idea_ref"] == "impossible-architecture/looping-bridge" and s["scout"] is False
    assert s["hook_text"] and s["hook_locked"] is True
    det = w.studio.detail(dream)
    assert det["view"] is None and "world_json" not in det and len(det["shots"]) == 0 and w.studio.list()[0]["id"] in (dream, seeded)
