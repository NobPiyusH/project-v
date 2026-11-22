"""Diagram lane: spec validation, text-card fallback, the clips activity routing, and one real (short) Manim render."""

import copy
import json
import shutil
import subprocess

import pytest

from workers.activities import clips as C
from workers.diagram import render as R
from workers.diagram.spec import fallback_spec, validate_spec
from workers.diagram.templates import TEMPLATES

JOB = {"shotId": "abcdef12-0000", "shotNumber": 3, "take": 1, "kind": "draft", "mode": "diagram", "imageUri": None,
       "prompt": "", "width": 1920, "height": 1080, "numFrames": 144, "seed": 1}


@pytest.mark.parametrize("name", list(TEMPLATES))
def test_templates_validate(name):
    assert validate_spec(TEMPLATES[name]()) == []


def test_fallback_validates_and_moves():
    spec = fallback_spec("How a CRT works", ["Electrons are fired", "Magnets steer them"], 7)
    assert validate_spec(spec) == []
    assert any(s["do"] == "show" for s in spec["steps"]) and spec["fallback"]


def test_validation_messages_are_specific():
    bad = copy.deepcopy(TEMPLATES["rgb-colour-mixing"]())
    bad["duration"] = 30
    bad["steps"][0]["target"] = "nope"
    bad["steps"][1]["do"] = "teleport"
    bad["elements"][1]["x"] = 99
    errs = " | ".join(validate_spec(bad))
    assert "duration" in errs and "'nope'" in errs and "teleport" in errs and "outside the frame" in errs
    assert validate_spec("x") == ["spec must be a JSON object"]
    dup = {"duration": 6, "elements": [{"id": "a", "type": "text", "x": 0, "y": 0, "text": "t"}] * 2}
    assert any("duplicated" in e for e in validate_spec(dup))


def test_flow_only_on_lines():
    spec = {"duration": 6, "elements": [{"id": "b", "type": "box", "x": 0, "y": 0, "w": 1, "h": 1}],
            "steps": [{"at": 0, "dur": 1, "do": "flow", "target": "b"}]}
    assert any("flow" in e for e in validate_spec(spec))


def test_invalid_spec_falls_back_to_text_card(monkeypatch, tmp_path):
    rendered = []
    monkeypatch.setattr(R, "render_to_file", lambda spec, out, timeout=900, **kw: rendered.append(spec))
    res = R.render_or_fallback({"duration": 6, "elements": []}, tmp_path / "o.mp4", title="CRT", lines=["a"])
    assert res["fallback"] and "invalid spec" in res["reason"]
    assert rendered[0]["fallback"] and rendered[0]["title"] == "CRT"


def test_failed_render_falls_back_once(monkeypatch, tmp_path):
    calls = []

    def fake(spec, out, timeout=900, **kw):
        calls.append(spec)
        if not spec.get("fallback"):
            raise RuntimeError("manim exploded")
    monkeypatch.setattr(R, "render_to_file", fake)
    res = R.render_or_fallback(TEMPLATES["rgb-colour-mixing"](), tmp_path / "o.mp4")
    assert res["fallback"] and "manim exploded" in res["reason"] and len(calls) == 2


def test_clips_activity_renders_diagrams_and_passes_rest(monkeypatch):
    saved, posts, passed = {}, [], {}
    monkeypatch.setattr(C, "_post", lambda url, body: posts.append(body))
    monkeypatch.setattr(C, "_stop_requested", lambda base, vid: False)
    monkeypatch.setattr(C, "put_file", lambda key, path, ct: saved.setdefault(key, f"s3://b/{key}"))
    monkeypatch.setattr(R, "render_or_fallback", lambda spec, out, title="", duration=6.0, lines=None, **kw: (out.write_bytes(b"mp4"),
                                                                                            {"fallback": False, "reason": None})[1])
    monkeypatch.setattr(C, "_generate_clips", lambda req: passed.setdefault("req", req) and
                        {"clips": [{"shotId": "x", "uri": "s3://b/x"}], "containers": 1, "stopped": False})
    dj = {**JOB, "prompt": json.dumps(TEMPLATES["rgb-colour-mixing"]())}
    ai = {**JOB, "shotId": "ffff0000-1", "shotNumber": 4, "mode": "fast", "imageUri": "s3://b/k.png", "width": 832, "height": 480}
    r = C.generate_clips({"videoId": "vid", "jobs": [dj, ai], "provider": "wan"})
    assert [j["shotNumber"] for j in passed["req"]["jobs"]] == [4]
    assert r["clips"][0]["mode"] == "diagram" and r["clips"][0]["uri"].endswith("shot003-take1.mp4")
    assert len(r["clips"]) == 2


def test_diagram_only_batch_never_touches_a_gpu(monkeypatch):
    monkeypatch.setattr(C, "_post", lambda url, body: None)
    monkeypatch.setattr(C, "_stop_requested", lambda base, vid: False)
    monkeypatch.setattr(C, "put_file", lambda key, path, ct: f"s3://b/{key}")
    monkeypatch.setattr(R, "render_or_fallback", lambda spec, out, title="", duration=6.0, lines=None, **kw: (out.write_bytes(b"m"),
                                                                                            {"fallback": True, "reason": "bad"})[1])
    monkeypatch.setattr(C, "_generate_clips", lambda req: pytest.fail("no GPU/provider call for diagram-only batches"))
    r = C.generate_clips({"videoId": "v", "jobs": [{**JOB, "prompt": "not json"}]})
    assert r["clips"][0]["uri"] and r["containers"] == 0


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe missing")
def test_real_render_is_1080p_24fps(tmp_path):
    pytest.importorskip("manim")
    spec = fallback_spec("Real render", ["one", "two"], 5)
    out = R.render_to_file(spec, tmp_path / "t.mp4")
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,r_frame_rate,pix_fmt,duration", "-of", "json", str(out)],
                           capture_output=True, text=True, check=True)
    st = json.loads(probe.stdout)["streams"][0]
    assert (st["width"], st["height"], st["r_frame_rate"], st["pix_fmt"]) == (1920, 1080, "24/1", "yuv420p")
    assert abs(float(st["duration"]) - 5.0) < 0.2


def test_diagram_size_is_passed_to_the_renderer(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(R, "render_to_file", lambda spec, out, timeout=900, width=0, height=0: seen.update(w=width, h=height))
    R.render_or_fallback(TEMPLATES["rgb-colour-mixing"](), tmp_path / "o.mp4", width=2560, height=1440)
    assert (seen["w"], seen["h"]) == (2560, 1440)


def _active(steps, t):
    return any(s["at"] <= t < s["at"] + s["dur"] for s in steps)


def test_enrich_motion_keeps_the_directors_steps_and_leaves_no_second_still():
    from workers.diagram.spec import enrich_motion, validate_spec

    spec = {"version": 1, "title": "t", "duration": 8.0,
            "elements": [{"id": "gun", "type": "box", "x": -3, "y": 0, "w": 2, "h": 1, "text": "Gun"},
                         {"id": "beam", "type": "beam", "x1": -2, "y1": 0, "x2": 3, "y2": 0},
                         {"id": "head", "type": "text", "x": 0, "y": 3, "text": "Title"}],
            "steps": [{"at": 0.2, "dur": 1.0, "do": "show", "target": "gun"}]}
    assert validate_spec(spec) == []
    out = enrich_motion(spec)
    assert spec["steps"] == [{"at": 0.2, "dur": 1.0, "do": "show", "target": "gun"}]  # the input is not changed
    assert out["steps"][0] == spec["steps"][0]  # the Director's step is kept first
    assert any(s["do"] == "flow" and s["target"] == "beam" for s in out["steps"])
    assert any(s["do"] == "pulse" and s["target"] == "gun" for s in out["steps"])
    assert all(_active(out["steps"], t / 2) for t in range(1, 15)), "something must move every half second"
    assert next(e for e in out["elements"] if e["id"] == "head")["size"] == 40  # one title size
    assert out["steps"] == enrich_motion(spec)["steps"]  # deterministic


def test_enrich_motion_shows_something_at_the_start_when_everything_is_hidden():
    from workers.diagram.spec import enrich_motion

    spec = {"version": 1, "title": "t", "duration": 6.0,
            "elements": [{"id": "a", "type": "text", "x": 0, "y": 1, "text": "A"}, {"id": "b", "type": "circle", "x": 0, "y": 0, "r": 1}],
            "steps": [{"at": 1.0, "dur": 1.0, "do": "show", "target": "a"}, {"at": 2.0, "dur": 1.0, "do": "show", "target": "b"}]}
    out = enrich_motion(spec)
    assert any(e.get("visible") for e in out["elements"])


def test_enrich_motion_drops_the_bottom_caption_the_video_burns_its_own_captions_there():
    from workers.diagram.spec import enrich_motion

    spec = {"version": 1, "title": "t", "duration": 6.0,
            "elements": [{"id": "box", "type": "box", "x": 0, "y": 0, "w": 2, "h": 1},
                         {"id": "cap", "type": "text", "x": 0, "y": -3.4, "text": "bottom caption"}],
            "steps": [{"at": 0.5, "dur": 0.5, "do": "show", "target": "cap"}]}
    out = enrich_motion(spec)
    assert [e["id"] for e in out["elements"]] == ["box"]
    assert all(s["target"] != "cap" for s in out["steps"])
