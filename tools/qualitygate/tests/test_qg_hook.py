"""The pipeline hook: report JSON next to the output, fail/warn/error handling, config flag. Tiny ffmpeg clips; no network, no spend."""

import json
import shutil
import subprocess

import pytest

from tools.qualitygate import hook

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")


def _clip(path, src):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src, "-f", "lavfi", "-i", "sine=f=440:d=3", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)  # fmt: skip


def test_black_clip_fails_and_report_is_written_next_to_it(tmp_path):
    v = tmp_path / "cut.mp4"
    _clip(v, "color=black:s=320x180:r=16:d=3")
    out = hook.run_hook(v)
    assert out.failed and out.report_path == tmp_path / "cut.quality-gate.json"
    data = json.loads(out.report_path.read_text(encoding="utf-8"))
    assert data["judge"] == [] and data["tiers"] == [1, 2] and data["status"] == "fail" and "thresholds" in data
    assert out.failures and "FAIL" in out.summary and "cut.quality-gate.json" in out.summary


def test_moving_clip_does_not_fail_and_warns_are_listed_not_blocking(tmp_path):
    v = tmp_path / "ok.mp4"
    _clip(v, "testsrc2=s=320x180:r=16:d=3")
    out = hook.run_hook(v, tmp_path / "r.json")
    assert not out.failed and out.status in ("pass", "warn") and (tmp_path / "r.json").exists()
    assert out.status == "pass" or out.warnings


def test_a_gate_crash_is_an_error_not_a_fail(tmp_path):
    out = hook.run_hook(tmp_path / "missing.mp4")
    assert out.status == "error" and not out.failed and out.report_path is None and "continuing" in out.summary


def test_flag_default_on_and_off_values(monkeypatch):
    monkeypatch.delenv(hook.ENV_FLAG, raising=False)
    assert hook.enabled()
    for off in ("0", "false", "OFF", "no"):
        assert not hook.enabled(off)
    assert hook.enabled("1")
