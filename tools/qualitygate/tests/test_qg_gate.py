"""End-to-end on a tiny synthetic clip made by ffmpeg itself (skipped when ffmpeg is missing). Free, local, a few seconds."""

import json
import shutil
import subprocess

import pytest

from tools.qualitygate import gate
from tools.qualitygate.__main__ import main
from tools.qualitygate.types import Status

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    d = tmp_path_factory.mktemp("qg")
    out = d / "clip.mp4"
    # 2 s moving test pattern, then 2 s of black; quiet sine audio
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=16:d=2", "-f", "lavfi", "-i", "color=black:s=320x180:r=16:d=2",
         "-f", "lavfi", "-i", "sine=f=440:d=4", "-filter_complex", "[0][1]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a", "-af", "volume=0.5",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(out)],
        check=True,
    )  # fmt: skip
    (d / "shots.json").write_text(json.dumps([{"id": "move", "start": 0, "end": 2}, {"id": "dark", "start": 2, "end": 4}]))
    return d


def test_gate_finds_black_and_static_shot(clip):
    rep = gate.run_gate(str(clip / "clip.mp4"), str(clip / "shots.json"))
    by = {(c.name, c.shot): c for c in rep.checks}
    assert by[("loudness", None)].status in (Status.WARN, Status.FAIL)  # quiet sine, about -27 LUFS
    assert by[("black_frames", None)].status is Status.FAIL
    assert by[("motion", "move")].status is Status.PASS
    assert by[("motion", "dark")].status is Status.FAIL
    assert rep.status is Status.FAIL and rep.judge == []


def test_cli_json_and_exit_code(clip, capsys):
    code = main([str(clip / "clip.mp4"), "--json-only"])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "fail"


def test_cli_judge_on_needs_backend(clip):
    assert main([str(clip / "clip.mp4"), "--judge", "on"]) == 2
