"""Fun Camera on the pod (25 Nov 2026): the worker's copy of the presets and graph must equal tools/film4k, and the media worker must
send the preset. No GPU, no network."""

import base64
import sys
from pathlib import Path

import pytest

from workers.activities import clips as C
from workers.pod import client
from workers.pod import server as S

FILM4K = Path(__file__).resolve().parents[2] / "tools" / "film4k"


@pytest.fixture(scope="module")
def film4k():
    sys.path.insert(0, str(FILM4K))
    import camera_graphs
    import camera_presets

    return camera_presets, camera_graphs


def test_the_pod_copy_of_the_presets_equals_the_tested_tools_module(film4k):
    presets, _ = film4k
    fun = {n: (p.pose, p.speed) for n, p in presets.PRESETS.items() if p.engine == "fun_camera"}
    assert fun == S.CAMERA_PRESETS
    assert S.CAMERA_TESTED == presets.TESTED


def test_the_pod_copy_of_the_graph_equals_fun_camera_graph_apart_from_the_save_node(film4k):
    _, graphs = film4k
    assert (S.CAMERA_UNETS, S.CAMERA_LORAS, S.CAMERA_TEXT) == (graphs.CAMERA_UNETS, graphs.I2V_LORAS, graphs.TEXT)
    c = {"camera": "pan_left", "prompt": "a moon", "seed": 7, "width": 720, "height": 1280, "frames": 81}
    mine = S.camera_graph(c, "in.png", "clip/x", {})
    theirs = graphs.fun_camera_graph({}, "pan_left", "in.png", "a moon", "x", 720, 1280, 81, 7)
    for node in [k for k in theirs if k not in ("16", "16v")]:
        assert mine[node] == theirs[node], node
    assert mine["16"]["class_type"] == "SaveImage" and mine["16"]["inputs"]["images"] == ["15", 0]


def test_the_camera_files_match_the_podkit_group_names_and_sizes():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "podkit"))
    from groups import COMMON, FUN_CAMERA

    expected = {**FUN_CAMERA, S.CAMERA_TEXT: COMMON[S.CAMERA_TEXT]}
    assert {n: (path, sub, size) for n, (_, path, sub, size) in S.CAMERA_FILES.items()} == \
        {n: (path, sub, size) for n, (_, path, sub, size) in expected.items()}


def clip(**kw):
    return {"prompt": "p", "image": "x", "seed": 1, "width": 720, "height": 1280, "frames": 81, "fps": 16, **kw}


def test_a_tested_preset_is_accepted_an_untested_one_needs_allow_untested_and_nonsense_is_refused():
    assert S.validate_clip(clip(camera="zoom_in")) == []
    assert S.validate_clip(clip(camera="pan_down")) and "not tested" in S.validate_clip(clip(camera="pan_down"))[0]
    assert S.validate_clip(clip(camera="pan_down", allowUntested=True)) == []
    assert "camera must be one of" in S.validate_clip(clip(camera="spin"))[0]
    assert S.validate_clip(clip(camera="zoom_in", endImage="x")), "first-last frame does not combine with a camera preset"
    assert S.validate_clip(clip()) == [], "no camera: the SVI/plain graph route is unchanged"


def test_720_by_1280_is_inside_the_pods_pixel_limit():
    assert S.validate_clip(clip(camera="slow_push")) == []


def test_the_media_worker_sends_the_camera_preset_to_the_pod(monkeypatch):
    sent = []
    monkeypatch.setattr(C, "get_bytes", lambda uri: b"pic")
    monkeypatch.setattr(C, "put_bytes", lambda key, data, ct: f"s3://b/{key}")
    monkeypatch.setattr(C, "_stop_requested", lambda *a: False)
    monkeypatch.setattr(C, "_post", lambda *a: None)
    monkeypatch.setattr(C, "exists", lambda key: False)
    monkeypatch.setattr(C, "last_frame_png", lambda mp4, clean=False: b"last:" + mp4)

    def run(url, kind, payload, ready, beat=None):
        sent.append({**payload, "image": base64.b64decode(payload["image"])})
        return b"mp4", {"seconds": 200, "frames": 81, "fps": 16}

    monkeypatch.setattr(client, "run", run)
    jobs = [{"shotId": f"abcdefg{n}-1", "shotNumber": n, "take": 1, "kind": "draft", "mode": "camera", "imageUri": f"s3://b/p{n}.png",
             "prompt": "x", "width": 720, "height": 1280, "numFrames": 81, "seed": n, "camera": cam} for n, cam in ((1, "zoom_in"), (2, "pan_left"), (3, None))]
    C._pod({"gpuUrl": "https://g.example/", "chain": True, "chainGroup": 2, "jobs": jobs}, "http://x", "v")
    assert [p.get("camera") for p in sent] == ["zoom_in", "pan_left", None]
    assert [p["image"] for p in sent] == [b"pic", b"last:mp4", b"pic"], "beat 2 continues from beat 1; beat 3 restarts from its own keyframe"
    assert sent[0]["allowUntested"] is True and "allowUntested" not in sent[2]
