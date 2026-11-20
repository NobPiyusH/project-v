"""'Choose the path': the end card (reuses the hook title's renderer) and a continued episode's shot 1 starting from the
previous episode's last frame. FFmpeg only, no GPU."""

import subprocess

from workers.activities import clips as C
from workers.common import ffmpeg_bin, run_ffmpeg
from workers.dream import finish as dream, hook


def _peak(path, at: float) -> float:
    out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-ss", str(at), "-i", str(path), "-frames:v", "1", "-vf",
                          "signalstats,metadata=print:key=lavfi.signalstats.YMAX", "-f", "null", "-"],
                         capture_output=True, text=True, check=True).stderr
    return float(out.split("YMAX=")[1].split()[0])


def test_end_card_shows_both_paths_with_arrows_over_the_last_two_seconds():
    ass = hook.end_card_ass("the door", "the *stairs* {x}", 1080, 1920, 20.0, "yellow")
    assert "WHICH WAY?" in ass and "← A: THE DOOR" in ass and "B: THE STAIRS X →" in ass
    assert "COMMENT A OR B" in ass
    assert "Dialogue: 0,0:00:18.00," in ass, "starts 2 s before the end"
    assert hook._colour("yellow") in ass, "the big word takes the hook's colour"
    assert "Style: End,Segoe UI," in ass, "same font and outline style as the hook title"
    long = hook.end_card_ass("a very long path name here", "b", 1080, 1920, 20)
    assert "A: A VERY LONG PATH" in long and "NAME" not in long, "at most 4 words"
    assert hook.end_card_ass("", "the stairs", 1080, 1920, 20) is None, "both options or no card"
    assert "0:00:00.00" in hook.end_card_ass("a", "b", 1080, 1920, 1.0), "a Short shorter than the card never goes negative"


def test_finish_burns_the_end_card_only_when_asked(tmp_path, monkeypatch):
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
            "dissolveSec": 0.5, "musicDir": None, "ambience": False, "pacing": False, "keepOrder": True}
    peaks = {}
    for name, extra in (("off", {}), ("on", {"pathA": "the door", "pathB": "the stairs"}),
                        ("both", {"pathA": "the door", "pathB": "the stairs", "hookText": "the *city* folds"})):
        work = tmp_path / name
        work.mkdir()
        out = dream.finish({**base, **extra}, work)
        f = work / "dream-short.mp4"
        peaks[name] = (_peak(f, 0.5), _peak(f, out["durationSec"] - 0.5))
    assert peaks["on"][1] > peaks["off"][1] + 100, "the card shows at the end"
    assert abs(peaks["on"][0] - peaks["off"][0]) < 5, "and not at the start"
    assert peaks["both"][0] > peaks["off"][0] + 100 and peaks["both"][1] > peaks["off"][1] + 100, "hook and card together"


def test_a_continued_episode_starts_from_the_previous_episodes_last_frame(monkeypatch):
    seen = []
    monkeypatch.setattr(C, "get_bytes", lambda uri: seen.append(uri) or b"parent-mp4")
    monkeypatch.setattr(C, "last_frame_png", lambda mp4, clean=False: b"PNG:" + mp4)
    job = {"shotId": "abcdefgh-1", "shotNumber": 1, "take": 1, "imageUri": None, "chainFromUri": "s3://b/parent-last.mp4"}
    assert C.start_image(job, False, {}, set()) == b"PNG:parent-mp4"
    assert C.start_image(job, True, {}, set()) == b"PNG:parent-mp4", "chained or not"
    assert seen == ["s3://b/parent-last.mp4"] * 2
    assert C.start_image({**job, "imageUri": "s3://b/pic.png"}, False, {}, set()) == b"parent-mp4", "a picture wins"
