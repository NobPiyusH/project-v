"""Dream Short music bed: where each Short's piece starts, and a real cut of a generated tone."""

import shutil
import subprocess
from pathlib import Path

import pytest

from workers.dream.music_bed import EDGE_SEC, cut, mean_volume, pick_start


def test_starts_stay_inside_the_track_and_follow_the_seed():
    a = pick_start("short-1#0", 3600, 16, [])
    assert a == pick_start("short-1#0", 3600, 16, [])  # same Short, same take: same piece (a retry is stable)
    assert a[0] != pick_start("short-1#1", 3600, 16, [])[0]  # New music: another piece
    assert a[0] != pick_start("short-2#0", 3600, 16, [])[0]  # another Short: another piece
    assert all(EDGE_SEC <= s <= 3600 - 16 - EDGE_SEC for s in a)


def test_starts_other_shorts_used_are_avoided():
    used = pick_start("short-1#0", 3600, 16, [])[:1]
    for seed in ("short-2#0", "short-3#0", "short-4#0"):
        assert all(abs(s - used[0]) >= 16 for s in pick_start(seed, 3600, 16, used))


def test_a_track_shorter_than_the_short_starts_at_zero():
    assert pick_start("x", 10, 16, []) == [0.0]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg")
def test_cut_makes_a_faded_stereo_piece(tmp_path: Path):
    bed = tmp_path / "bed.m4a"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=40",
                    "-c:a", "aac", str(bed)], check=True)
    out = tmp_path / "piece.wav"
    cut(bed, 12.0, 8.0, out)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=channels,sample_rate:format=duration",
                            "-of", "default=nw=1", str(out)], capture_output=True, text=True, check=True).stdout
    assert "channels=2" in probe and "sample_rate=48000" in probe
    assert abs(float(probe.split("duration=")[1].split()[0]) - 8.0) < 0.1
    assert mean_volume(bed, 12.0, 8.0) > -35


def test_the_strongest_beat_near_the_start_wins():
    from workers.dream.music_bed import strongest_beat
    beats, strength = [0.2, 0.7, 1.2, 1.7, 2.2], [1.0, 3.0, 1.0, 3.0, 9.0]
    assert strongest_beat(beats, strength, 0, 2.0, 1.5) == 1.7  # 2.2 is outside; 0.7 and 1.7 tie, 1.7 is nearer
    assert strongest_beat(beats, strength, 3, 4, 3.5) is None
