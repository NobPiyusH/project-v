"""Gemini Omni clip path: request shape, audio dropped, tokens reported, failures contained (mocked, no spend)."""

import base64
from types import SimpleNamespace

from workers.activities import clips as C

JOB = {"shotId": "s1-aaaaaaaa", "shotNumber": 2, "take": 1, "kind": "draft", "imageUri": "s3://b/k.png",
       "prompt": "Rapid descent", "width": 720, "height": 1280, "numFrames": 120, "mode": "omni"}


def _patch(monkeypatch, create):
    monkeypatch.setattr(C, "get_bytes", lambda uri: b"png")
    monkeypatch.setattr(C, "gemini", lambda: SimpleNamespace(interactions=SimpleNamespace(create=create)))
    monkeypatch.setattr(C, "run_ffmpeg", lambda args: open(args[-1], "wb").write(b"silent"))
    saved = {}
    monkeypatch.setattr(C, "put_file", lambda key, path, ct: saved.setdefault("uri", f"s3://b/{key}"))
    return saved


def test_omni_clip_request_and_result(monkeypatch):
    seen = {}

    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(output_video=SimpleNamespace(data=base64.b64encode(b"mp4").decode()),
                               usage=SimpleNamespace(total_output_tokens=28960))
    saved = _patch(monkeypatch, create)
    r = C._omni_one("gemini-omni-flash-preview", "vid", JOB, "720p")
    assert seen["response_format"] == {"type": "video", "duration": "5s", "resolution": "720p", "aspect_ratio": "9:16"}
    assert seen["input"][0]["type"] == "image" and "Rapid descent" in seen["input"][1]["text"]
    assert r["uri"] == saved["uri"] and r["outputTokens"] == 28960 and r["gpuSeconds"] == 0 and r["fps"] == 24


def test_omni_failure_is_contained(monkeypatch):
    def create(**kw):
        raise RuntimeError("boom")
    _patch(monkeypatch, create)
    r = C._omni_one("m", "vid", JOB, "720p")
    assert r["uri"] is None and "boom" in r["error"] and r["outputTokens"] == 0
