"""control_from_render: argv shape and the size rules VACE needs (no ffmpeg run)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import control_from_render as C  # noqa: E402


def test_edges_cmd_matches_fake_path_edge_settings():
    cmd = C.control_from_render_cmd("f_%04d.png", "o.mp4", 720, 1280, 81)
    vf = cmd[cmd.index("-vf") + 1]
    assert "edgedetect=low=0.1:high=0.3" in vf and vf.endswith("format=yuv420p")
    assert cmd[cmd.index("-frames:v") + 1] == "81" and cmd[cmd.index("-framerate") + 1] == "16"
    assert cmd[-1] == "o.mp4"


def test_depth_mode_has_no_edgedetect_and_video_has_no_framerate_flag():
    cmd = C.control_from_render_cmd("r.mp4", "o.mp4", 480, 832, 33, mode="depth", image_seq=False)
    assert "edgedetect" not in cmd[cmd.index("-vf") + 1] and "-framerate" not in cmd


@pytest.mark.parametrize("w,h,n", [(721, 1280, 81), (720, 1280, 80), (720, 1281, 81)])
def test_bad_sizes_rejected(w, h, n):
    with pytest.raises(ValueError):
        C.control_from_render_cmd("f_%04d.png", "o.mp4", w, h, n)


def test_bad_mode():
    with pytest.raises(ValueError):
        C.control_from_render_cmd("f_%04d.png", "o.mp4", 720, 1280, 81, mode="x")
