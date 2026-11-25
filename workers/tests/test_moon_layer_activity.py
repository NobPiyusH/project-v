"""moon_layer_composite on a tiny synthetic plate made by ffmpeg lavfi in a temp dir: no network, no pods, no paid calls (storage is faked)."""

import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("temporalio")

from temporalio.exceptions import ApplicationError  # noqa: E402

from workers.activities import moon_layer as m  # noqa: E402
from workers.common import ffmpeg_bin  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None and shutil.which(ffmpeg_bin()) is None, reason="needs ffmpeg")


def _ff(*args):
    subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture
def world(tmp_path, monkeypatch):
    plate = tmp_path / "src_plate.mp4"
    _ff("-f", "lavfi", "-i", "testsrc=size=160x288:rate=8:duration=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(plate))
    moon = tmp_path / "moon.png"  # a light disc on black
    _ff("-f", "lavfi", "-i", "color=c=black:s=128x128,geq=lum='if(lt(hypot(X-64,Y-64),50),230,0)':cb=128:cr=128", "-frames:v", "1", str(moon))
    puts = {}
    monkeypatch.setattr(m, "download", lambda uri, dest: Path(shutil.copy(plate, dest)))
    monkeypatch.setattr(m, "put_file", lambda key, path, ct: puts.setdefault(key, Path(shutil.copy(path, tmp_path / "uploaded.mp4"))) and f"s3://b/{key}")
    return tmp_path, moon, puts


def _req(moon, **kw):
    return {"videoId": "v1", "plateUri": "s3://b/master.mp4", "imagePath": str(moon), "keyBlack": True,
            "layer": {"diameterFrac": 0.4, "scaleFrom": 1.0, "scaleTo": 1.8, "ease": "smooth", "glow": 0.5}, "caption": None, **kw}


def test_composite_keeps_size_rate_duration_and_audio_and_stores_the_new_video(world):
    tmp, moon, puts = world
    r = m.moon_layer_composite(_req(moon))
    assert r["videoUri"] == "s3://b/videos/v1/master/moon-layer.mp4" and list(puts) == ["videos/v1/master/moon-layer.mp4"]
    assert (r["width"], r["height"], r["fps"]) == (160, 288, 8) and r["durationSec"] == pytest.approx(2.0, abs=0.3)
    info = m.probe(tmp / "uploaded.mp4")
    assert (info["width"], info["height"], info["audio"]) == (160, 288, True), "the master's audio track is copied"
    assert len(r["imageSha256"]) == 64


@pytest.mark.skipif(not Path("C:/Windows/Fonts/arialbd.ttf").exists(), reason="the caption font is the Windows one")
def test_composite_with_a_caption_runs(world):
    _, moon, _ = world
    r = m.moon_layer_composite(_req(moon, caption={"text": "What if?", "startSec": 0.0, "endSec": 1.0}))
    assert r["width"] == 160


def test_a_missing_moon_picture_is_a_non_retryable_error_so_the_stage_waits_for_the_owner(world):
    tmp, _, _ = world
    with pytest.raises(ApplicationError) as e:
        m.moon_layer_composite(_req(tmp / "nope.png"))
    assert e.value.type == m.INPUT_MISSING and e.value.non_retryable
    with pytest.raises(ApplicationError):
        m.moon_layer_composite(_req(None))
