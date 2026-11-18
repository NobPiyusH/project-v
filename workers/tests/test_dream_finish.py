"""Dream Short finish: filter graph, dissolve timing, music pick, and a real FFmpeg run on tiny synthetic clips."""

from pathlib import Path

import pytest

from workers.common import media_duration, run_ffmpeg
from workers.dream import finish as dream


def test_dissolve_graph_chains_every_clip_and_ends_with_the_look():
    g = dream.dissolve_graph([3.5, 3.5, 3.5], 0.5, 1080, 1920, 24, "dreamcore")
    assert g.count("xfade=transition=fade") == 2
    assert "offset=3.0" in g and "offset=6.0" in g  # 3.5-0.5, then +3.5-0.5
    assert g.endswith("format=yuv420p[v]")
    assert "gblur" in g and "vignette" in g


def test_dissolve_never_longer_than_a_third_of_a_short_clip():
    g = dream.dissolve_graph([0.6, 3.0], 0.5, 1080, 1920, 24, "dreamcore")
    assert "duration=0.2" in g
    assert dream.total_duration([0.6, 3.0], 0.5) == pytest.approx(3.4)


def test_unknown_look_falls_back_to_dreamcore():
    assert dream.look_filter("nope") == dream.LOOKS["dreamcore"]


def test_pick_track_is_stable_per_short_and_ignores_non_audio(tmp_path):
    for name in ("a.mp3", "b.wav", "c.flac", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    first = dream.pick_track(str(tmp_path), "short-1")
    assert first is not None and first.suffix != ".txt"
    assert dream.pick_track(str(tmp_path), "short-1") == first
    assert dream.pick_track(None, "short-1") is None
    assert dream.pick_track(str(tmp_path / "missing"), "short-1") is None


def test_finish_makes_one_vertical_24fps_short(tmp_path, monkeypatch):
    clips = []
    for i, color in enumerate(("pink", "teal")):
        p = tmp_path / f"src{i}.mp4"
        run_ffmpeg(["-f", "lavfi", "-i", f"color=c={color}:s=240x416:r=16:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)])
        clips.append({"uri": f"s3://test/{p.name}", "fps": 16})
    saved = {}
    monkeypatch.setattr(dream, "get_bytes", lambda uri: (tmp_path / uri.rsplit("/", 1)[1]).read_bytes())
    monkeypatch.setattr(dream, "_smooth", lambda src, dst, src_fps, dst_fps: (
        run_ffmpeg(["-i", str(src), "-vf", f"fps={dst_fps}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dst)]), "ffmpeg")[1])

    def fake_put(key, path, content_type):
        saved["path"] = Path(path).read_bytes()
        return f"s3://bucket/{key}"

    monkeypatch.setattr(dream, "put_file", fake_put)
    work = tmp_path / "work"
    work.mkdir()
    r = dream.finish({"dreamId": "d1", "look": "dreamcore", "clips": clips, "width": 270, "height": 480, "fps": 24,
                      "dissolveSec": 0.5, "musicDir": None}, work)
    assert r["uri"] == "s3://bucket/videos/d1/dream-short.mp4"
    assert r["smoothing"] == "ffmpeg" and r["music"] is None
    assert r["durationSec"] == pytest.approx(3.5, abs=0.15)
    out = work / "dream-short.mp4"
    assert out.exists() and media_duration(out) == pytest.approx(3.5, abs=0.15)

    # With the Short's own score (Lyria): it is fetched from storage and mixed in.
    score = tmp_path / "score.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=6", str(score)])
    work2 = tmp_path / "work2"
    work2.mkdir()
    r2 = dream.finish({"dreamId": "d1", "look": "dreamcore", "clips": clips, "width": 270, "height": 480, "fps": 24,
                       "dissolveSec": 0.5, "musicDir": None, "musicUri": "s3://test/score.wav"}, work2)
    assert r2["music"] == "Lyria score"
    assert media_duration(work2 / "dream-short.mp4") == pytest.approx(3.5, abs=0.15)


def test_hard_cuts_join_without_dissolves():
    g = dream.dissolve_graph([3.5, 3.5, 3.5], 0, 1080, 1920, 24, "lonely")
    assert "xfade" not in g and "concat=n=3:v=1:a=0" in g and g.endswith("[v]")
    assert dream.total_duration([3.5, 3.5, 3.5], 0) == 10.5


def test_clean_looks_have_no_haze_or_grain():
    for look in ("lonely", "walkthrough"):
        f = dream.look_filter(look)
        assert "noise" not in f and "gblur" not in f and "vignette" not in f
