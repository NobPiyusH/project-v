"""Salad worker + client: no GPU, no network beyond localhost."""

import json
import threading
import urllib.request

import pytest

from workers.activities import clips as C
from workers.salad import client, server as S


def test_dry_run_ok(capsys):
    assert S.main(["--dry-run"]) == 0
    assert "dry run OK" in capsys.readouterr().out


def test_expert_split_4_steps():
    assert S.expert_split(4) == 2


def test_clip_graph_matches_proven_settings():
    g = S.clip_graph({"prompt": "p", "seed": 7, "width": 480, "height": 832, "frames": 81, "negative": "no dogs"}, "a.png", "clip/a")
    assert g["13"]["inputs"]["steps"] == 4 and g["13"]["inputs"]["cfg"] == 1.0 and g["13"]["inputs"]["end_at_step"] == 2
    assert g["14"]["inputs"]["start_at_step"] == 2 and g["13"]["inputs"]["sampler_name"] == "uni_pc_bh2"
    assert g["1"]["inputs"]["unet_name"] == S.UNET_HIGH and g["12"]["inputs"]["length"] == 81
    assert "no dogs" in g["9"]["inputs"]["text"] and g["11"]["inputs"]["image"] == "a.png"


def test_validation():
    ok = {"prompt": "x", "image": "b64", "seed": 1, "width": 480, "height": 832, "frames": 81, "fps": 16}
    assert S.validate_clip(ok) == []
    assert S.validate_clip({**ok, "frames": 80})
    assert S.validate_clip({**ok, "width": 1920, "height": 1088})
    assert S.validate_picture({"prompt": "x", "width": 768, "height": 1344, "seed": 3}) == []
    assert S.validate_picture({"prompt": "", "width": 770})


def test_jobs_queue_runs_and_isolates_failure():
    def runner(kind, p):
        if p.get("boom"):
            raise ValueError("bad")
        return b"DATA", {"frames": 1}
    jobs = S.Jobs(runner)
    a, b = jobs.submit("picture", {}), jobs.submit("picture", {"boom": 1})
    assert jobs.work_one(False) and jobs.work_one(False)
    assert jobs.get(a)["state"] == "done" and jobs.result(a) == (b"DATA", "image/png")
    assert jobs.get(b)["state"] == "failed" and "bad" in jobs.get(b)["error"] and jobs.result(b) is None


@pytest.fixture()
def live_worker():
    state = S.State()
    state.jobs = S.Jobs(lambda kind, p: (b"MP4" if kind == "clip" else b"PNG", {"frames": 81, "fps": 16}))
    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), S.make_handler(state))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield state, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_http_not_ready_then_ready_roundtrip(live_worker):
    state, base = live_worker
    assert client.health(base)["ready"] is False
    with pytest.raises(client.NotReady):
        client.run_once(base, "picture", {"prompt": "x", "width": 768, "height": 1344, "seed": 1})
    state.ready, state.phase = True, "ready"
    threading.Thread(target=lambda: [state.jobs.work_one() for _ in range(2)], daemon=True).start()
    data, info = client.run_once(base, "picture", {"prompt": "x", "width": 768, "height": 1344, "seed": 1}, sleep=lambda s: None)
    assert data == b"PNG" and info["state"] == "done"
    req = urllib.request.Request(f"{base}/clip", data=b"{}", method="POST")
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_run_retries_when_instance_lost(monkeypatch):
    calls = {"n": 0}

    def fake_once(base, kind, payload, beat=None, sleep=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise client.NotReady("job vanished")
        return b"OK", {"seconds": 5}
    monkeypatch.setattr(client, "health", lambda base: {"ready": True})
    monkeypatch.setattr(client, "run_once", fake_once)
    data, info = client.run("http://x", "clip", {}, 10)
    assert data == b"OK" and info["retries"] == 1


def test_run_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr(client, "health", lambda base: {"ready": True})
    monkeypatch.setattr(client, "run_once", lambda *a, **k: (_ for _ in ()).throw(client.NotReady("gone")))
    with pytest.raises(RuntimeError, match="after 2 retries"):
        client.run("http://x", "clip", {}, 10)


def test_wait_ready_fails_fast_on_boot_failure_and_times_out(monkeypatch):
    monkeypatch.setattr(client, "health", lambda b: {"ready": False, "phase": "failed", "error": "disk full"})
    with pytest.raises(RuntimeError, match="disk full"):
        client.wait_ready("http://x", 5, sleep=lambda s: None)
    monkeypatch.setattr(client, "health", lambda b: {"ready": False, "phase": "downloading"})
    t = iter(range(0, 1000, 100))
    with pytest.raises(RuntimeError, match="not ready"):
        client.wait_ready("http://x", 150, sleep=lambda s: None, clock=lambda: next(t))


def test_clips_activity_salad_path(monkeypatch):
    monkeypatch.setattr(C, "get_bytes", lambda uri: b"png")
    monkeypatch.setattr(C, "put_bytes", lambda key, data, ct: f"s3://b/{key}")
    monkeypatch.setattr(C, "_stop_requested", lambda *a: False)
    monkeypatch.setattr(C, "_post", lambda *a: None)
    sent = []
    monkeypatch.setattr(client, "run", lambda url, kind, payload, ready, beat=None: (sent.append((url, kind, payload)) or (b"mp4", {"seconds": 70, "frames": 81, "fps": 16})))
    job = {"shotId": "abcdefgh-1", "shotNumber": 1, "take": 1, "kind": "dream", "mode": "fast", "imageUri": "s3://b/k.png",
           "prompt": "walk", "width": 480, "height": 832, "numFrames": 81, "seed": 5}
    out = C.generate_clips.__wrapped__({"videoId": "v", "provider": "salad", "gpuUrl": "https://g.example/", "jobs": [job]}) \
        if hasattr(C.generate_clips, "__wrapped__") else None
    if out is None:
        clips, stopped = C._salad({"gpuUrl": "https://g.example/", "jobs": [job]}, "http://127.0.0.1:8080", "v")
        out = {"clips": clips, "stopped": stopped}
    assert out["clips"][0]["uri"].endswith("shot001-take1.mp4") and out["clips"][0]["gpuSeconds"] == 70
    assert sent[0][0] == "https://g.example" and sent[0][1] == "clip" and sent[0][2]["frames"] == 81


def test_volume_cache_uses_complete_copy_only(tmp_path):
    vol = str(tmp_path)
    assert S.volume_file("", "a.safetensors", 3) is None            # no volume configured
    assert S.volume_file(vol, "a.safetensors", 3) is None           # nothing cached yet
    src = tmp_path / "src.bin"
    src.write_bytes(b"abc")
    S.copy_to_volume(vol, "a.safetensors", src)
    assert S.volume_file(vol, "a.safetensors", 3) == tmp_path / "wan-fp8" / "a.safetensors"
    assert S.volume_file(vol, "a.safetensors", 4) is None           # wrong size = incomplete copy: not used
