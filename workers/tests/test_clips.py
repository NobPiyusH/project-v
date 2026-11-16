"""M2b clip runner with a fake Modal: clips are saved as they finish, and Stop cancels the remaining GPU calls."""

from types import SimpleNamespace

import pytest

from workers.activities import clips as C


class FakeCall:
    def __init__(self, job, ready_after):
        self.job, self.polls, self.ready_after, self.cancelled = job, 0, ready_after, False

    def get(self, timeout=0):
        self.polls += 1
        if self.polls < self.ready_after:
            raise TimeoutError()  # what Modal raises while the clip is still rendering
        return {"mp4": b"MP4", "seconds": 61.5, "frames": 49, "fps": 16, "mode": self.job["mode"], "error": None}

    def cancel(self):
        self.cancelled = True


@pytest.fixture()
def fake(monkeypatch):
    calls, saved, posts = [], {}, []
    stop = {"after": None, "checks": 0}

    def spawn(job):
        calls.append(FakeCall(job, ready_after=1 if len(calls) == 0 else 99))
        return calls[-1]

    wan = SimpleNamespace(generate_one=SimpleNamespace(spawn=spawn))
    monkeypatch.setattr(C.modal.Cls, "from_name", lambda app, cls: (lambda: wan))
    monkeypatch.setattr(C.modal.Dict, "from_name", lambda name, create_if_missing=False: {})
    monkeypatch.setattr(C, "get_bytes", lambda uri: b"PNG")
    monkeypatch.setattr(C, "put_bytes", lambda key, data, mime: saved.setdefault(key, f"s3://projectv/{key}"))
    monkeypatch.setattr(C, "_post", lambda url, body: posts.append(body))
    monkeypatch.setattr(C.time, "sleep", lambda s: None)

    def stop_requested(base, video_id):
        stop["checks"] += 1
        return stop["after"] is not None and stop["checks"] > stop["after"]

    monkeypatch.setattr(C, "_stop_requested", stop_requested)
    return SimpleNamespace(calls=calls, saved=saved, posts=posts, stop=stop)


def _req(n):
    return {"videoId": "a0a3ed6a-0000", "modalApp": "projectv-wan", "modalClass": "Wan", "progressUrl": "http://cp",
            "jobs": [{"shotId": f"shot-{i}", "shotNumber": i, "take": 1, "kind": "draft", "mode": "fast",
                      "imageUri": f"s3://b/{i}.png", "prompt": "p", "width": 480, "height": 832, "numFrames": 49, "seed": i}
                     for i in range(1, n + 1)]}


def test_stop_keeps_finished_clips_and_cancels_the_rest(fake):
    fake.stop["after"] = 2          # stop on the third check, after the first clip has finished
    out = C.generate_clips(_req(2))
    by_shot = {c["shotId"]: c for c in out["clips"]}
    assert by_shot["shot-1"]["uri"] == "s3://projectv/videos/a0a3ed6a-0000/clips/draft/shot001-take1.mp4"
    assert by_shot["shot-1"]["gpuSeconds"] == 61.5
    assert by_shot["shot-2"]["uri"] is None and by_shot["shot-2"]["error"] == "stopped"
    assert fake.calls[1].cancelled, "no GPU keeps running after Stop"
    assert out["stopped"] is True and out["containers"] == 2
    assert fake.posts[-1].get("finished") is True


def test_a_modal_failure_is_reported_per_clip(fake, monkeypatch):
    class Boom(FakeCall):
        def get(self, timeout=0):
            raise C.modal.exception.FunctionTimeoutError("clip ran past its time limit")

    monkeypatch.setattr(fake, "calls", [])
    wan = SimpleNamespace(generate_one=SimpleNamespace(spawn=lambda job: Boom(job, 1)))
    monkeypatch.setattr(C.modal.Cls, "from_name", lambda app, cls: (lambda: wan))
    out = C.generate_clips(_req(1))
    assert out["clips"][0]["uri"] is None
    assert "FunctionTimeoutError" in out["clips"][0]["error"]
    assert out["stopped"] is False
