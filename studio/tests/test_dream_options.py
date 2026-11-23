"""The Dream Shorts options (hook title, chained shots, first-last frame, locked scene, Unfiltered, idea pack, SVI scenes) against a scratch
Postgres with a fake writer: all OFF unless asked for, and switching them on changes only what it should. Port of DreamHookChainIdeasTest.
No GPU, no Gemini, no spend."""

import uuid

import pytest

from studio.dream import looks, svi
from studio.dream.dream_studio import NewDream
from studio.dream.llm_types import DreamWorld, PlannedDreamShot, PlannedScene
from studio.dream.service import continues
from studio.media.types import ClipOutput, ClipRequest, ClipResult, KeyframeJob, KeyframeOutput, KeyframeRequest, KeyframeResult
from studio.tests.dworld import asset, make_world, seed_short


@pytest.fixture
def w(scratch_db):
    return make_world(scratch_db, music_bed="s3://projectv/music-beds/x.m4a")


@pytest.fixture
def short(w):
    dream, (s1, s2) = seed_short(w.db, shots=2)
    s3 = uuid.uuid4()
    w.db.execute("INSERT INTO dream_shot (id, dream_short_id, ord, scene, picture_prompt, motion_prompt) VALUES (%(id)s, %(d)s, 2, 'scene', "
                 "'picture prompt', 'walk forward')", {"id": s3, "d": dream})
    return dream, [s1, s2, s3]


def media(w, shot, picture, clip=None):
    w.db.execute("UPDATE dream_shot SET picture_asset_id = %(a)s WHERE id = %(s)s", {"a": asset(w.db, picture), "s": shot})
    if clip is not None:
        w.db.execute("UPDATE dream_shot SET clip_asset_id = %(a)s WHERE id = %(s)s", {"a": asset(w.db, clip), "s": shot})


def text(w, sql, dream):
    rows = w.db.rows(sql.replace(":d", "%(d)s"), {"d": dream})
    return list(rows[0].values())[0] if rows else None


def world(hook, **kw):
    return DreamWorld(title="T", world="a city of twisted glass", palette="p", light="l", hero="",
                      shots=[PlannedDreamShot(scene="s1", motion="m1"), PlannedDreamShot(scene="s2", motion="m2"),
                             PlannedDreamShot(scene="s3", motion="m3")], hook=hook, cameraPath="forward down the avenue, rising, then turning left",
                      **kw)


def write(w, dream, hook=None, answer=None):
    """The writer's prompts (system, user) for this Short, with a fake writer that answers `hook`."""
    w.llm.calls.clear()
    w.llm.answers["dream.write"] = answer or world(hook)
    if answer is None:
        w.db.execute("UPDATE dream_short SET shot_count = 3 WHERE id = %(d)s", {"d": dream})
    said = w.dreams.write_shots(dream, None)
    _, system, user, tp, _ = w.llm.calls[-1]
    assert tp is DreamWorld
    return system, user, said


def form(**kw):
    base = dict(idea="a pink pier", look="lonely", shots=4, quality="480p", default_mode="AUTO", review_minutes=10, music="bed", scout=False,
                music_edit=True, ambience=True, pacing=True)
    base.update(kw)
    return NewDream(**base)


def row(w, dream):
    return w.dreams.dream(dream)


# ------------------------------------------------------------------------------------------------ defaults


def test_everything_is_off_by_default_and_the_prompts_are_unchanged(w, short):
    dream, (s1, s2, s3) = short
    system, user, _ = write(w, dream, "IGNORED *HOOK*")
    assert '"hook"' not in system, "no hook field asked for"
    assert "ONE continuous camera move through ONE world" not in system, "the look's own structure is used"
    assert "Concept seed" not in user
    assert text(w, "SELECT hook_text FROM dream_short WHERE id = :d", dream) is None, "a hook the Short did not ask for is never stored"
    for s in w.dreams.shots(dream):
        media(w, s["id"], f"s3://b/p-{s['id']}.png")
    assert not w.dreams.chained(dream)
    assert all(j.chain_from_uri is None for j in w.dreams.clip_jobs(dream, None))
    for s in w.dreams.shots(dream):
        w.db.execute("UPDATE dream_shot SET clip_asset_id = %(a)s WHERE id = %(s)s", {"a": asset(w.db, f"s3://b/c-{s['id']}.mp4"), "s": s["id"]})
    w.db.execute("UPDATE dream_short SET hook_text = 'X *Y* Z' WHERE id = %(d)s", {"d": dream})
    assert w.dreams.finish_request(dream).hook_text is None, "hook off: nothing is burned in"


def test_plain_shorts_keep_their_writer_prompt_word_for_word(w, short):
    system, _, _ = write(w, short[0])
    assert ("\"shots\": [{\"scene\": what this shot shows (one or two sentences, concrete), \"motion\": the camera move and\n"
            "the small motion inside the scene}]} with exactly 3 shots.") in system
    assert '"scenes"' not in system


# ------------------------------------------------------------------------------------------------ hook title


def test_the_writer_is_asked_for_a_hook_and_it_is_stored_unless_the_owner_set_one(w, short):
    dream, _ = short
    w.db.execute("UPDATE dream_short SET hook_title = true WHERE id = %(d)s", {"d": dream})
    assert '"hook"' in write(w, dream, "THE STREET *FOLDED* LIKE PAPER")[0]
    assert text(w, "SELECT hook_text FROM dream_short WHERE id = :d", dream) == "THE STREET *FOLDED* LIKE PAPER"
    w.dreams.set_hook(dream, "MY *OWN* WORDS", "yellow")  # the owner edits it in the review step
    write(w, dream, "WRITER *REWRITE*")
    assert text(w, "SELECT hook_text FROM dream_short WHERE id = :d", dream) == "MY *OWN* WORDS", "a rewrite never replaces the owner's hook"
    assert text(w, "SELECT hook_color FROM dream_short WHERE id = :d", dream) == "yellow"


def test_the_finish_request_carries_the_hook_only_when_on_and_filled(w, short):
    dream, shots = short
    for s in shots:
        media(w, s, f"s3://b/p-{s}.png", f"s3://b/c-{s}.mp4")
    w.db.execute("UPDATE dream_short SET hook_title = true, hook_text = '  THESE *TOWERS* WONT  ', hook_color = 'cyan' WHERE id = %(d)s", {"d": dream})
    fin = w.dreams.finish_request(dream)
    assert fin.hook_text == "THESE *TOWERS* WONT" and fin.hook_color == "cyan"
    w.dreams.set_hook(dream, "   ", None)
    assert w.dreams.finish_request(dream).hook_text is None, "a blank hook clears it"
    assert w.dreams.finish_request(dream).hook_color == "cyan", "and keeps the colour"


# ------------------------------------------------------------------------------------------------ chained shots


def test_chained_shots_are_described_as_one_continuous_move_and_clips_run_in_order_from_the_last_frame(w, short):
    dream, (s1, s2, s3) = short
    w.db.execute("UPDATE dream_short SET chain_shots = true WHERE id = %(d)s", {"d": dream})
    assert "ONE continuous camera move through ONE world" in write(w, dream)[0]
    ids = [s["id"] for s in w.dreams.shots(dream)]
    s1, s2, s3 = ids
    media(w, s1, "s3://b/p1.png", "s3://b/c1.mp4")  # shot 1 already has a clip (an earlier batch)
    media(w, s2, "s3://b/p2.png")
    media(w, s3, "s3://b/p3.png")
    assert w.dreams.chained(dream)
    jobs = w.dreams.clip_jobs(dream, None)
    assert [j.shot_number for j in jobs] == [2, 3]
    assert jobs[0].chain_from_uri == "s3://b/c1.mp4", "shot 2 continues from shot 1's finished clip"
    assert jobs[1].chain_from_uri is None, "shot 3 continues from shot 2, made in this batch"
    w.db.execute("UPDATE dream_shot SET clip_asset_id = %(a)s WHERE id = %(s)s", {"a": asset(w.db, "s3://b/c2.mp4"), "s": s2})
    assert w.dreams.clip_jobs(dream, [str(s3)])[0].chain_from_uri == "s3://b/c2.mp4"


def test_chained_shots_after_the_first_need_no_picture_of_their_own(w, short):
    dream, (s1, _, _) = short
    media(w, s1, "s3://b/p1.png")  # only the first shot has a picture
    assert len(w.dreams.clip_jobs(dream, None)) == 1, "unchained: a shot without a picture waits for it"
    w.db.execute("UPDATE dream_short SET chain_shots = true WHERE id = %(d)s", {"d": dream})
    jobs = w.dreams.clip_jobs(dream, None)
    assert [j.shot_number for j in jobs] == [1, 2, 3] and jobs[0].image_uri == "s3://b/p1.png"
    assert jobs[1].image_uri is None, "no picture to fall back on: the worker continues from the clip before or fails"


def test_chains_of_two_pair_shots_one_with_two_and_three_with_four_and_each_pair_starts_from_a_picture(w, short):
    dream, (s1, s2, s3) = short
    assert [continues(True, 2, o) for o in range(5)] == [False, True, False, True, False]
    assert [continues(True, 0, o) for o in range(3)] == [False, True, True] and not continues(False, 2, 1)
    w.db.execute("INSERT INTO dream_shot (id, dream_short_id, ord, scene, picture_prompt, motion_prompt) VALUES (%(id)s, %(d)s, 3, 's', 'p', 'm')",
                 {"id": uuid.uuid4(), "d": dream})
    w.db.execute("UPDATE dream_short SET chain_shots = true, chain_group = 2 WHERE id = %(d)s", {"d": dream})
    media(w, s1, "s3://b/p1.png", "s3://b/c1.mp4")
    media(w, s3, "s3://b/p3.png")  # shots 2 and 4 have no picture: they continue from the clip before
    jobs = w.dreams.clip_jobs(dream, None)
    assert [j.shot_number for j in jobs] == [2, 3, 4]
    assert jobs[0].chain_from_uri == "s3://b/c1.mp4"
    assert jobs[1].chain_from_uri is None and jobs[1].image_uri == "s3://b/p3.png", "shot 3 starts a new pair, from its own picture"
    assert jobs[2].chain_from_uri is None, "shot 4 continues from shot 3, made in this batch"


def test_a_locked_scene_has_one_camera_path_one_world_and_one_seed(w, short):
    dream, _ = short
    w.db.execute("UPDATE dream_short SET chain_shots = true, chain_locked = true WHERE id = %(d)s", {"d": dream})
    assert w.dreams.locked(dream)
    system = write(w, dream)[0]
    assert "ONE locked scene" in system and '"cameraPath"' in system
    mp = [r["motion_prompt"] for r in w.db.rows("SELECT motion_prompt FROM dream_shot WHERE dream_short_id = %(d)s ORDER BY ord", {"d": dream})]
    assert all("forward down the avenue, rising, then turning left" in m and "a city of twisted glass" in m for m in mp), mp
    for s in w.dreams.shots(dream):
        media(w, s["id"], f"s3://b/p-{s['id']}.png")
    assert len({j.seed - j.take for j in w.dreams.clip_jobs(dream, None)}) == 1, "every clip of the scene shares one seed"
    w.db.execute("UPDATE dream_short SET chain_group = 2 WHERE id = %(d)s", {"d": dream})
    assert not w.dreams.locked(dream), "pairs are separate scenes: not locked"


def test_each_chained_clip_keeps_its_own_cost_entry_and_a_failed_one_is_reported_for_the_owner(w, short):
    dream, _ = short
    w.db.execute("UPDATE dream_short SET chain_shots = true WHERE id = %(d)s", {"d": dream})
    s1, s2, s3 = [s["id"] for s in w.dreams.shots(dream)]
    for s in (s1, s2, s3):
        media(w, s, f"s3://b/p-{s}.png")
    req = ClipRequest(str(dream), "a", "b", "u", w.dreams.clip_jobs(dream, None), 1, 0, None, "wan", None, None, 0, None, 0, True)
    said = w.dreams.save_clips(dream, req, ClipResult([ClipOutput(str(s1), 1, "dream", "s3://b/c1.mp4", 20, 57, 16, "fast", None, 0),
                                                       ClipOutput(str(s2), 1, "dream", None, 0, 0, 0, "fast", "pod lost", 0),
                                                       ClipOutput(str(s3), 1, "dream", None, 0, 0, 0, "fast", "shot 2 has no clip", 0)], 1, False))
    assert "chained" in said and "1/3 clips made" in said and "shots after a failed one wait" in said, said
    assert text(w, "SELECT error FROM dream_shot WHERE id = :d", s2) == "pod lost", "the failed shot waits for the owner (rule 2b)"
    assert w.db.rows("SELECT count(*) AS n FROM cost_entry WHERE dream_short_id = %(d)s AND note LIKE 'Dream Short clip, shot 1%%'",
                     {"d": dream})[0]["n"] == 1, "one cost entry per clip that used the GPU"


# ------------------------------------------------------------------------------------------------ first-last frame


def test_first_last_frame_clips_travel_from_their_picture_to_the_next_and_the_last_ends_freely(w, short):
    dream, _ = short
    w.db.execute("UPDATE dream_short SET chain_bridge = true WHERE id = %(d)s", {"d": dream})
    assert w.dreams.bridged(dream) and not w.dreams.chained(dream), "first-last is not last-frame chaining"
    assert "the exact view at the END of the shot before it" in write(w, dream)[0]
    s1, s2, s3 = [s["id"] for s in w.dreams.shots(dream)]
    for n, s in enumerate((s1, s2, s3), 1):
        media(w, s, f"s3://b/p{n}.png")
    jobs = w.dreams.clip_jobs(dream, None)
    assert [j.image_uri for j in jobs] == ["s3://b/p1.png", "s3://b/p2.png", "s3://b/p3.png"], "every shot starts from its own picture"
    assert jobs[0].end_image_uri == "s3://b/p2.png" and jobs[1].end_image_uri == "s3://b/p3.png"
    assert jobs[2].end_image_uri is None, "the last clip ends freely"
    assert all(j.chain_from_uri is None for j in jobs)
    w.db.execute("UPDATE dream_short SET chain_bridge = false WHERE id = %(d)s", {"d": dream})
    assert all(j.end_image_uri is None for j in w.dreams.clip_jobs(dream, None)), "off: as before"


def test_a_new_picture_makes_the_clip_that_ends_on_it_stale_too(w, short):
    dream, _ = short
    w.db.execute("UPDATE dream_short SET chain_bridge = true WHERE id = %(d)s", {"d": dream})
    s1, s2, s3 = [s["id"] for s in w.dreams.shots(dream)]
    for n, s in enumerate((s1, s2, s3), 1):
        media(w, s, f"s3://b/p{n}.png", f"s3://b/c{n}.mp4")
    job = KeyframeJob(str(s2), 2, "zimage", "p", 768, 1344, "9:16", "1K", None, 1)
    w.dreams.save_pictures(dream, KeyframeRequest(str(dream), None, "a", "b", [job]),
                           KeyframeResult([KeyframeOutput(str(s2), "s3://b/p2-new.png", "zimage", "m", 1, 1.0, None, None, None)], 0))

    def has_clip(s):
        return w.db.rows("SELECT clip_asset_id IS NOT NULL AS c FROM dream_shot WHERE id = %(s)s", {"s": s})[0]["c"]

    assert not has_clip(s2), "its own clip"
    assert not has_clip(s1), "shot 1 ended on the old picture"
    assert has_clip(s3), "shot 3 does not touch picture 2's end"


def test_first_last_wins_over_last_frame_when_both_are_asked(w):
    dream = w.studio.create(form(chain_shots=True, chain_group=2, chain_locked=True, chain_bridge=True))
    r = row(w, dream)
    assert r["chain_bridge"] is True and r["chain_shots"] is False and r["chain_group"] == 0 and r["chain_locked"] is False


def test_new_shorts_make_flux_pictures_by_default_and_zimage_stays_selectable(w):
    def model(d):
        return row(w, d)["picture_model"]

    assert model(w.studio.create(form())) == "flux2-klein", "owner's pick 21 Nov 2026: FLUX.2 klein is the studio default"
    assert model(w.studio.create(form(picture_model="zimage"))) == "zimage"
    assert model(w.studio.create(form(picture_model="sdxl"))) == "flux2-klein", "an unknown model falls back to the default"


# ------------------------------------------------------------------------------------------------ unfiltered


def unfiltered(prompt):
    # every option that would change the owner's words is switched ON here: Unfiltered must turn them off
    return NewDream(prompt, "unfiltered", 5, "480p", "MANUAL", None, 10, "bed", True, True, True, True, True, True, "A *BIG* HOOK", "red", False,
                    "impossible-architecture", "folding-street", None, None, True, None, "manual", 15)


def test_unfiltered_turns_off_everything_that_rewrites_or_judges_the_prompt_and_keeps_one_shot_per_line(w):
    dream = w.studio.create(unfiltered("a goalkeeper dives in the rain\n\n  a crowd of fans roaring  \nthe ball hits the net"))
    r = row(w, dream)
    assert r["look"] == "unfiltered"
    for col in ("scout", "hook_title", "path_choice", "producer_decides", "edit_music", "edit_ambience", "edit_pacing"):
        assert r[col] is False, col
    assert r["idea_ref"] is None, "no idea pack"
    assert r["shot_count"] == 3, "one shot per non-empty line"
    assert r["gpu_hold"] == "manual" and r["gpu_idle_minutes"] == 15


def test_unfiltered_uses_the_owners_words_exactly_without_calling_a_writer(w):
    dream = w.studio.create(unfiltered("a goalkeeper dives in the rain, anime style, a man is crying\nthe ball hits the net"))
    w.llm.calls.clear()
    said = w.dreams.write_shots(dream, "some notes that must not reach any writer")
    assert w.llm.calls == []
    assert "2 shots" in said and "exactly as written" in said
    rows = w.db.rows("SELECT scene, picture_prompt, motion_prompt FROM dream_shot WHERE dream_short_id = %(d)s ORDER BY ord", {"d": dream})
    assert len(rows) == 2 and rows[0]["picture_prompt"] == "a goalkeeper dives in the rain, anime style, a man is crying"
    assert rows[0]["picture_prompt"] == rows[0]["motion_prompt"], "the same words drive the picture and the motion"
    assert rows[1]["picture_prompt"] == "the ball hits the net"
    assert text(w, "SELECT title FROM dream_short WHERE id = :d", dream) == "a goalkeeper dives in the rain,…"
    media(w, w.dreams.shots(dream)[0]["id"], "s3://b/p1.png")
    jobs = w.dreams.clip_jobs(dream, None)
    assert len(jobs) == 1 and jobs[0].prompt == "a goalkeeper dives in the rain, anime style, a man is crying", "no look style, camera or 'no people' added"
    assert jobs[0].negative is None, "no negative prompt added"
    lk = looks.look("unfiltered")
    assert lk.dissolve_sec == 0 and lk.speed == 1.0 and looks.frames(lk.shot_seconds) == 81 and lk.picture_style == ""


def test_unfiltered_skips_the_quality_checks_that_judge_no_people_and_feel_good(w):
    dream = w.studio.create(unfiltered("a goalkeeper dives in the rain"))
    w.dreams.write_shots(dream, None)
    media(w, w.dreams.shots(dream)[0]["id"], "s3://b/p1.png")
    assert w.dreams.picture_check_request(dream) is None, "no picture check"
    assert w.dreams.check_request(dream) is None, "no quality check"


# ------------------------------------------------------------------------------------------------ idea pack


def test_an_idea_from_the_pack_seeds_the_short_and_skips_the_scout(w):
    dream = w.studio.create(NewDream("", "lonely", 5, "480p", "AUTO", None, 10, "bed", True, True, True, True, None, True, None, "yellow", True,
                                     "impossible-architecture", "folding-street"))
    r = row(w, dream)
    assert r["look"] == "architecture", "the pack decides the look"
    assert r["scout"] is False, "no researching: the idea comes from the pack"
    assert r["idea_ref"] == "impossible-architecture/folding-street" and "folds upward like a page" in r["idea"]
    assert r["hook_title"] is True and r["hook_text"] == "THE STREET *FOLDED* LIKE PAPER", "the idea's own hook, locked so the writer keeps it"
    assert r["hook_locked"] is True and r["hook_color"] == "yellow" and r["chain_shots"] is True
    system, user, _ = write(w, dream, "WRITER *HOOK*")
    assert "glides slowly and continuously" in system, "the architecture look's own camera rule"
    assert "Concept seed from the studio's idea pack" in user and "Steady walk toward the fold" in user
    assert text(w, "SELECT hook_text FROM dream_short WHERE id = :d", dream) == "THE STREET *FOLDED* LIKE PAPER"


def test_the_old_form_still_creates_a_short_with_all_three_options_off(w):
    dream = w.studio.create(NewDream("a pink pier", "lonely", 5, "480p", "AUTO", None, 10, "bed", True, True, True, True))
    r = row(w, dream)
    assert r["hook_title"] is False and r["chain_shots"] is False and r["idea_ref"] is None and r["scout"] is True and r["look"] == "lonely"
    with pytest.raises(ValueError):
        w.studio.create(NewDream("x", "lonely", 5, "480p", "AUTO", None, 10, "bed", True, True, True, True, None, None, None, None, None,
                                 "impossible-architecture", "nope"))


# ------------------------------------------------------------------------------------------------ SVI scenes


def test_svi_scenes_are_written_as_scenes_of_beats_with_one_shared_style(w, short):
    dream, _ = short
    w.db.execute("UPDATE dream_short SET svi_scenes = 2, svi_scene_seconds = 10, look = 'architecture' WHERE id = %(d)s", {"d": dream})
    answer = DreamWorld(title="T", world="w", palette="p", light="l", hero="", style="35 mm, soft teal haze",
                        scenes=[PlannedScene(scene="a folding street", beats=["glide forward", "tilt up", "keep rising"]),
                                PlannedScene(scene="a stair to the sky", beats=["climb"])])
    system, _, said = write(w, dream, answer=answer)
    assert "Invent 2 scenes" in system and '"style": ONE line' in system
    assert "with exactly 2 scenes" in system and "shots." not in system, "no shot count: the scenes set the shape"
    assert "2 SVI scenes, 6 beats" in said
    rows = w.dreams.shots(dream)
    assert [svi.scene_of(r) for r in rows] == [1, 1, 1, 2, 2, 2]
    assert all("Style: 35 mm, soft teal haze." in r["motion_prompt"] for r in rows), "one style line for every beat"
    assert rows[0]["picture_prompt"].startswith("a folding street") and "Style: 35 mm, soft teal haze." in rows[0]["picture_prompt"]
    assert rows[1]["motion_prompt"].startswith("tilt up."), "each beat has its own motion"
    assert rows[5]["motion_prompt"].split(".")[0] == "climb", "a missing beat repeats the last motion"
    assert w.dreams.dream(dream)["shot_count"] == 6


def test_svi_on_the_form_wins_over_chaining_and_sets_the_shape(w):
    dream = w.studio.create(NewDream("a pink pier", "lonely", 4, "480p", "AUTO", None, 10, "bed", False, True, True, True, None, None, None, None,
                                     True, None, None, 2, True, None, True, None, None, 3, 15, "dissolve"))
    r = row(w, dream)
    assert r["svi_scenes"] == 3 and r["svi_scene_seconds"] == 15 and r["svi_between"] == "dissolve"
    assert r["shot_count"] == 12, "3 scenes x 4 beats"
    assert r["chain_shots"] is False and r["chain_bridge"] is False
    off = w.studio.create(NewDream("a pink pier", "lonely", 4, "480p", "AUTO", None, 10, "bed", False, True, True, True, None))
    assert row(w, off)["svi_scenes"] == 0, "off by default"


def test_svi_clip_jobs_rerender_from_the_asked_beat_to_the_scenes_end_and_finish_gets_the_beats_per_scene(w, short):
    dream, _ = short
    w.db.execute("UPDATE dream_short SET svi_scenes = 1, svi_scene_seconds = 10 WHERE id = %(d)s", {"d": dream})
    w.db.execute("UPDATE dream_shot SET scene_no = 1, beat_no = ord + 1 WHERE dream_short_id = %(d)s", {"d": dream})
    s1, s2, s3 = [s["id"] for s in w.dreams.shots(dream)]
    media(w, s1, "s3://b/p1.png", "s3://b/c1.mp4")
    media(w, s2, "s3://b/unused.png", "s3://b/c2.mp4")
    w.db.execute("UPDATE dream_shot SET clip_asset_id = NULL WHERE id = %(s)s", {"s": s3})
    jobs = w.dreams.clip_jobs(dream, None)
    assert [(j.scene, j.beat) for j in jobs] == [(1, 3)] and jobs[0].chain_from_uri == "s3://b/c2.mp4", "a retry continues after the last kept beat"
    assert jobs[0].image_uri == "s3://b/p1.png" and jobs[0].num_frames == 81 and (jobs[0].width, jobs[0].height) == (480, 832)
    redo = w.dreams.clip_jobs(dream, [str(s2)])
    assert [j.beat for j in redo] == [2, 3], "the later beats grew from it, so they are redone too; earlier beats are kept"
    for s in (s3,):
        media(w, s, "s3://b/p3.png", "s3://b/c3.mp4")
    fin = w.dreams.finish_request(dream)
    assert fin.svi_scenes == [3] and fin.svi_scene_frames == [min(svi.frames_of(3), 10 * 16 + 1)] and fin.keep_order is True
