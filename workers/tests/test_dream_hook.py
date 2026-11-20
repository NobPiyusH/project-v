"""Hook title: parsing, line layout, the .ass file, and a real FFmpeg overlay on a synthetic clip."""

import subprocess
from pathlib import Path

import pytest

from workers.common import ffmpeg_bin, run_ffmpeg
from workers.dream import finish as dream
from workers.dream import hook


def test_parse_marks_the_starred_word_and_uppercases():
    assert hook.parse_hook("the city *forgot* how to stand") == [
        ("THE", False), ("CITY", False), ("FORGOT", True), ("HOW", False), ("TO", False), ("STAND", False)]


def test_no_star_emphasises_the_longest_word_and_only_one():
    words = hook.parse_hook("a city without gravity")
    assert [w for w, e in words if e] == ["WITHOUT"] or [w for w, e in words if e] == ["GRAVITY"]
    assert sum(e for _, e in words) == 1


def test_at_most_six_words_and_empty_text_gives_nothing():
    assert len(hook.parse_hook("one two three four five six seven eight")) == hook.MAX_WORDS
    assert hook.parse_hook("   ") == []
    assert hook.hook_ass("", 1080, 1920) is None


def test_lines_are_before_emphasis_after_and_at_most_three():
    lines = hook.hook_lines(hook.parse_hook("the city *forgot* how to stand"))
    assert [[w for w, _ in ln] for ln in lines] == [["THE", "CITY"], ["FORGOT"], ["HOW", "TO", "STAND"]]
    assert len(hook.hook_lines(hook.parse_hook("*gravity* left"))) == 2


def test_ass_has_red_big_word_fade_and_safe_position():
    ass = hook.hook_ass("the city *forgot* how to stand", 1080, 1920)
    assert "&H2020FF&" in ass                      # default red (BGR)
    assert r"\fad(250,350)" in ass and r"\pos(540,576)" in ass   # centred, 30 % down: upper-middle third
    assert "Dialogue: 0,0:00:00.00,0:00:01.80" in ass
    assert ass.count(r"\N") == 2                   # three lines
    big = int(ass.split(r"\fs")[1].split("\\")[0])
    assert big > 1920 * hook.NORMAL_PCT            # the emphasised word is larger


def test_colour_name_or_hex_and_unknown_falls_back_to_red():
    assert hook._colour("yellow") == "&H00D4FF&"
    assert hook._colour("#00ff00") == "&H00FF00&"
    assert hook._colour("nonsense") == hook._colour("red")


def test_long_lines_shrink_to_stay_inside_the_side_margins():
    lines = hook.hook_lines(hook.parse_hook("extraordinarily *incomprehensible* architecture"))
    normal, big = hook.font_sizes(lines, 540, 960)
    assert normal < 960 * hook.NORMAL_PCT
    widest = len("INCOMPREHENSIBLE") * hook.CHAR_WIDTH * big
    assert widest <= 540 * (1 - 2 * hook.SIDE_MARGIN) + 1


def _clip(path: Path, seconds: float = 3.0) -> None:
    run_ffmpeg(["-f", "lavfi", "-i", f"color=c=0x203040:s=270x480:r=24:d={seconds}", "-f", "lavfi", "-i",
                f"anullsrc=r=48000:cl=stereo:d={seconds}", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", str(path)])


def _frame_peak(path: Path, at: float) -> float:
    out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-ss", str(at), "-i", str(path), "-frames:v", "1", "-vf",
                          "signalstats,metadata=print:key=lavfi.signalstats.YMAX", "-f", "null", "-"],
                         capture_output=True, text=True, check=True).stderr
    return float(out.split("YMAX=")[1].split()[0])


def test_overlay_is_burned_into_the_first_seconds_only(tmp_path):
    src, dst = tmp_path / "src.mp4", tmp_path / "out.mp4"
    _clip(src)
    assert hook.apply_hook(src, dst, "the city *forgot* how to stand", 270, 480) is True
    plain = _frame_peak(src, 0.9)
    assert _frame_peak(dst, 0.9) > plain + 100   # white text on a dark clip: the brightest pixel jumps while the title shows
    assert abs(_frame_peak(dst, 2.5) - _frame_peak(src, 2.5)) < 5   # gone after ~1.8 s
    assert hook.apply_hook(src, tmp_path / "none.mp4", "  ", 270, 480) is False


def test_finish_burns_the_hook_only_when_asked(tmp_path, monkeypatch):
    clips = []
    for i in range(2):
        p = tmp_path / f"src{i}.mp4"
        run_ffmpeg(["-f", "lavfi", "-i", "color=c=0x203040:s=240x416:r=16:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)])
        clips.append({"uri": f"s3://test/{p.name}", "fps": 16})
    monkeypatch.setattr(dream, "get_bytes", lambda uri: (tmp_path / uri.rsplit("/", 1)[1]).read_bytes())
    monkeypatch.setattr(dream, "_smooth", lambda src, dst, src_fps, dst_fps, speed=1.0: (
        run_ffmpeg(["-i", str(src), "-vf", f"fps={dst_fps}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dst)]), "ffmpeg")[1])
    monkeypatch.setattr(dream, "put_file", lambda key, path, ct: f"s3://bucket/{key}")
    base = {"dreamId": "d1", "look": "dreamcore", "clips": clips, "width": 270, "height": 480, "fps": 24,
            "dissolveSec": 0.5, "musicDir": None, "ambience": False, "pacing": False}
    means = {}
    for name, extra in (("off", {}), ("on", {"hookText": "the city *forgot* how to stand"})):
        work = tmp_path / name
        work.mkdir()
        dream.finish({**base, **extra}, work)
        means[name] = _frame_peak(work / "dream-short.mp4", 0.9)
    assert means["on"] > means["off"] + 100
