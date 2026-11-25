"""Golden: quality-gate results on tiny synthetic clips made with ffmpeg lavfi (skipped without ffmpeg). Free, local, seconds."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "golden"))
import golden  # noqa: E402

from tools.qualitygate import gate  # noqa: E402
from tools.qualitygate.types import Status  # noqa: E402

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")

VIDEO = {
    "static": "color=c=0x406080:s=320x180:r=16:d=3",
    "moving": "testsrc2=s=320x180:r=16:d=3",
}
AUDIO = {
    "silent": "anullsrc=r=48000:cl=mono",
    "quiet": "sine=f=440:r=48000:d=3,volume=0.01",
    "mid": "sine=f=440:r=48000:d=3,volume=0.2",
    "loud": "sine=f=440:r=48000:d=3,volume=4.0,alimiter=limit=1.0",
}


def _make(d: Path, name: str, video: str, audio: str | None) -> str:
    out = d / f"{name}.mp4"
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", VIDEO[video]]
    if audio:
        cmd += ["-f", "lavfi", "-i", AUDIO[audio], "-t", "3", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "8", str(out)]
    subprocess.run(cmd, check=True)
    return str(out)


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("golden_qg")
    c = {"static": _make(d, "static", "static", None), "moving": _make(d, "moving", "moving", None)}
    for a in AUDIO:
        c[f"audio_{a}"] = _make(d, f"audio_{a}", "moving", a)
    return c


def _summary(report) -> dict:
    out = {"status": report.status.value, "checks": {}}
    for c in report.checks:
        key = c.name if c.shot in (None, "all") else f"{c.name}@{c.shot}"
        out["checks"][key] = {"status": c.status.value, "value": None if c.value is None else round(c.value, 2)}
    return out


def test_static_clip_is_flagged_as_near_static(clips, request):
    rep = gate.run_gate(clips["static"])
    by = {c.name: c for c in rep.checks}
    assert by["motion"].status in (Status.FAIL, Status.WARN), "a static clip must never pass the motion check"
    assert by["frozen_frames"].status is Status.FAIL
    golden.check("qg_static_clip", _summary(rep), request.config)


def test_moving_clip_passes_motion_and_freeze(clips, request):
    rep = gate.run_gate(clips["moving"])
    by = {c.name: c for c in rep.checks}
    assert by["motion"].status is Status.PASS and by["frozen_frames"].status is Status.PASS
    golden.check("qg_moving_clip", _summary(rep), request.config)


def test_loudness_bands_on_synthetic_audio(clips, request):
    got = {}
    for a in AUDIO:
        rep = gate.run_gate(clips[f"audio_{a}"])
        c = next(c for c in rep.checks if c.name == "loudness")
        got[a] = {"status": c.status.value, "value": None if c.value is None else round(c.value, 1)}
    assert got["quiet"]["status"] == "fail"  # far under the -14 LUFS target
    golden.check("qg_loudness_bands", got, request.config, abs_=0.6)
