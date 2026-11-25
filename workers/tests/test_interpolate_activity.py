"""interpolate_master on a tiny synthetic clip made by ffmpeg lavfi, with a FAKE rife program: no network, no GPU, no paid calls (storage is faked)."""

import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("temporalio")

from tools.film4k.tests.test_interpolate import fake_rife  # noqa: E402
from workers.activities import interpolate as m  # noqa: E402
from workers.common import ffmpeg_bin  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None and shutil.which(ffmpeg_bin()) is None, reason="needs ffmpeg")


@pytest.fixture
def world(tmp_path, monkeypatch):
    src = tmp_path / "src_master.mp4"
    subprocess.run([ffmpeg_bin(), "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x288:rate=8:duration=1", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)
    puts = {}
    monkeypatch.setattr(m, "download", lambda uri, dest: Path(shutil.copy(src, dest)))
    monkeypatch.setattr(m, "put_file", lambda key, path, ct: puts.setdefault(key, Path(shutil.copy(path, tmp_path / "uploaded.mp4"))) and f"s3://b/{key}")
    monkeypatch.setenv("INTERPOLATE_RIFE_EXE", fake_rife(tmp_path))
    monkeypatch.setenv("INTERPOLATE_WORKDIR", str(tmp_path / "work"))
    return tmp_path, puts


def test_interpolation_stores_a_2x_video_and_reports_the_numbers(world):
    tmp, puts = world
    r = m.interpolate_master({"videoId": "v1", "sourceUri": "s3://b/master.mp4", "factor": 2})
    assert r["videoUri"] == "s3://b/videos/v1/master/interpolated.mp4" and list(puts) == ["videos/v1/master/interpolated.mp4"]
    assert (r["fpsIn"], r["fpsOut"], r["framesIn"], r["framesOut"], r["width"], r["height"]) == (8.0, 16.0, 8, 16, 160, 288)
    assert r["tool"] == "rife-ncnn-vulkan" and r["model"] == "rife-v4.6" and r["durationSec"] == pytest.approx(1.0, abs=0.15)
    assert list((tmp / "work").iterdir()) == [], "work files are removed"


def test_a_missing_rife_program_is_a_clear_skip_not_a_failure(world, monkeypatch):
    tmp, puts = world
    monkeypatch.setenv("INTERPOLATE_RIFE_EXE", str(tmp / "nope.exe"))
    r = m.interpolate_master({"videoId": "v1", "sourceUri": "s3://b/master.mp4"})
    assert "RIFE program was not found" in r["skip"] and not puts


def test_a_failing_rife_run_raises_so_the_stage_waits_for_the_owner(world, monkeypatch):
    tmp, _ = world
    (tmp / "short.txt").write_text("9")  # the fake leaves out 9 frames: far too few
    with pytest.raises(RuntimeError, match="RIFE returned"):
        m.interpolate_master({"videoId": "v1", "sourceUri": "s3://b/master.mp4"})
    assert list((tmp / "work").iterdir()) == []


def test_defaults_are_the_owners_laptop_paths_on_e(monkeypatch):
    for k in ("INTERPOLATE_RIFE_EXE", "INTERPOLATE_RIFE_MODEL", "INTERPOLATE_WORKDIR"):
        monkeypatch.delenv(k, raising=False)
    exe, model, work = m.settings()
    assert exe.startswith("E:") and model == "rife-v4.6" and work.startswith("E:") and "project-data" in work
