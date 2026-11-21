"""SVI scenes: pod graph + validation, the media worker's scene path, and the laptop assembly (real FFmpeg)."""

import base64
import io
import shutil
import subprocess
import zipfile

import pytest

from workers.activities import clips as C
from workers.dream import scenes as SC
from workers.pod import client, server as S

SCENE = {"image": "eA==", "width": 480, "height": 832, "frames": 81, "fps": 16,
         "beats": [{"prompt": "walk on", "seed": 11}, {"prompt": "turn left", "seed": 12}, {"prompt": "rise", "seed": 13}]}


# ------------------------------------------------------------------------------------------- pod worker

def test_scene_graph_is_the_bench_recipe():
    g = S.scene_graph(SCENE, "a.png", "scene/a")
    # SVI LoRAs stacked at 1.0 on top of LightX2V, then the usual shift-3 samplers
    assert g["30"]["inputs"] == {"model": ["3", 0], "lora_name": S.SVI_HIGH, "strength_model": 1.0}
    assert g["31"]["inputs"] == {"model": ["4", 0], "lora_name": S.SVI_LOW, "strength_model": 1.0}
    assert g["5"]["inputs"]["model"] == ["30", 0] and g["6"]["inputs"]["model"] == ["31", 0]
    assert g["21"] == {"class_type": "VAEEncode", "inputs": {"pixels": ["11", 0], "vae": ["10", 0]}}
    for k in (1, 2, 3):
        c = g[f"c{k}"]
        assert c["class_type"] == "WanImageToVideoSVIPro" and c["inputs"]["anchor_samples"] == ["21", 0]
        assert c["inputs"]["motion_latent_count"] == 3 and c["inputs"]["length"] == 81
        assert g[f"k{k}a"]["inputs"]["steps"] == 4 and g[f"k{k}a"]["inputs"]["cfg"] == 1.0
        assert g[f"k{k}a"]["inputs"]["end_at_step"] == 2 and g[f"k{k}b"]["inputs"]["start_at_step"] == 2
        assert g[f"k{k}a"]["inputs"]["sampler_name"] == "uni_pc_bh2"
        assert g[f"k{k}a"]["inputs"]["noise_seed"] == 10 + k
        assert g[f"l{k}"]["class_type"] == "SaveLatent" and g[f"s{k}"]["inputs"]["filename_prefix"] == f"scene/a_b{k}"
    assert "prev_samples" not in g["c1"]["inputs"], "beat 1 starts from the picture alone"
    assert g["c2"]["inputs"]["prev_samples"] == ["k1b", 0] and g["c3"]["inputs"]["prev_samples"] == ["k2b", 0]
    assert g["c1"]["inputs"]["positive"] == ["8", 0] and g["p2"]["inputs"]["text"] == "turn left", "own prompt per beat"
    assert not {"12", "13", "14", "15", "16"} & set(g), "no plain I2V nodes left"


def test_resumed_scene_continues_from_the_saved_latent():
    c = {**SCENE, "startBeat": 3, "beats": SCENE["beats"][2:], "prevLatent": "eA=="}
    g = S.scene_graph(c, "a.png", "scene/a", "a-prev.latent")
    assert g["22"] == {"class_type": "LoadLatent", "inputs": {"latent": "a-prev.latent"}}
    assert g["c3"]["inputs"]["prev_samples"] == ["22", 0] and "c1" not in g and "c2" not in g
    assert g["c3"]["inputs"]["anchor_samples"] == ["21", 0], "the scene's picture stays the anchor"


def test_scene_validation():
    assert S.validate_scene(SCENE) == []
    assert "no beats" in S.validate_scene({**SCENE, "beats": []})
    assert any("1..5" in e for e in S.validate_scene({**SCENE, "beats": SCENE["beats"] * 2}))
    assert any("prevLatent" in e for e in S.validate_scene({**SCENE, "startBeat": 2}))
    assert any("81" in e for e in S.validate_scene({**SCENE, "frames": 121}))
    assert any("empty prompt" in e for e in S.validate_scene({**SCENE, "beats": [{"prompt": " ", "seed": 1}]}))


def test_scenes_share_the_wan_model_in_the_batch_order():
    ran = []
    jobs = S.Jobs(lambda kind, p: (ran.append(p["n"]) or b"x", {}), clock=lambda: 0.0)
    jobs.submit("clip", {"n": "c1"})
    jobs.work_one(False)  # c1: Wan is loaded
    for kind, n in [("picture", "p1"), ("scene", "s1"), ("clip", "c2")]:
        jobs.submit(kind, {"n": n})
    while jobs.work_one(False):
        pass
    assert ran == ["c1", "s1", "c2", "p1"], "a scene runs on the loaded Wan experts, no swap"


def test_normal_boot_never_fetches_svi(monkeypatch):
    """Plain Shorts keep their boot: the SVI LoRAs and KJNodes are not in the boot download list."""
    assert not set(S.SVI_FILES) & set(S.FILES)
    assert S.Gpu().svi_ready is False
    assert sum(f[3] for f in S.SVI_FILES.values()) == 2 * 1226934120


def test_run_limit_for_scenes():
    assert client.RUN_LIMIT_S["scene"] >= client.RUN_LIMIT_S["clip"]


# ------------------------------------------------------------------------------------------- media worker

def _zip(beats):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k in beats:
            z.writestr(f"beat{k}.mp4", f"MP4-{k}".encode())
            z.writestr(f"beat{k}.latent", f"LAT-{k}".encode())
    return buf.getvalue()


def _job(scene, beat, take=1, **kw):
    return {"shotId": f"s{scene}b{beat}-xxxx", "shotNumber": (scene - 1) * 3 + beat, "take": take, "kind": "dream",
            "mode": "fast", "prompt": f"move {scene}.{beat}", "width": 480, "height": 832, "numFrames": 81,
            "seed": 100 + beat, "imageUri": f"s3://b/scene{scene}.png", "scene": scene, "beat": beat, **kw}


@pytest.fixture
def worker(monkeypatch):
    saved, sent = {}, []
    monkeypatch.setattr(C, "exists", lambda key: key in saved)
    monkeypatch.setattr(C, "get_bytes", lambda uri: saved.get(uri.split("/", 3)[3], b"PNG:" + uri.encode()))
    monkeypatch.setattr(C, "put_bytes", lambda key, data, ct: saved.__setitem__(key, data) or f"s3://b/{key}")
    monkeypatch.setattr(C, "_stop_requested", lambda *a: False)
    monkeypatch.setattr(C, "_post", lambda *a: None)

    def run(url, kind, payload, ready, beat=None):
        sent.append((kind, payload))
        if payload["beats"][0]["prompt"] == "fail":
            raise RuntimeError("ComfyUI error")
        start = payload["startBeat"]
        return _zip(range(start, start + len(payload["beats"]))), {"seconds": 90}
    monkeypatch.setattr(client, "run", run)
    return saved, sent


def test_one_pod_job_per_scene_and_every_beat_saved(worker):
    saved, sent = worker
    jobs = [_job(1, b) for b in (1, 2, 3)] + [_job(2, b) for b in (1, 2, 3)]
    clips, stopped = C._svi({"gpuUrl": "https://g/", "jobs": jobs, "svi": True}, "http://cp", "v")
    assert not stopped and [k for k, _ in sent] == ["scene", "scene"]
    p = sent[0][1]
    assert [b["prompt"] for b in p["beats"]] == ["move 1.1", "move 1.2", "move 1.3"] and p["startBeat"] == 1
    assert base64.b64decode(p["image"]) == b"PNG:s3://b/scene1.png" and "prevLatent" not in p
    assert base64.b64decode(sent[1][1]["image"]) == b"PNG:s3://b/scene2.png"
    assert all(c["uri"] and c["gpuSeconds"] == 30 for c in clips) and len(clips) == 6
    assert saved["videos/v/clips/dream/shot002-take1.latent"] == b"LAT-2"
    assert saved["videos/v/clips/dream/shot002-take1.mp4"] == b"MP4-2"


def test_redo_from_a_beat_resumes_from_the_earlier_beats_latent(worker):
    saved, sent = worker
    saved["videos/v/clips/dream/shot001-take1.latent"] = b"OLD-LAT-1"
    jobs = [_job(1, 2, take=2, chainFromUri="s3://b/videos/v/clips/dream/shot001-take1.mp4"), _job(1, 3, take=2)]
    clips, _ = C._svi({"gpuUrl": "https://g/", "jobs": jobs}, "http://cp", "v")
    p = sent[0][1]
    assert p["startBeat"] == 2 and len(p["beats"]) == 2
    assert base64.b64decode(p["prevLatent"]) == b"OLD-LAT-1"
    assert [c["take"] for c in clips] == [2, 2] and all(c["uri"] for c in clips)


def test_retry_reuses_saved_beats_and_renders_only_the_rest(worker):
    saved, sent = worker
    saved["videos/v/clips/dream/shot001-take1.mp4"] = b"M1"
    saved["videos/v/clips/dream/shot001-take1.latent"] = b"L1"
    clips, _ = C._svi({"gpuUrl": "https://g/", "jobs": [_job(1, b) for b in (1, 2, 3)]}, "http://cp", "v")
    p = sent[0][1]
    assert p["startBeat"] == 2 and base64.b64decode(p["prevLatent"]) == b"L1"
    assert clips[0]["gpuSeconds"] == 0 and clips[0]["uri"].endswith("shot001-take1.mp4")


def test_a_failed_scene_fails_only_its_own_beats(worker):
    _, sent = worker
    jobs = [_job(1, 1, prompt="fail"), _job(1, 2)] + [_job(2, 1)]
    clips, _ = C._svi({"gpuUrl": "https://g/", "jobs": jobs}, "http://cp", "v")
    assert [bool(c["uri"]) for c in clips] == [False, False, True]
    assert "ComfyUI error" in clips[0]["error"] and len(sent) == 2


def test_a_later_beat_without_an_earlier_one_fails_clearly(worker):
    clips, _ = C._svi({"gpuUrl": "https://g/", "jobs": [_job(1, 2)]}, "http://cp", "v")
    assert clips[0]["uri"] is None and "no earlier beat" in clips[0]["error"]


def test_svi_never_runs_on_modal(monkeypatch):
    monkeypatch.setattr(C, "_post", lambda *a: None)
    with pytest.raises(RuntimeError, match="pod only"):
        C.generate_clips({"videoId": "v", "svi": True, "jobs": [_job(1, 1)]})


def test_stop_between_scenes(worker, monkeypatch):
    calls = iter([False, True])
    monkeypatch.setattr(C, "_stop_requested", lambda *a: next(calls))
    clips, stopped = C._svi({"gpuUrl": "https://g/", "jobs": [_job(1, 1), _job(2, 1)]}, "http://cp", "v")
    assert stopped and clips[1]["error"] == "stopped"


# ------------------------------------------------------------------------------------------- laptop assembly

def test_beats_for_scene_lengths():
    assert [SC.beats_for(s) for s in (10, 15, 20)] == [3, 4, 5]
    assert SC.scene_frames(5) == 353 and SC.scene_frames(4) == 285 and SC.scene_frames(1) == 81


def test_join_graph_matches_the_bench():
    g = SC.join_graph(5, 321)
    assert "[0:v]trim=start_frame=0:end_frame=69" in g
    assert "[0:v]trim=start_frame=69:end_frame=81,setpts=PTS-STARTPTS[t1]" in g
    assert "[1:v]trim=start_frame=1:end_frame=13,setpts=PTS-STARTPTS[h1]" in g, "anchor frame 0 dropped"
    assert "[4:v]trim=start_frame=13:end_frame=81" in g, "the last beat keeps its tail"
    assert "blend=all_expr='A*(1-((N+1)/13))+B*((N+1)/13)'" in g and "concat=n=9" in g and "end_frame=321" in g
    assert "eq=" not in g and "saturation" not in g, "no colour correction by default"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs FFmpeg")
def test_assemble_real_ffmpeg(tmp_path):
    srcs = []
    for k in range(3):
        p = tmp_path / f"b{k}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x{40 * k + 20:02x}2040:s=64x112:r=16",
                        "-frames:v", "81", "-pix_fmt", "yuv420p", str(p)], check=True)
        srcs.append(p)
    clips = [{"uri": "u", "fps": 16, "scene": f"s{k}"} for k in range(3)]
    out, oc = SC.assemble(srcs, clips, [2, 1], [161, 0], tmp_path)
    n = lambda p: int(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries",  # noqa: E731
                                      "stream=nb_read_frames", "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout.strip())
    assert n(out[0]) == 149, "2 beats = 81 + 68 frames (shorter than the 161 asked: nothing is invented)"
    assert n(out[1]) == 81 and [c["scene"] for c in oc] == ["s0", "s2"]
    with pytest.raises(ValueError):
        SC.assemble(srcs, clips, [2, 2], None, tmp_path)
