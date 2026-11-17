"""Animatic: shots on the timeline, cut at the planned length, with on-screen text that includes '%' and ':'."""

import shutil
from pathlib import Path

import pytest

from workers.activities import animatic as A
from workers.common import run_ffmpeg


def test_the_animatic_never_runs_past_the_planned_length(monkeypatch, tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setattr(A.activity, "heartbeat", lambda *a: None)
    monkeypatch.setattr(A, "download",lambda uri, dest: Path(shutil.copy(uri, dest)))
    monkeypatch.setattr(A, "put_file", lambda key, path, mime: str(Path(shutil.copy(path, store / Path(key).name))))
    img = tmp_path / "k.png"
    run_ffmpeg(["-f", "lavfi", "-i", "color=c=navy:s=768x1376", "-frames:v", "1", str(img)])
    # 1.02 s at 30 fps rounds up to 31 frames each: three shots make 3.1 s against a 3.0 s plan unless cut.
    shots = [{"index": i, "startSec": i * 1.0, "durationSec": 1.02, "imageUri": str(img),
              "overlayText": "90% of it: dark" if i == 1 else None} for i in range(3)]
    out = A.render_animatic({"videoId": "v1", "title": "t", "shots": shots, "narration": [], "musicUri": None,
                             "totalSec": 3.0, "aspectRatio": "9:16"})
    assert out["durationSec"] == pytest.approx(3.0, abs=0.01)
