"""The quality gate adapter: runs workers/quality_gate on a tiny generated cut (no database, no paid vision, no network)."""

import shutil
import subprocess

import pytest

from studio.quality import run_quality_gate

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def make_cut(path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(path)], check=True)


def test_gate_runs_on_a_finished_cut_without_vision_or_database(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    cut = tmp_path / "cut.mp4"
    make_cut(cut)
    r = run_quality_gate(cut, tmp_path / "out", no_vision=True, no_db=True)
    assert r.overall in ("PASS", "FAIL") and r.report_json is not None and r.report_md is not None
    assert {"vision", "audio", "freeze", "cost"} <= set(r.data["checks"]) and r.data["checks"]["vision"]["status"] == "SKIPPED"
    assert r.exit_code == (0 if r.passed else 1) and isinstance(r.failures(), list)
