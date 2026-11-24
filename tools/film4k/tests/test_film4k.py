"""film4k: camera move, join filter, RIFE counts, ESRGAN graph, storyboard shape. Pure functions; no ffmpeg, no pods."""

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import camera  # noqa: E402
import comfy  # noqa: E402


def test_every_scene_pushes_in_and_never_zooms_out():
    """scale=eval=frame with a shrinking size crashed ffmpeg (scene 6, 24 Nov): the zoom must only grow."""
    for sid in range(1, 16):
        f = camera.camera_filter(sid)
        assert "eval=frame" in f
        assert f"(1+{camera.ZOOM}*n/226)" in f
        assert "1.16-" not in f and "(1-" not in f


def test_scenes_differ_by_drift_and_tilt():
    assert camera.move(1)["dx"] == 60 and camera.move(2)["dx"] == -60 and camera.move(3)["dx"] == 0
    assert camera.move(4)["dy"] == 160 and camera.move(1)["dy"] == 0


def test_join_filter_cross_fades_at_the_beat_boundaries():
    f = camera.join_filter()
    assert "offset=4.5625" in f and "offset=9.1250" in f
    assert f.count("xfade") == 2


def test_rife_target_gives_48_fps_at_1_25x_speed():
    assert camera.rife_target(227) == 545
    assert camera.rife_target(227, speed=1.0) == 681


def test_esrgan_graph_upscales_in_three_slices_and_joins_them():
    g = comfy.esrgan_graph("clip.mp4", "up")
    assert [g[f"a{j}"]["inputs"]["batch_index"] for j in range(3)] == [0, 27, 54]
    assert sum(g[f"a{j}"]["inputs"]["length"] for j in range(3)) == 81
    assert g["d2"]["inputs"]["image1"] == ["d1", 0] and g["6"]["inputs"]["images"] == ["d2", 0]
    assert g["7"]["inputs"]["filename_prefix"] == "video/up"
    assert g["c0"]["inputs"]["width"] == 2160 and g["c0"]["inputs"]["height"] == 3840


def test_beat_video_nodes_swap_png_saves_for_mp4_saves():
    g = {"s1": {}, "l1": {}, "d1": {"class_type": "VAEDecode"}}
    out = comfy.beat_video_nodes(g, 1)
    assert "s1" not in out and "l1" not in out
    assert out["w1"]["class_type"] == "SaveVideo" and out["v1"]["inputs"]["images"] == ["d1", 0]


def test_storyboard_has_fifteen_scenes_of_three_beats():
    sb = json.loads((HERE / "storyboard.json").read_text(encoding="utf-8"))
    assert [s["id"] for s in sb["scenes"]] == list(range(1, 16))
    for s in sb["scenes"]:
        assert len(s["beats"]) == 3
        assert set(re.findall(r"\{(\w+)\}", s["key"])) <= {"c"}
    assert "{c}" not in sb["creature"]
