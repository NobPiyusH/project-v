"""tools/film4k/interpolate.py: pure command builders, and the whole recipe on a synthetic ffmpeg lavfi clip with a FAKE rife program (no Vulkan, no GPU).
One marked-slow test runs the real RIFE if it is installed on this machine."""

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tools.film4k import interpolate as ip

FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="needs ffmpeg and ffprobe on PATH")

FAKE = textwrap.dedent("""
    import shutil, sys
    from pathlib import Path
    a = sys.argv[1:]
    src, dst, n, model = Path(a[a.index("-i") + 1]), Path(a[a.index("-o") + 1]), int(a[a.index("-n") + 1]), a[a.index("-m") + 1]
    short = int(Path(__file__).with_name("short.txt").read_text()) if Path(__file__).with_name("short.txt").exists() else 0
    frames = sorted(src.glob("*.png"))
    assert model and a[a.index("-j") + 1] == "1:1:1"
    for k in range(n - short):
        shutil.copy(frames[k * len(frames) // n], dst / f"{k + 1:08d}.png")
""")


def fake_rife(tmp_path: Path, short: int = 0) -> str:
    """A stand-in for rife-ncnn-vulkan.exe: writes `-n` frames by repeating the inputs (short = how many frames it leaves out)."""
    (tmp_path / "fake_rife.py").write_text(FAKE)
    (tmp_path / "short.txt").write_text(str(short))
    if os.name == "nt":
        exe = tmp_path / "rife.cmd"
        exe.write_text(f'@"{sys.executable}" "{tmp_path / "fake_rife.py"}" %*\n')
    else:
        exe = tmp_path / "rife.sh"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{tmp_path / "fake_rife.py"}" "$@"\n')
        exe.chmod(0o755)
    return str(exe)


def make_clip(path: Path, seconds: float = 1.0, fps: int = 8, audio: bool = True) -> None:
    cmd = [FFMPEG, "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=160x288:rate={fps}:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)


def audio_md5(path: Path) -> str:
    out = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vn", "-c", "copy", "-f", "md5", "-"], capture_output=True, text=True, check=True)
    return out.stdout.strip()


def streams(path: Path) -> dict:
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,r_frame_rate,nb_frames,duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return {s["codec_type"]: s for s in json.loads(out.stdout)["streams"]}


# ---------------------------------------------------------------------------------------------------- pure builders

def test_frame_count_is_factor_times_input_and_bad_input_is_refused():
    assert ip.out_frame_count(74) == 148 and ip.out_frame_count(10, 3) == 30
    for bad in ((1, 2), (10, 1)):
        with pytest.raises(ValueError):
            ip.out_frame_count(*bad)


def test_rife_command_is_the_proven_recipe():
    c = ip.rife_cmd("rife.exe", "in", "out", 648)
    assert c == ["rife.exe", "-i", "in", "-o", "out", "-m", "rife-v4.6", "-n", "648", "-j", "1:1:1"]


def test_extract_command_keeps_every_frame_and_drops_audio():
    c = ip.extract_cmd("ffmpeg", "src.mp4", "frames")
    assert "-an" in c and c[c.index("-fps_mode") + 1] == "passthrough" and c[-1].endswith("%08d.png")


def test_encode_command_doubles_the_rate_and_copies_the_audio_stream():
    c = ip.encode_cmd("ffmpeg", "out", "src.mp4", "final.mp4", 24, 2)
    assert c[c.index("-framerate") + 1] == "48" and c[c.index("-c:a") + 1] == "copy" and c[c.index("-crf") + 1] == "16"
    assert c[c.index("-pix_fmt") + 1] == "yuv420p" and c[c.index("-map", c.index("-map") + 1):][:2] == ["-map", "1:a:0"]
    assert "tpad" not in " ".join(c)
    assert "stop=1" in " ".join(ip.encode_cmd("ffmpeg", "o", "s", "f", 24, 2, pad_frames=1))
    silent = ip.encode_cmd("ffmpeg", "o", "s", "f", 24, 2, has_audio=False)
    assert "-c:a" not in silent and "1:a:0" not in silent
    assert ip.encode_cmd("ffmpeg", "o", "s", "f", 23.976, 2)[ip.encode_cmd("ffmpeg", "o", "s", "f", 23.976, 2).index("-framerate") + 1] == "47.952"


def test_disk_estimate_grows_with_frames():
    assert ip.disk_needed_bytes(1080, 1920, 100) > ip.disk_needed_bytes(1080, 1920, 50) > 0


# ------------------------------------------------------------------------------- the recipe with a fake rife program

@needs_ffmpeg
def test_recipe_makes_a_2x_rate_clip_with_the_same_duration_and_bit_identical_audio(tmp_path):
    src, out = tmp_path / "src.mp4", tmp_path / "out.mp4"
    make_clip(src)
    r = ip.interpolate(str(src), str(out), FFMPEG, FFPROBE, fake_rife(tmp_path), str(tmp_path / "work"), factor=2)
    assert (r["fpsIn"], r["fpsOut"], r["framesIn"], r["framesOut"]) == (8.0, 16.0, 8, 16)
    s = streams(out)
    assert s["video"]["r_frame_rate"] == "16/1" and int(s["video"]["nb_frames"]) == 16
    assert float(s["video"]["duration"]) == pytest.approx(float(streams(src)["video"]["duration"]), abs=0.01)
    assert audio_md5(out) == audio_md5(src), "the audio stream is copied, not re-encoded"
    assert list((tmp_path / "work").iterdir()) == [], "the scratch folder is removed"


@needs_ffmpeg
def test_one_missing_frame_is_padded_so_the_video_keeps_its_length(tmp_path):
    src, out = tmp_path / "src.mp4", tmp_path / "out.mp4"
    make_clip(src)
    ip.interpolate(str(src), str(out), FFMPEG, FFPROBE, fake_rife(tmp_path, short=1), str(tmp_path / "work"))
    assert int(streams(out)["video"]["nb_frames"]) == 16


@needs_ffmpeg
def test_far_too_few_frames_is_an_error_and_the_scratch_folder_is_still_removed(tmp_path):
    src = tmp_path / "src.mp4"
    make_clip(src)
    with pytest.raises(RuntimeError, match="RIFE returned"):
        ip.interpolate(str(src), str(tmp_path / "out.mp4"), FFMPEG, FFPROBE, fake_rife(tmp_path, short=5), str(tmp_path / "work"))
    assert list((tmp_path / "work").iterdir()) == []


@needs_ffmpeg
def test_a_silent_clip_works_too(tmp_path):
    src, out = tmp_path / "src.mp4", tmp_path / "out.mp4"
    make_clip(src, audio=False)
    ip.interpolate(str(src), str(out), FFMPEG, FFPROBE, fake_rife(tmp_path), str(tmp_path / "work"))
    assert "audio" not in streams(out)


@needs_ffmpeg
def test_not_enough_disk_space_is_refused_before_anything_runs(tmp_path, monkeypatch):
    src = tmp_path / "src.mp4"
    make_clip(src)
    monkeypatch.setattr(ip.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100, 99, 1))
    with pytest.raises(RuntimeError, match="Not enough free space"):
        ip.interpolate(str(src), str(tmp_path / "out.mp4"), FFMPEG, FFPROBE, fake_rife(tmp_path), str(tmp_path / "work"))


# ---------------------------------------------------------------------------------- the real program, if installed

@pytest.mark.slow
@needs_ffmpeg
@pytest.mark.skipif(not Path(ip.DEFAULT_EXE).is_file(), reason="real RIFE not installed on this machine")
def test_real_rife_doubles_a_short_clip(tmp_path):
    src, out = tmp_path / "src.mp4", tmp_path / "out.mp4"
    make_clip(src, seconds=1.0, fps=12)
    r = ip.interpolate(str(src), str(out), FFMPEG, FFPROBE, ip.DEFAULT_EXE, str(tmp_path / "work"))
    assert r["fpsOut"] == 24.0 and streams(out)["video"]["r_frame_rate"] == "24/1" and audio_md5(out) == audio_md5(src)
