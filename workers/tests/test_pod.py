import os
"""Pod worker + client: no GPU, no network beyond localhost."""

import json
import threading
import urllib.request

import pytest

from workers.activities import clips as C
from workers.pod import client, server as S
from workers.activities import keyframes as K


@pytest.fixture(autouse=True)
def _nothing_saved_yet(monkeypatch):
    """Storage is never touched by these tests: by default no take is saved yet."""
    monkeypatch.setattr(C, "exists", lambda key: False)
    monkeypatch.setattr(K, "exists", lambda key: False)


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


def test_first_last_graph_swaps_only_the_conditioning_node():
    c = {"prompt": "p", "seed": 7, "width": 480, "height": 832, "frames": 81}
    plain, flf = S.clip_graph(c, "a.png", "clip/a"), S.clip_graph(c, "a.png", "clip/a", "a-end.png")
    assert "17" not in plain and plain["12"]["class_type"] == "WanImageToVideo"
    assert flf["12"]["class_type"] == "WanFirstLastFrameToVideo"
    assert flf["12"]["inputs"]["start_image"] == ["11", 0] and flf["12"]["inputs"]["end_image"] == ["17", 0]
    assert flf["17"] == {"class_type": "LoadImage", "inputs": {"image": "a-end.png"}}
    assert {k: v for k, v in flf.items() if k not in ("12", "17")} == {k: v for k, v in plain.items() if k != "12"}, \
        "same experts, LoRAs, steps and sampler: no new weights"


def test_clips_pod_path_sends_the_end_picture(monkeypatch):
    monkeypatch.setattr(C, "get_bytes", lambda uri: b"END" if uri.endswith("p2.png") else b"png")
    monkeypatch.setattr(C, "put_bytes", lambda key, data, ct: f"s3://b/{key}")
    monkeypatch.setattr(C, "_stop_requested", lambda *a: False)
    monkeypatch.setattr(C, "_post", lambda *a: None)
    sent = []
    monkeypatch.setattr(client, "run", lambda url, kind, payload, ready, beat=None: (sent.append(payload) or (b"mp4", {"seconds": 70})))
    base = {"kind": "dream", "mode": "fast", "prompt": "walk", "width": 480, "height": 832, "numFrames": 81, "seed": 5, "take": 1}
    jobs = [{**base, "shotId": "aaaaaaaa-1", "shotNumber": 1, "imageUri": "s3://b/p1.png", "endImageUri": "s3://b/p2.png"},
            {**base, "shotId": "bbbbbbbb-2", "shotNumber": 2, "imageUri": "s3://b/p2.png"}]
    clips, _ = C._pod({"gpuUrl": "https://g.example/", "jobs": jobs}, "http://127.0.0.1:8080", "v")
    import base64
    assert base64.b64decode(sent[0]["endImage"]) == b"END" and "endImage" not in sent[1]
    assert all(c["uri"] for c in clips)


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

    def fake_once(base, kind, payload, beat=None, sleep=None, clock=None):
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


def test_clips_activity_pod_path(monkeypatch):
    monkeypatch.setattr(C, "get_bytes", lambda uri: b"png")
    monkeypatch.setattr(C, "put_bytes", lambda key, data, ct: f"s3://b/{key}")
    monkeypatch.setattr(C, "_stop_requested", lambda *a: False)
    monkeypatch.setattr(C, "_post", lambda *a: None)
    sent = []
    monkeypatch.setattr(client, "run", lambda url, kind, payload, ready, beat=None: (sent.append((url, kind, payload)) or (b"mp4", {"seconds": 70, "frames": 81, "fps": 16})))
    job = {"shotId": "abcdefgh-1", "shotNumber": 1, "take": 1, "kind": "dream", "mode": "fast", "imageUri": "s3://b/k.png",
           "prompt": "walk", "width": 480, "height": 832, "numFrames": 81, "seed": 5}
    out = C.generate_clips.__wrapped__({"videoId": "v", "provider": "pod", "gpuUrl": "https://g.example/", "jobs": [job]}) \
        if hasattr(C.generate_clips, "__wrapped__") else None
    if out is None:
        clips, stopped = C._pod({"gpuUrl": "https://g.example/", "jobs": [job]}, "http://127.0.0.1:8080", "v")
        out = {"clips": clips, "stopped": stopped}
    assert out["clips"][0]["uri"].endswith("shot001-take1.mp4") and out["clips"][0]["gpuSeconds"] == 70
    assert sent[0][0] == "https://g.example" and sent[0][1] == "clip" and sent[0][2]["frames"] == 81


class _Resp:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _control_plane(monkeypatch, pod_states, replace_url="https://new.example"):
    """Fake control plane: heartbeat answers the next pod state; replace hands out a new pod URL."""
    calls = {"replace": 0}

    def urlopen(req, timeout=None):
        if req.full_url.endswith("/replace"):
            calls["replace"] += 1
            return _Resp(json.dumps({"url": replace_url, "readyTimeoutSeconds": 900}).encode())
        return _Resp(json.dumps({"ok": True, "pod": pod_states.pop(0) if pod_states else "OPEN"}).encode())
    monkeypatch.setattr(client.urllib.request, "urlopen", urlopen)
    return calls


def test_lease_beat_raises_when_pod_gone_or_released(monkeypatch):
    _control_plane(monkeypatch, ["GONE", "NONE"])
    monkeypatch.setattr(client, "health", lambda b: {"ready": False})
    t = iter([0, 100, 200])
    beat = client.lease_beat("http://cp", "v", clock=lambda: next(t), pod_url=lambda: "https://old")
    with pytest.raises(client.PodGone) as gone:
        beat({})
    assert not gone.value.released
    with pytest.raises(client.PodGone) as rel:
        beat({})
    assert rel.value.released


def test_lease_beat_ignores_gone_while_the_worker_still_answers(monkeypatch):
    _control_plane(monkeypatch, ["GONE"])
    monkeypatch.setattr(client, "health", lambda b: {"ready": True})
    client.lease_beat("http://cp", "v", pod_url=lambda: "https://old")({})  # no raise: a status blip never re-rents


def test_rented_replaces_a_vanished_pod_once_and_resubmits(monkeypatch):
    calls = _control_plane(monkeypatch, [])
    urls = []

    def fake_run(url, kind, payload, ready, beat=None):
        urls.append((url, ready))
        if len(urls) == 1:
            raise client.PodGone("deleted")
        return b"MP4", {"seconds": 70}
    monkeypatch.setattr(client, "run", fake_run)
    pod = client.Rented("http://cp", "v", "https://old/", 1800)
    assert pod.run("clip", {}) == (b"MP4", {"seconds": 70})
    assert urls == [("https://old", 1800), ("https://new.example", 900)] and calls["replace"] == 1


def test_rented_replaces_a_pod_that_never_became_ready(monkeypatch):
    """21 Nov 2026: a pod never became ready and the step just failed. Now the broker retires it and rents another."""
    bodies = []
    calls = {"replace": 0}

    def urlopen(req, timeout=None):
        if req.full_url.endswith("/replace"):
            calls["replace"] += 1
            bodies.append(json.loads(req.data))
            return _Resp(json.dumps({"url": "https://new.example", "readyTimeoutSeconds": 900}).encode())
        return _Resp(json.dumps({"ok": True, "pod": "OPEN"}).encode())
    monkeypatch.setattr(client.urllib.request, "urlopen", urlopen)
    urls = []

    def fake_run(url, kind, payload, ready, beat=None):
        urls.append(url)
        if url == "https://old":
            raise client.NeverReady("Pod worker not ready after 30 min (last phase: downloading)")
        return b"MP4", {"seconds": 70}
    monkeypatch.setattr(client, "run", fake_run)
    pod = client.Rented("http://cp", "v", "https://old/", 1800)
    assert pod.run("scene", {}) == (b"MP4", {"seconds": 70})
    assert urls == ["https://old", "https://new.example"] and calls["replace"] == 1
    assert bodies[0]["unreadyUrl"] == "https://old" and "not ready" in bodies[0]["why"]


def test_wait_ready_timeout_is_never_ready():
    t = iter(range(0, 1000, 100))
    with pytest.raises(client.NeverReady):
        client.wait_ready("http://127.0.0.1:9", 150, sleep=lambda s: None, clock=lambda: next(t))


def test_rented_second_loss_or_release_fails_fast_without_renting(monkeypatch):
    calls = _control_plane(monkeypatch, [])
    n = {"run": 0}

    def gone(*a, **k):
        n["run"] += 1
        raise client.PodGone("deleted")
    monkeypatch.setattr(client, "run", gone)
    pod = client.Rented("http://cp", "v", "https://old", 1800)
    with pytest.raises(RuntimeError, match="already replaced once"):
        pod.run("clip", {})
    with pytest.raises(RuntimeError, match="GPU pod lost"):
        pod.run("clip", {})  # the next clip fails at once, no 30-minute wait
    assert calls["replace"] == 1 and n["run"] == 2

    monkeypatch.setattr(client, "run", lambda *a, **k: (_ for _ in ()).throw(client.PodGone("ended", released=True)))
    pod2 = client.Rented("http://cp", "v", "https://old", 1800)
    with pytest.raises(RuntimeError, match="GPU pod lost: ended$"):
        pod2.run("clip", {})
    assert calls["replace"] == 1, "a released rental is never rented again"


def test_volume_cache_uses_complete_copy_only(tmp_path):
    vol = str(tmp_path)
    assert S.volume_file("", "a.safetensors", 3) is None            # no volume configured
    assert S.volume_file(vol, "a.safetensors", 3) is None           # nothing cached yet
    src = tmp_path / "src.bin"
    src.write_bytes(b"abc")
    S.copy_to_volume(vol, "a.safetensors", src)
    assert S.volume_file(vol, "a.safetensors", 3) == tmp_path / "wan-fp8" / "a.safetensors"
    assert S.volume_file(vol, "a.safetensors", 4) is None           # wrong size = incomplete copy: not used


def test_copy_tree_plain_copies_contents_and_follows_links(tmp_path):
    src = tmp_path / "snap"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("a")
    (tmp_path / "blob").write_text("blob")
    try:
        (src / "sub" / "link.txt").symlink_to(tmp_path / "blob")
    except OSError:
        pytest.skip("symlinks need a privilege on this Windows account")
    S.copy_tree_plain(src, tmp_path / "out")
    assert (tmp_path / "out" / "a.txt").read_text() == "a"
    assert (tmp_path / "out" / "sub" / "link.txt").read_text() == "blob"


def test_keyframes_activity_pod_picture_job(monkeypatch):
    """Java marks pod picture jobs provider 'gpu_pod' (DreamService.POD): they must reach the pod, not be dropped."""
    from workers.activities import keyframes as K

    monkeypatch.setattr(K, "put_bytes", lambda key, data, ct: f"s3://b/{key}")
    monkeypatch.setattr(K, "activity_beat", lambda d: None)
    monkeypatch.setattr(client, "lease_beat", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(client, "run", lambda url, kind, payload, ready, beat=None: (sent.append((url, kind, payload)) or (b"png", {"seconds": 9})))
    job = {"shotId": "s1", "attempt": 1, "provider": "gpu_pod", "prompt": "a dream", "width": 768, "height": 1344, "seed": 4}
    out = K.generate_keyframes.__wrapped__({"videoId": "v", "gpuUrl": "https://g.example/", "jobs": [job]}) \
        if hasattr(K.generate_keyframes, "__wrapped__") else None
    if out is None:
        results: list = []
        secs = K._pod_pictures({"videoId": "v", "gpuUrl": "https://g.example/"}, [job], results)
        out = {"results": results, "podGpuSeconds": secs}
    assert K.POD_PROVIDER == "gpu_pod"
    assert sent and sent[0][0] == "https://g.example" and sent[0][1] == "picture"
    r = out["results"][0]
    assert r["uri"].endswith("s1-1.png") and r["provider"] == "gpu_pod" and r["error"] is None
    assert out["podGpuSeconds"] == 9


def test_activity_filter_keeps_gpu_pod_jobs(monkeypatch):
    from workers.activities import keyframes as K

    seen = []
    monkeypatch.setattr(K, "_pod_pictures", lambda req, jobs, results: (seen.extend(jobs) or 0.0))
    fn = getattr(K.generate_keyframes, "__wrapped__", K.generate_keyframes)
    fn({"videoId": "v", "jobs": [{"shotId": "a", "provider": "gpu_pod"}, {"shotId": "b", "provider": "pod"}]})
    assert [j["shotId"] for j in seen] == ["a"]


def test_worker_touches_heartbeat_on_requests_and_boot(tmp_path, monkeypatch, live_worker):
    hb = tmp_path / "hb"
    monkeypatch.setattr(S, "HEARTBEAT", str(hb))
    state, base = live_worker
    state.ready = True
    assert not hb.exists()
    urllib.request.urlopen(base + "/health", timeout=5).read()
    assert hb.exists()
    hb.unlink()
    S.touch_heartbeat()
    assert hb.exists()
    hb.unlink()
    st = S.State()
    st.ready = True  # boot finished: the loop must exit without touching
    S.boot_heartbeat(st, interval=0)
    assert not hb.exists()
    st2 = S.State()
    t = threading.Thread(target=S.boot_heartbeat, args=(st2, 0.01), daemon=True)
    t.start()
    import time
    time.sleep(0.1)
    assert hb.exists()
    st2.error = "x"
    t.join(2)
    assert not t.is_alive()


def test_heartbeat_path_matches_java_guard():
    java = (__import__("pathlib").Path(__file__).parents[2] / "control-plane/src/main/java/com/projectv/gpu/PodSelfStop.java").read_text()
    assert f'HEARTBEAT = "{S.HEARTBEAT}"' in java


def test_unfiltered_finish_look_adds_no_grading_haze_or_grain():
    from workers.dream import finish as F

    assert F.look_filter("unfiltered") == "null"                       # ffmpeg's pass-through filter
    assert "gblur" in F.look_filter("dreamcore")                       # (an unknown look would fall back to this haze)
    g = F.dissolve_graph([5.0, 5.0], 0, 1080, 1920, 24, "unfiltered")
    assert "[x]null,format=yuv420p[v]" in g and "noise" not in g and "curves" not in g


def _job_states(monkeypatch, states):
    """Fakes the pod: submit answers 202, each poll answers the next state, the result answers bytes."""
    seq = iter(states)

    def call(url, body=None, timeout=60):
        if body is not None:
            return 202, json.dumps({"id": "j1"}).encode()
        if url.endswith("/result"):
            return 200, b"PNG"
        return 200, json.dumps({"state": next(seq)}).encode()
    monkeypatch.setattr(client, "_call", call)


def test_queued_time_never_counts_against_the_run_limit(monkeypatch):
    # 3 h in the queue behind other Shorts' clips, then a 1-minute run: the picture is fine.
    _job_states(monkeypatch, ["queued"] * 5 + ["running", "running", "done"])
    t = iter([3 * 3600, 3 * 3600, 3 * 3600 + 60])  # the clock is read only once the job runs
    data, _ = client.run_once("http://x", "picture", {}, sleep=lambda s: None, clock=lambda: next(t))
    assert data == b"PNG"


def test_a_job_running_past_its_limit_fails(monkeypatch):
    _job_states(monkeypatch, ["queued", "running", "running"])
    t = iter([0, 0, client.RUN_LIMIT_S["picture"] + 1])
    with pytest.raises(client.JobFailed, match="ran 10 min"):
        client.run_once("http://x", "picture", {}, sleep=lambda s: None, clock=lambda: next(t))


def test_retry_reuses_saved_pictures_and_renders_only_the_rest(monkeypatch):
    monkeypatch.setattr(K, "exists", lambda key: key.endswith("s1-1.png"))
    monkeypatch.setattr(K, "put_bytes", lambda key, data, ct: f"s3://b/{key}")
    monkeypatch.setattr(K, "activity_beat", lambda d: None)
    monkeypatch.setattr(client, "lease_beat", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(client, "run", lambda url, kind, payload, ready, beat=None: (sent.append(payload) or (b"png", {"seconds": 9})))
    jobs = [{"shotId": s, "attempt": 1, "provider": "gpu_pod", "prompt": s, "seed": 4} for s in ("s1", "s2")]
    results: list = []
    secs = K._pod_pictures({"videoId": "v", "gpuUrl": "https://g.example/"}, jobs, results)
    assert [p["prompt"] for p in sent] == ["s2"] and secs == 9
    assert results[0]["uri"].endswith("videos/v/keyframes/s1-1.png") and results[0]["reused"]


def test_retry_reuses_saved_clips_and_chains_from_them(monkeypatch):
    saved = "videos/v/clips/dream/shot001-take1.mp4"
    monkeypatch.setattr(C, "exists", lambda key: key == saved)
    monkeypatch.setattr(C, "get_bytes", lambda uri: b"OLDMP4" if uri.endswith(saved) else b"png")
    monkeypatch.setattr(C, "put_bytes", lambda key, data, ct: f"s3://b/{key}")
    monkeypatch.setattr(C, "_stop_requested", lambda *a: False)
    monkeypatch.setattr(C, "_post", lambda *a: None)
    monkeypatch.setattr(C, "last_frame_png", lambda mp4, clean=False: b"LAST:" + mp4)
    sent = []
    monkeypatch.setattr(client, "run", lambda url, kind, payload, ready, beat=None: (sent.append(payload) or (b"mp4", {"seconds": 70})))
    base = {"kind": "dream", "mode": "fast", "prompt": "walk", "width": 480, "height": 832, "numFrames": 81, "seed": 5, "take": 1}
    jobs = [{**base, "shotId": "aaaaaaaa-1", "shotNumber": 1, "imageUri": "s3://b/p1.png"},
            {**base, "shotId": "bbbbbbbb-2", "shotNumber": 2}]
    clips, _ = C._pod({"gpuUrl": "https://g.example/", "jobs": jobs, "chain": True}, "http://127.0.0.1:8080", "v")
    import base64
    assert len(sent) == 1 and base64.b64decode(sent[0]["image"]) == b"LAST:OLDMP4"
    assert clips[0]["uri"].endswith(saved) and clips[0]["gpuSeconds"] == 0 and clips[1]["uri"]


def test_batch_order_keeps_the_loaded_model_busy_before_swapping():
    ran = []
    t = [0.0]
    jobs = S.Jobs(lambda kind, p: (ran.append(p["n"]) or b"x", {}), clock=lambda: t[0])
    # Two Shorts interleave: pictures of one, a clip of the other, more pictures, another clip.
    for kind, n in [("picture", "p1"), ("clip", "c1"), ("picture", "p2"), ("clip", "c2"), ("picture", "p3")]:
        jobs.submit(kind, {"n": n})
    while jobs.work_one(False):
        pass
    assert ran == ["p1", "p2", "p3", "c1", "c2"]  # one swap instead of four


def test_batch_order_never_starves_the_other_kind():
    ran = []
    t = [0.0]
    jobs = S.Jobs(lambda kind, p: (ran.append(p["n"]) or b"x", {}), clock=lambda: t[0])
    jobs.submit("clip", {"n": "c1"})
    jobs.work_one(False)                      # Wan is loaded now
    jobs.submit("picture", {"n": "p1"})
    jobs.submit("clip", {"n": "c2"})
    jobs.work_one(False)                      # c2: stays on Wan
    jobs.submit("clip", {"n": "c3"})
    t[0] = S.Jobs.SWITCH_AFTER_S + 1          # p1 has waited long enough
    jobs.work_one(False)
    assert ran == ["c1", "c2", "p1"]


def test_retry_chain_keeps_earlier_frames_when_a_saved_clip_is_unreadable(monkeypatch):
    saved2 = "videos/v/clips/dream/shot002-take1.mp4"
    monkeypatch.setattr(C, "exists", lambda key: key == saved2)

    def get(uri):
        if uri.endswith(saved2):
            raise OSError("unreadable")
        return b"png"
    monkeypatch.setattr(C, "get_bytes", get)
    monkeypatch.setattr(C, "put_bytes", lambda key, data, ct: f"s3://b/{key}")
    monkeypatch.setattr(C, "_stop_requested", lambda *a: False)
    monkeypatch.setattr(C, "_post", lambda *a: None)
    monkeypatch.setattr(C, "last_frame_png", lambda mp4, clean=False: b"LAST:" + mp4)
    sent = []
    monkeypatch.setattr(client, "run", lambda url, kind, payload, ready, beat=None: (sent.append(payload) or (b"NEWMP4", {"seconds": 70})))
    base = {"kind": "dream", "mode": "fast", "prompt": "walk", "width": 480, "height": 832, "numFrames": 81, "seed": 5, "take": 1}
    jobs = [{**base, "shotId": "aaaaaaaa-1", "shotNumber": 1, "imageUri": "s3://b/p1.png"},
            {**base, "shotId": "bbbbbbbb-2", "shotNumber": 2}]
    clips, _ = C._pod({"gpuUrl": "https://g.example/", "jobs": jobs, "chain": True}, "http://127.0.0.1:8080", "v")
    import base64
    assert len(sent) == 2, "shot 1 is rendered, and so is shot 2 (its saved file could not be read)"
    assert base64.b64decode(sent[1]["image"]) == b"LAST:NEWMP4", "shot 2 still starts from shot 1's last frame"
    assert all(c["uri"] for c in clips)


def test_picture_model_defaults_to_zimage_and_rejects_unknown():
    base = {"prompt": "x", "width": 768, "height": 1344, "seed": 1}
    assert S.validate_picture(base) == []
    assert S.validate_picture({**base, "model": "zimage"}) == []
    assert S.validate_picture({**base, "model": "flux2-klein"}) == []
    assert S.validate_picture({**base, "model": "nope"})


def test_flux_graph_matches_comfy_template():
    g = S.flux_graph({"prompt": "a fox", "seed": 9, "width": 768, "height": 1344}, "pic/a")
    assert g["1"]["inputs"]["unet_name"] == "flux-2-klein-4b.safetensors"
    assert g["2"]["inputs"] == {"clip_name": "qwen_3_4b.safetensors", "type": "flux2", "device": "default"}
    assert g["3"]["inputs"]["vae_name"] == "flux2-vae.safetensors" and g["4"]["inputs"]["text"] == "a fox"
    assert g["6"]["inputs"]["cfg"] == 1.0 and g["8"]["inputs"] == {"steps": 4, "width": 768, "height": 1344}
    assert g["9"]["inputs"]["noise_seed"] == 9 and g["10"]["inputs"]["width"] == 768 and g["13"]["inputs"]["filename_prefix"] == "pic/a"
    assert set(S.FLUX_FILES) == {"flux-2-klein-4b.safetensors", "qwen_3_4b.safetensors", "flux2-vae.safetensors"}


def test_gpu_routes_by_model_and_keeps_flux_loaded(monkeypatch):
    gpu = S.Gpu()
    calls = []
    monkeypatch.setattr(gpu, "picture", lambda p: calls.append("zimage") or (b"z", {}))
    monkeypatch.setattr(gpu, "flux_picture", lambda p: calls.append("flux") or (b"f", {}))
    gpu.run("picture", {"prompt": "x"})
    gpu.run("picture", {"prompt": "x", "model": "flux2-klein"})
    assert calls == ["zimage", "flux"]


def test_flux_fetches_once_and_never_frees_comfy(monkeypatch, tmp_path):
    gpu = S.Gpu(tmp_path, S.State())
    fetched, freed, graphs = [], [], []
    monkeypatch.setattr(S, "fetch_files", lambda *a: fetched.append(a[2]))
    monkeypatch.setattr(S, "ROOT", tmp_path)
    (tmp_path / "comfy" / "out" / "pic").mkdir(parents=True)
    (tmp_path / "comfy" / "out" / "pic" / "x.png").write_bytes(b"PNG")
    monkeypatch.setattr(gpu, "_free_comfy", lambda: freed.append(1))
    monkeypatch.setattr(gpu, "_run_graph", lambda g: graphs.append(g) or {"outputs": {"13": {"images": [{"subfolder": "pic", "filename": "x.png"}]}}})
    for _ in range(2):
        (tmp_path / "comfy" / "out" / "pic" / "x.png").write_bytes(b"PNG")
        assert gpu.flux_picture({"prompt": "p", "seed": 1})[0] == b"PNG"
    assert fetched == [S.FLUX_FILES] and not freed and len(graphs) == 2


# ---------------------------------------------------------------------------- per-rental bearer token (Vast.ai, RunPod)

def _authed_worker(token="s3cret-token"):
    state = S.State()
    state.ready, state.phase = True, "ready"
    state.jobs = S.Jobs(lambda kind, p: (b"PNG", {}))
    srv = S.ThreadingHTTPServer(("127.0.0.1", 0), S.make_handler(state, token))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return state, srv, srv.server_address[1]


def test_authorized_helper():
    assert S.authorized(None, "")                       # no token set: open, as before
    assert S.authorized("Bearer abc", "abc")
    assert not S.authorized(None, "abc")
    assert not S.authorized("Bearer abd", "abc")
    assert not S.authorized("abc", "abc")               # must be the Bearer scheme
    assert not S.authorized("Bearer ", "abc")


def test_worker_with_token_rejects_everything_but_live():
    state, srv, port = _authed_worker()
    try:
        base = f"http://127.0.0.1:{port}"
        assert urllib.request.urlopen(base + "/live", timeout=5).status == 200   # bare liveness, open
        for method, path in (("GET", "/health"), ("GET", "/ready"), ("GET", "/"), ("GET", "/jobs/x"), ("POST", "/picture"),
                             ("POST", "/clip"), ("POST", "/scene")):
            req = urllib.request.Request(base + path, data=b"{}" if method == "POST" else None, method=method)
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req, timeout=5)
            assert e.value.code == 401, path
        wrong = urllib.request.Request(base + "/health", headers={"Authorization": "Bearer nope"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(wrong, timeout=5)
        assert e.value.code == 401
    finally:
        srv.shutdown()


def test_unauthorized_request_does_not_touch_heartbeat(tmp_path, monkeypatch):
    hb = tmp_path / "hb"
    monkeypatch.setattr(S, "HEARTBEAT", str(hb))
    state, srv, port = _authed_worker()
    try:
        base = f"http://127.0.0.1:{port}"
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/health", timeout=5)
        assert not hb.exists()                           # a scanner must not keep the rented GPU alive
        ok = urllib.request.Request(base + "/health", headers={"Authorization": "Bearer s3cret-token"})
        assert urllib.request.urlopen(ok, timeout=5).status == 200
        assert hb.exists()
    finally:
        srv.shutdown()


def test_client_sends_token_from_url_userinfo_and_runs_a_job():
    state, srv, port = _authed_worker()
    try:
        threading.Thread(target=lambda: [state.jobs.work_one() for _ in range(1)], daemon=True).start()
        base = f"http://worker:s3cret-token@127.0.0.1:{port}"
        assert client.health(base)["ready"] is True
        data, info = client.run_once(base, "picture", {"prompt": "x", "width": 768, "height": 1344, "seed": 1}, sleep=lambda s: None)
        assert data == b"PNG"
        bad = client.health(f"http://worker:wrong@127.0.0.1:{port}")
        assert bad["ready"] is False and bad["phase"] == "failed"   # a pod that refuses our token is never waited for
    finally:
        srv.shutdown()


def test_split_token():
    assert client.split_token("http://worker:abc123@1.2.3.4:41000") == ("http://1.2.3.4:41000", "abc123")
    assert client.split_token("https://id-8000.proxy.runpod.net/health") == ("https://id-8000.proxy.runpod.net/health", None)
    assert client.split_token("http://worker:tok@[::1]:8000/x?y=1") == ("http://[::1]:8000/x?y=1", "tok")


def test_worker_token_is_taken_out_of_the_environment():
    import os
    import subprocess
    import sys
    from pathlib import Path
    src = Path(S.__file__)
    code = ("import os,sys,importlib.util;"
            f"s=importlib.util.spec_from_file_location('w',r'{src}');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "print(m.WORKER_TOKEN, 'SV_TOKEN' in os.environ)")
    out = subprocess.run([sys.executable, "-c", code], env={**os.environ, "SV_TOKEN": "abc"}, capture_output=True, text=True)
    assert out.stdout.split() == ["abc", "False"], out.stderr


def test_weight_downloads_retry_and_fall_back_to_plain_http(monkeypatch, tmp_path):
    import sys
    import types
    seen = []
    constants = types.SimpleNamespace(HF_HUB_DISABLE_XET=False)  # read once at import in the real library

    def hf_hub_download(repo, path, local_dir):
        seen.append(constants.HF_HUB_DISABLE_XET)
        if len(seen) < 3:
            raise RuntimeError("File reconstruction error: Internal Writer Error: Failed to send data: receiver dropped")
        f = tmp_path / path
        f.write_bytes(b"w")
        return str(f)
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(hf_hub_download=hf_hub_download,
                                                                               constants=constants))
    logs = []
    monkeypatch.setattr(S, "log", logs.append)
    sleeps = []
    assert S.hf_download("r", "a.safetensors", str(tmp_path), sleep=sleeps.append).name == "a.safetensors"
    assert seen == [False, False, True], "xet off only on the last try, on the library's own switch"
    assert sleeps == [5, 10] and constants.HF_HUB_DISABLE_XET is False
    assert any("try 3/3 (plain HTTP" in m for m in logs)


def test_weight_download_gives_up_after_three_tries(monkeypatch, tmp_path):
    import sys
    import types
    n = []
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(
        constants=types.SimpleNamespace(HF_HUB_DISABLE_XET=False), hf_hub_download=lambda *a, **k: n.append(1) or (_ for _ in ()).throw(OSError("down"))))
    monkeypatch.setattr(S, "log", lambda m: None)
    with pytest.raises(OSError):
        S.hf_download("r", "a", str(tmp_path), sleep=lambda s: None)
    assert len(n) == 3


def test_xet_is_off_before_huggingface_hub_is_imported():
    import subprocess
    import sys
    code = ("import os, sys; os.environ.pop('HF_HUB_DISABLE_XET', None); import workers.pod.server as S; "
            "assert 'huggingface_hub' not in sys.modules, 'imported too early'; "
            "assert os.environ['HF_HUB_DISABLE_XET'] == '1'; "
            "from huggingface_hub import constants; assert constants.HF_HUB_DISABLE_XET is True; print('ok')")
    env = {k: v for k, v in os.environ.items() if k != "HF_HUB_DISABLE_XET"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.stdout.strip() == "ok", out.stderr


def test_hf_transfer_only_when_already_installed():
    e = {}
    S.hub_env(e, has_hf_transfer=False)
    assert e == {"HF_HUB_DISABLE_XET": "1"}
    S.hub_env(e, has_hf_transfer=True)
    assert e["HF_HUB_ENABLE_HF_TRANSFER"] == "1"
    e2 = {"HF_HUB_DISABLE_XET": "0"}
    S.hub_env(e2, has_hf_transfer=False)
    assert e2["HF_HUB_DISABLE_XET"] == "0", "an explicit setting on the pod wins"


def test_fetch_files_retries_each_weight(monkeypatch, tmp_path):
    calls = []

    def flaky(repo, path, local_dir, tries=S.DOWNLOAD_TRIES, sleep=None):
        calls.append(path)
        f = tmp_path / path
        f.write_bytes(b"abc")
        return f
    monkeypatch.setattr(S, "hf_download", flaky)
    monkeypatch.setattr(S, "ROOT", tmp_path)
    monkeypatch.setattr(S, "VOLUME", "")
    monkeypatch.setattr(S, "log", lambda m: None)
    links = []
    monkeypatch.setattr(S.Path, "symlink_to", lambda self, target: links.append((self.name, target.name)))  # Windows
    comfy = tmp_path / "comfy"
    S.fetch_files(comfy, S.State(), {"a.safetensors": ("r", "a.safetensors", "unet", 3)}, "x")
    assert calls == ["a.safetensors"], "every weight goes through the retrying download"
    assert links == [("a.safetensors", "a.safetensors")]
