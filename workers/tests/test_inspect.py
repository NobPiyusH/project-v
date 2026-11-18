"""Inspector file checks against tiny generated media (needs FFmpeg on PATH, like the worker itself)."""

import shutil
from pathlib import Path

import pytest

from workers.activities import inspect as I
from workers.common import run_ffmpeg

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None and not I.ffmpeg_bin(), reason="needs FFmpeg")


@pytest.fixture()
def local(monkeypatch, tmp_path):
    """URIs in these tests are local file paths: 'download' copies instead of reading SeaweedFS."""
    monkeypatch.setattr(I, "download", lambda uri, dest: Path(shutil.copy(uri, dest)))
    return tmp_path


def image(path: Path, size: str, source: str) -> str:
    sep = ":" if "=" in source else "="
    run_ffmpeg(["-f", "lavfi", "-i", f"{source}{sep}s={size}", "-frames:v", "1", str(path)])
    return str(path)


def codes(result: dict) -> list[str]:
    return [f["code"] for f in result["findings"]]


def test_black_wrong_way_and_duplicate_images_are_caught(local):
    good = image(local / "good.png", "540x960", "testsrc2")
    black = image(local / "black.png", "540x960", "color=c=black")
    wide = image(local / "wide.png", "960x540", "testsrc2")
    same = image(local / "same.png", "540x960", "testsrc2")
    r = I.inspect_media({"stage": "KEYFRAMES", "aspectRatio": "9:16", "images": [
        {"ref": "shot 1", "uri": good}, {"ref": "shot 2", "uri": black},
        {"ref": "shot 3", "uri": wide}, {"ref": "shot 4", "uri": same}]})
    by_ref = {(f["ref"], f["code"]) for f in r["findings"]}
    assert ("shot 2", "BLANK_IMAGE") in by_ref
    assert ("shot 3", "WRONG_ORIENTATION") in by_ref
    assert ("shot 4", "NEAR_DUPLICATE") in by_ref
    assert not any(ref == "shot 1" for ref, _ in by_ref)


def test_video_with_wrong_frame_cut_voice_and_black_start(local):
    video = local / "v.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=black:s=320x180:d=2", "-f", "lavfi", "-i", "testsrc2=s=320x180:d=3",
                "-f", "lavfi", "-i", "sine=f=440:d=5",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-map", "2:a",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)])
    r = I.inspect_media({"stage": "ANIMATIC", "aspectRatio": "9:16", "isShort": True, "targetSec": 5,
                         "videoUri": str(video), "speechEndSec": 6.5})
    assert {"WRONG_FRAME", "VOICE_CUT", "BLACK_FRAMES"} <= set(codes(r))


def test_clipping_voice_without_whisper_is_flagged_and_marked_unchecked(local, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    wav = local / "line.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "sine=f=300:d=2", "-af", "volume=24dB", str(wav)])
    r = I.inspect_media({"stage": "NARRATION", "lines": [{"index": 0, "text": "Hello there everyone today", "uri": str(wav)}]})
    assert "CLIPPING" in codes(r)
    assert "SPEECH_UNCHECKED" in codes(r)


def test_a_check_that_breaks_becomes_a_warning_not_an_error(local):
    r = I.inspect_media({"stage": "ANIMATIC", "videoUri": str(local / "missing.mp4")})
    assert codes(r) == ["CHECK_ERROR"]


def test_a_still_pretending_to_be_video_is_frozen_and_real_motion_passes(local):
    still = local / "still.mp4"
    moving = local / "moving.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=270x480:d=3", "-vf", "trim=end_frame=1,loop=loop=80:size=1",
                "-r", "16", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(still)])
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=s=270x480:d=3:r=16", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(moving)])
    r = I.inspect_media({"stage": "DRAFT_GEN", "aspectRatio": "9:16",
                         "images": [{"ref": "shot 1", "uri": str(still)}, {"ref": "shot 2", "uri": str(moving)}]})
    by_ref = {(f["ref"], f["code"]) for f in r["findings"]}
    assert ("shot 1", "FROZEN") in by_ref
    assert not any(ref == "shot 2" for ref, _ in by_ref), r["findings"]
    assert r["checked"] == ["shot 1", "shot 2"]
