"""Edit-and-sync polish: beat snapping, per-cut transitions, loudness chain, ducking, end fade; flags default off."""

import re
import subprocess

import pytest

from workers.common import ffmpeg_bin, run_ffmpeg
from workers.dream import finish as dream
from workers.dream import polish


def test_flags_are_opt_in():
    assert not any(polish.flag({}, f) for f in polish.FLAGS)
    assert not polish.flag({"beatSnap": "yes"}, "beatSnap")
    assert polish.flag({"beatSnap": True}, "beatSnap")


BEATS = [round(0.5 * k, 3) for k in range(40)]  # 120 bpm


def test_snap_trims_to_a_beat_before_the_cut():
    dur, st = polish.snap_cuts([3.7, 4.0, 4.0], [0, 0.0, 0.0], BEATS)
    assert dur[0] == pytest.approx(3.5) and st[0] == 1.0  # 3.7 -> nearest beat 3.5 (trim 0.2) beats 4.0 (stretch 0.3 > room 0.22)


def test_snap_can_slow_a_shot_a_little_to_reach_a_beat():
    dur, st = polish.snap_cuts([3.9, 4.0, 4.0], [0, 0.0, 0.0], BEATS)
    assert dur[0] == pytest.approx(4.0) and st[0] == pytest.approx(4.0 / 3.9, abs=1e-3)


def test_snap_prefers_bar_lines_and_respects_the_stretch_limit():
    bars = [4.0]
    dur, _ = polish.snap_cuts([3.8, 4.0, 4.0], [0, 0.0, 0.0], BEATS, bars=bars)
    assert dur[0] == pytest.approx(4.0)  # a reachable bar (4.0, +0.2, inside the 6% room) wins over the nearer beat 3.5
    dur2, st2 = polish.snap_cuts([3.0, 4.0, 4.0], [0, 0.0, 0.0], [3.6], max_trim=1.0)  # 3.6 is 20% away: out of reach
    assert dur2[0] == 3.0 and st2[0] == 1.0


def test_snap_keeps_last_shot_and_min_len():
    dur, _ = polish.snap_cuts([3.6, 3.5], [0, 0.0], [3.0, 3.5], min_len=3.4)
    assert dur[1] == 3.5 and dur[0] >= 3.4 - 1e-9


def test_snap_with_dissolves_lands_the_dissolve_middle_on_the_beat():
    dur, _ = polish.snap_cuts([4.2, 4.0], [0, 0.5], BEATS)
    assert dur[0] - 0.25 in BEATS  # the middle of the 0.5 s dissolve (clip end minus half of it) sits on a beat


def test_snap_is_deterministic_and_improves_grid_error():
    durs, dis = [3.7, 3.9, 4.1, 4.0], [0, 0.0, 0.0, 0.0]
    a = polish.snap_cuts(durs, dis, BEATS)
    assert a == polish.snap_cuts(durs, dis, BEATS)
    assert polish.grid_error(a[0], dis, BEATS) < polish.grid_error(durs, dis, BEATS)


def test_prefer_pulls_the_climax_cut_to_the_rise():
    dur, _ = polish.snap_cuts([4.0, 4.0, 4.0], [0, 0.0, 0.0], BEATS, prefer={0: 3.5})
    assert dur[0] == pytest.approx(3.5)


def test_transitions_follow_how_much_the_frames_differ():
    assert polish.transition_lengths([5, 25, 60], 0.5) == [0.12, 0.25, 0.5]
    assert polish.transition_lengths([5, 60], 0.0) == [0.0, 0.0]


def test_graph_takes_per_cut_dissolves_and_stretch():
    g = dream.dissolve_graph([4.0, 4.0, 4.0], [0.0, 0.12, 0.5], 1080, 1920, 24, "unfiltered", stretch=[1.04, 1.0, 1.0])
    assert "xfade=transition=fade:duration=0.12:offset=3.88" in g
    assert "duration=0.5:offset=7.38" in g
    assert "setpts=PTS*1.0400,trim=duration=4.000" in g
    assert dream.total_duration([4.0, 4.0, 4.0], [0.0, 0.12, 0.5]) == pytest.approx(11.38)


def test_scalar_dissolve_path_is_unchanged():
    assert dream.total_duration([3.5, 3.5, 3.5], 0.5) == pytest.approx(9.5)
    assert "setpts=PTS*" not in dream.dissolve_graph([3.5, 3.5], 0.5, 1080, 1920, 24, "dreamcore")


STATS = "noise {\n\t\"input_i\" : \"-21.30\",\n\t\"input_tp\" : \"-4.10\",\n\t\"input_lra\" : \"3.20\",\n\t\"input_thresh\" : \"-31.50\",\n\t\"target_offset\" : \"0.20\"\n}"


def test_parse_loudnorm_and_two_pass_filter():
    s = polish.parse_loudnorm("junk\n" + STATS)
    assert s["input_i"] == -21.3 and s["target_offset"] == 0.2
    f = polish.loudness_filter(s)
    assert "I=-14.0" in f and "TP=-1.5" in f and "measured_I=-21.3" in f and "linear=true" in f and "alimiter" in f
    assert polish.parse_loudnorm("nothing here") is None
    one = polish.loudness_filter(None)
    assert "measured_I" not in one and "alimiter" in one


def test_limiter_ceiling_sits_under_the_true_peak_target():
    ceiling = float(re.search(r"limit=([0-9.]+)", polish.limiter(-1.5)).group(1))
    assert 20 * __import__("math").log10(ceiling) == pytest.approx(-1.8, abs=0.05)


def test_end_fade_straight_or_soft():
    assert polish.end_fade(20, False) == "afade=t=out:st=18.500:d=1.500"
    assert polish.end_fade(20, True) == "afade=t=out:st=17.500:d=2.500:curve=qsin"
    assert "d=0.500" in polish.end_fade(1.0, True)  # never longer than a third of a very short Short


def test_duck_filters_wire_voice_as_the_sidechain():
    f = polish.duck_filters("[bed]", "[voice]", "mix")
    assert f[0] == "[voice]asplit=2[dv1][dv2]" and "sidechaincompress" in f[1] and f[2].endswith("[mix]")


def _lufs_and_peak(path):
    err = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", err)[-1]), float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", err)[-1])


def test_two_pass_loudness_hits_target_and_peak_ceiling(tmp_path):
    src = tmp_path / "quiet.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=f=220:d=8,volume=0.05,aformat=channel_layouts=stereo:sample_rates=48000", str(src)])
    stats = polish.measure_loudness("[0:a]anull[x]", "[x]", ["-i", str(src)], tmp_path)
    assert stats is not None and stats["input_i"] < -20
    out = tmp_path / "loud.wav"
    run_ffmpeg(["-i", str(src), "-af", polish.loudness_filter(stats), "-ar", "48000", str(out)])
    lufs, peak = _lufs_and_peak(out)
    assert lufs == pytest.approx(-14.0, abs=1.0)
    assert peak <= -1.4
