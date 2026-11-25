"""Dream Shorts pure rules: choose-the-path helpers, idea packs, timeline, SVI scenes, rental estimate. Ports of DreamBranchesTest,
DreamIdeaPacksTest, DreamTimelineTest, DreamSviTest and RentalEstimateTest."""

import re
from datetime import UTC, datetime, timedelta

import pytest

from studio.dream import branches, idea_packs, looks, svi, timeline
from studio.dream.branches import PathPick
from studio.dream.settings import RentalEstimate, clean_picture_model
from studio.dream.timeline import Cost, Event
from studio.dream.wire_types import PlannedScene

# ------------------------------------------------------------------------------------------------ choose the path


def test_path_options_are_cleaned_to_two_short_different_ones():
    assert branches.clean_paths([' "the door" ', "the *stairs*"]) == ["the door", "the stairs"]
    assert branches.clean_path("the long red hallway beyond the arch") == "the long red hallway", "at most 4 words"
    assert branches.clean_path("← left door") == "left door"
    assert branches.clean_paths(["the door", "THE DOOR"]) is None, "two different options or none"
    assert branches.clean_paths(["the door"]) is None
    assert branches.clean_paths(["  ", None, "x"]) is None
    assert branches.clean_paths(None) is None
    assert branches.clean_paths(["a", "b", "c"]) == ["a", "b"], "only two are kept"


def test_a_pick_is_a_or_b_and_remembers_who_picked():
    assert PathPick.owner(" b ").path == "B"
    assert PathPick.owner("A").source == branches.OWNER
    vote = PathPick("a", branches.COMMENTS, {"A": 30, "B": 12})
    assert vote.source == branches.COMMENTS and vote.path == "A"
    with pytest.raises(ValueError):
        PathPick.owner("C")
    with pytest.raises(ValueError):
        PathPick.owner(None)


def test_the_writer_continues_the_same_world_from_the_last_frame_down_the_chosen_path():
    s = branches.for_writer({"title": "Glass City", "world": "a city of twisted glass", "hero": "a red kiosk",
                             "lastScene": "an arch with a door on the left and stairs on the right"}, "the stairs", 2)
    assert "EPISODE 2" in s and "SAME world" in s
    assert "a city of twisted glass" in s and "a red kiosk" in s
    assert "ended on: an arch with a door" in s
    assert "chose the path: THE STAIRS" in s and "last frame" in s
    assert "EPISODE 3" in branches.for_writer({}, "x", 3), "missing memory never breaks the writer"
    assert branches.child_idea("Glass City", "the stairs", 2) == "Episode 2: Glass City - the path chosen: the stairs"


# ------------------------------------------------------------------------------------------------ idea packs

STAR = re.compile(r"\*[^*\s]+\*")


def test_the_pack_loads_with_at_least_twenty_ideas_in_a_dream_look():
    pack = idea_packs.pack("impossible-architecture")
    assert len(pack.ideas) >= 20
    assert pack.look in looks.LOOKS and pack.look in looks.SELECTABLE_LOOKS
    assert pack.look not in looks.LOOK_ORDER, "the Autopilot's rotation is unchanged"


def test_every_idea_has_a_unique_id_a_hook_of_three_to_six_words_with_one_big_word_and_a_camera_note():
    ids = set()
    for i in idea_packs.pack("impossible-architecture").ideas:
        assert i.id not in ids
        ids.add(i.id)
        for f in (i.title, i.concept, i.camera, i.palette):
            assert f and f.strip(), f"{i.id} has an empty field"
        assert 3 <= len(i.hook.split()) <= 6, i.hook
        assert len(STAR.findall(i.hook)) == 1, i.hook
        assert i.hook == i.hook.upper()


def test_references_round_trip_and_unknown_ones_are_empty():
    i = idea_packs.idea("impossible-architecture", "looping-bridge")
    assert idea_packs.resolve(idea_packs.ref("impossible-architecture", "looping-bridge")) == i
    assert idea_packs.resolve(None) is None
    assert idea_packs.resolve("impossible-architecture/nope") is None
    assert idea_packs.resolve("garbage") is None
    assert i.camera in idea_packs.for_writer(i)


def test_the_architecture_look_has_its_own_camera_rule_shot_length_and_hard_cuts():
    lk = looks.look("architecture")
    assert lk.shot_seconds == 2.5 and lk.dissolve_sec == 0
    assert "glides slowly and continuously" in looks.FEEL_ARCHITECTURE
    assert "first frame IS the previous shot" in looks.CHAIN_STRUCTURE % 5


def test_clip_frames_and_sizes():
    assert looks.frames(3.5) == 57 and looks.frames(1) == 33 and looks.frames(9) == 81
    assert looks.clip_size("720p") == (720, 1280) and looks.clip_size(None) == (480, 832)
    assert looks.look(None) is looks.LOOKS["lonely"] and looks.look("nope") is looks.LOOKS["lonely"]
    assert clean_picture_model(" ZImage ", "x") == "zimage" and clean_picture_model("sdxl", "flux2-klein") == "flux2-klein"


# ------------------------------------------------------------------------------------------------ timeline

T0 = datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC)


def at(sec):
    return T0 + timedelta(seconds=sec)


def status(step, st, sec):
    return Event(step, "status", None, {"status": st}, at(sec))


def test_work_counts_only_running_and_steps_close_when_the_next_starts():
    events = [status("WRITE", "RUNNING", 0), status("WRITE", "WAITING_REVIEW", 60),  # 60 s work, then 600 s waiting
              status("PICTURES", "RUNNING", 660), status("PICTURES", "RUNNING", 700),
              status("CLIPS", "WAITING_COST", 760),  # 100 s pictures
              status("CLIPS", "RUNNING", 900)]  # still running at 1000
    steps = timeline.steps(events, [Cost(12.5, at(950)), Cost(1, at(680))], at(1000), {"CLIPS": 300})
    assert steps[0].status == "PENDING"  # SCOUT never ran
    write = steps[1]
    assert write.status == "DONE" and write.work_seconds == 60 and write.started_at == T0 and write.finished_at == at(660)
    assert steps[2].work_seconds == 100 and steps[2].cost_inr == 1.0
    clips = steps[3]
    assert clips.status == "RUNNING" and clips.finished_at is None and clips.work_seconds == 100
    assert clips.cost_inr == 12.5 and clips.typical_seconds == 300 and steps[4].typical_seconds is None


def test_a_rerun_replaces_the_steps_times_and_completion_closes_finish():
    events = [status("MUSIC", "RUNNING", 0), status("FINISH", "RUNNING", 10), status("FINISH", "COMPLETED", 40),
              status("FINISH", "RUNNING", 100), status("FINISH", "COMPLETED", 120)]
    work = timeline.finished_work(events)
    assert work["MUSIC"] == 10 and work["FINISH"] == 20


def test_typical_is_the_median_and_needs_two_shorts():
    typical = timeline.typical([{"CLIPS": 100, "WRITE": 30}, {"CLIPS": 300}, {"CLIPS": 200}])
    assert typical["CLIPS"] == 200 and "WRITE" not in typical  # one sample: no estimate yet
    assert timeline.typical([{"MUSIC": 100}, {"MUSIC": 200}])["MUSIC"] == 150


def test_log_is_newest_first_in_plain_words():
    events = [status("CLIPS", "RUNNING", 0), Event("CLIPS", "cancel", None, {"afterClip": True}, at(5)), status("CLIPS", "CANCELLED", 60)]
    log = timeline.log(events)
    assert log[0].text == "Short cancelled (at Clips)"
    assert log[1].text == "Cancel requested: it will apply after the current clip finishes"
    assert log[2].text == "Clips started"


# ------------------------------------------------------------------------------------------------ SVI scenes


def test_scene_lengths_need_three_four_or_five_beats():
    assert (svi.beats_for(10), svi.beats_for(15), svi.beats_for(20)) == (3, 4, 5)
    assert svi.frames_of(5) == 353
    assert svi.clean_seconds(30) == 20 and svi.clean_seconds(15) == 15
    assert svi.clean_between(None) == "cut" and svi.clean_between("Dissolve") == "dissolve"


def test_writer_scenes_are_flattened_to_beats_and_padded():
    beats = svi.flatten([PlannedScene(scene="a pier", beats=["walk", "turn"]), PlannedScene(scene="a door", beats=["rise", "drift", "push", "extra"])], 2, 3)
    assert len(beats) == 6
    assert beats[2] == svi.Beat(1, 3, "a pier", "turn"), "a missing beat repeats the scene's last motion"
    assert beats[3] == svi.Beat(2, 1, "a door", "rise")
    assert beats[5].motion == "push", "beats beyond the Short's shape are dropped"
    assert svi.flatten(None, 2, 3) == []


def test_unfiltered_blank_line_separates_scenes_and_each_line_is_a_beat():
    beats = svi.unfiltered("pink pier at dawn\nwalk to the end\n\n\na red door opens\n")
    assert beats == [svi.Beat(1, 1, "pink pier at dawn", "pink pier at dawn"), svi.Beat(1, 2, "walk to the end", "walk to the end"),
                     svi.Beat(2, 1, "a red door opens", "a red door opens")]
    assert [b.beat for b in svi.unfiltered("a\n\nb\n\nc\n\nd")] == [1, 1, 1], "at most 3 scenes"
    assert len(svi.unfiltered("1\n2\n3\n4\n5\n6\n7")) == 5, "at most 5 beats (20 s) a scene"


def test_writer_structure_asks_for_scenes_beats_and_one_style():
    s = svi.structure(3, 20, 5)
    assert "3 scenes" in s and "5 beats" in s and "never cuts inside a scene" in s
    assert svi.structure(1, 10, 3).startswith("Invent 1 scene in the same world.")
    assert '"style": ONE line' in svi.json_fields(3, 5)


def test_beats_per_scene_from_shot_rows():
    assert svi.beats_per_scene([{"scene_no": 1}, {"scene_no": 1}, {"scene_no": 2}]) == [2, 1]
    assert not svi.on({"svi_scenes": 0}) and svi.on({"svi_scenes": 2})


# ------------------------------------------------------------------------------------------------ rental estimate

E = RentalEstimate()


def test_one_svi_scene_of_four_clips_is_about_a_third_of_an_hour():
    h = E.hours_for_row({"shot_count": 4, "picture_model": "flux2-klein", "svi_scenes": 1})
    assert h == pytest.approx((900 + 3 + 4 * 76 + 180) / 3600.0, abs=1e-9)
    assert h == pytest.approx(0.35, abs=0.05)


def test_six_shot_wan_short_with_zimage():
    h = E.hours_for_row({"shot_count": 6, "picture_model": "zimage", "svi_scenes": 0})
    assert h == pytest.approx((900 + 6 * 5 + 6 * 76) / 3600.0, abs=1e-9)  # no SVI setup


def test_unknown_work_falls_back_to_the_configured_hours():
    assert E.hours_for_row(None) == 0.75
    assert E.hours_for_row({"picture_model": "flux2-klein"}) == 0.75
    assert E.hours_for_row({"shot_count": 4, "picture_model": None}) == 0.75
    assert E.hours_for_row({"shot_count": 0, "picture_model": "zimage"}) == 0.75
    assert E.hours(4, "sdxl", 4, False) == 0.75


def test_every_number_comes_from_config():
    slow = RentalEstimate(600, 10, 20, 100, 0, 1.0)
    assert slow.hours(2, "flux2-klein", 2, True) == pytest.approx((600 + 2 * 10 + 2 * 100) / 3600.0, abs=1e-9)
    assert slow.hours_for_row(None) == 1.0
