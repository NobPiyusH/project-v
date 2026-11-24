"""One pod shard of a film: rent (lease + kill-timer, fail-closed, ledger cap), run SVI scenes, upscale each beat to 4K, terminate.

Usage (from the repo root):
  python tools/film4k/run_shard.py --dir E:/project-data/outputs/game-4k --name A --scenes 1,4,7 --cap 4.00 --spent-before 0.22
Needs: Docker running (lease database), RUNPOD_API_KEY and HF_TOKEN in .env, keyframes in <dir>/keyframes (sceneNN_*.png),
storyboard in tools/film4k/storyboard.json. SPENDS MONEY: run only with the owner's yes and a cap.
STATUS: rebuilt on podkit on 24 Nov 2026 from the working script (E:\\project-data\\outputs\\game-4k\\film.py). Unit-tested pieces only;
NOT yet run on a real pod: the first real run is a smoke test (one scene) before a full shard.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(Path(__file__).parent)]
from film4k.comfy import Comfy, beat_video_nodes, esrgan_graph  # noqa: E402
from podkit import boot, guard, rent  # noqa: E402
from podkit.ledger import CapExceeded, Ledger  # noqa: E402

from workers.pod import server as ps  # noqa: E402

W, H = 720, 1280
NEG = "cartoon, painting, stylized, oversaturated, neon, glowing beam, morphing, warped, deformed, extra wings, extra limbs, text"
UA = {"User-Agent": "projectv-film4k"}


def say(m: str) -> None:
    print(time.strftime("%H:%M:%S ") + m, flush=True)


def load_env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"')
    return env


def make_gql(key: str):
    def gql(q, v=None):
        req = urllib.request.Request(
            "https://api.runpod.io/graphql?api_key=" + key,
            method="POST",
            data=json.dumps({"query": q, "variables": v or {}}).encode(),
            headers={"Content-Type": "application/json", **UA},
        )
        try:
            return json.loads(urllib.request.urlopen(req, timeout=90).read())
        except Exception as e:  # noqa: BLE001
            return {"errors": [{"message": f"{type(e).__name__} {str(e)[:200]}"}]}

    return gql


def wait_ready(pod_id: str, limit_s: int = 25 * 60) -> bool:
    b8189 = f"https://{pod_id}-8189.proxy.runpod.net"
    b8188 = f"https://{pod_id}-8188.proxy.runpod.net"
    t0, seen = time.time(), 0
    while time.time() - t0 < limit_s:
        time.sleep(20)
        try:
            lines = (
                urllib.request.urlopen(urllib.request.Request(b8189 + "/logs/boot.log", headers=UA), timeout=15)
                .read()
                .decode(errors="replace")
                .splitlines()
            )
        except Exception:  # noqa: BLE001
            continue
        seen = len(lines)
        text = "\n".join(lines)
        if any(m in text for m in boot.BAD_MARKERS):
            say("bad pod (no CUDA or a failed download)")
            return False
        if any(m in text for m in boot.READY_MARKERS):
            for _ in range(16):  # ComfyUI needs a moment after the log says complete: the SVI node must be loaded
                try:
                    info = (
                        urllib.request.urlopen(
                            urllib.request.Request(b8188 + "/object_info/WanImageToVideoSVIPro", headers=UA), timeout=20
                        )
                        .read()
                        .decode()
                    )
                    if "WanImageToVideoSVIPro" in info:
                        return True
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(15)
            return False
    say(f"not ready in time ({seen} log lines)")
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--scenes", required=True)
    ap.add_argument("--cap", type=float, required=True, help="total USD the owner approved for the whole film")
    ap.add_argument("--spent-before", type=float, default=0.0, help="USD already spent on this film outside pods (keyframes)")
    ap.add_argument("--deadline-min", type=int, default=75)
    a = ap.parse_args()
    out = Path(a.dir)
    for d in ("beats720", "beats4k", "work"):
        (out / d).mkdir(parents=True, exist_ok=True)
    scenes = [int(x) for x in a.scenes.split(",")]
    sb = json.loads((Path(__file__).parent / "storyboard.json").read_text(encoding="utf-8"))
    env = load_env()
    gql = make_gql(env["RUNPOD_API_KEY"])
    ledger = Ledger(out / "work" / "ledger.json", a.cap)
    if a.spent_before and not (out / "work" / "ledger.json").exists():
        ledger.add_fixed("keyframes and earlier", a.spent_before)

    if not guard.database_ready():
        sys.exit("The studio database (Docker) is not running: start Docker first. Renting without a lease is not allowed.")
    files = {n: list(v) for n, v in {**ps.FILES, **ps.SVI_FILES}.items()}

    def make_input(cloud: str, gpu: str) -> dict:
        return {
            "cloudType": cloud,
            "gpuCount": 1,
            "gpuTypeId": gpu,
            "name": f"projectv-film4k-{a.name}",
            "imageName": "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04",
            "containerDiskInGb": 130,
            "volumeInGb": 0,
            "minVcpuCount": 4,
            "minMemoryInGb": 48,
            "ports": "8188/http,8189/http",
            "dockerArgs": boot.docker_args(),
            "env": boot.pod_env(env["HF_TOKEN"], ps.KJ_TARBALL, files),
        }

    pod = rent.rent_pod(gql, make_input, say=say)
    if pod is None:
        sys.exit("no GPU in budget (stock gap): retry later")
    state = out / "work" / f"pod_{a.name}.json"
    state.write_text(json.dumps({"id": pod.id, "released": False}))
    try:
        minutes = ledger.max_minutes(pod.cost_per_hr, a.deadline_min)
        if minutes < 20:
            raise CapExceeded(f"only {minutes} min fit under the cap")
        ledger.reserve(pod.id, pod.cost_per_hr, minutes, a.name)
    except CapExceeded as e:
        say(f"cap: {e}")
        rent.terminate(gql, pod.id)
        sys.exit(3)
    ok_lease = guard.start_guards(
        pod.id,
        f"projectv-film4k-{a.name}",
        "film 4k",
        str(state),
        minutes,
        str(ROOT / "tools" / "gpu_lease" / "lease.py"),
        str(ROOT / "tools" / "bakeoff" / "killer.py"),
    )
    say(f"guards started (lease {'confirmed' if ok_lease else 'NOT CONFIRMED'}), deadline {minutes} min")
    try:
        if not ok_lease:
            raise RuntimeError("lease not confirmed: refusing to run an unguarded pod")
        if not wait_ready(pod.id):
            raise RuntimeError("pod not ready")
        say(f"READY after {(time.time() - pod.created) / 60:.1f} min")
        comfy = Comfy(f"https://{pod.id}-8188.proxy.runpod.net")
        for sid in scenes:
            run_scene(comfy, sb, sid, out)
        say(f"ALL SCENES DONE, {(time.time() - pod.created) / 60:.1f} min on pod")
    except Exception as e:  # noqa: BLE001
        say(f"ERROR {type(e).__name__} {str(e)[:300]}")
    finally:
        done = rent.terminate(gql, pod.id)
        state.write_text(json.dumps({"id": pod.id, "released": True}))
        cost = ledger.close(pod.id)
        say(f"terminate sent ok={done}; booked ${cost:.2f}\n{ledger.summary()}")


def run_scene(comfy: Comfy, sb: dict, sid: int, out: Path) -> None:
    sc = next(s for s in sb["scenes"] if s["id"] == sid)
    if all((out / "beats4k" / f"s{sid:02d}_b{k}.mp4").exists() for k in (1, 2, 3)):
        say(f"scene {sid}: already done")
        return
    kf = sorted(p for p in (out / "keyframes").glob(f"scene{sid:02d}_*.png"))[-1]
    anchor = out / "work" / f"anchor{sid:02d}.png"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(kf), "-vf", f"scale={W}:{H}:flags=lanczos", str(anchor), "-y"], check=True)
    comfy.upload(str(anchor), anchor.name)
    c = {
        "width": W,
        "height": H,
        "frames": 81,
        "negative": NEG,
        "beats": [{"prompt": p + ", photoreal, cinematic", "seed": 1000 * sid + i} for i, p in enumerate(sc["beats"])],
    }
    g = beat_video_nodes(ps.scene_graph(c, anchor.name, f"film_s{sid:02d}"), 3)
    for k in (1, 2, 3):
        g[f"w{k}"]["inputs"]["filename_prefix"] = f"video/film_s{sid:02d}_b{k}"
    outs = comfy.run(g, 40 * 60, say, f"scene {sid} svi")
    for k, item in enumerate(comfy.mp4s(outs, ["w1", "w2", "w3"]), 1):
        comfy.fetch(item, str(out / "beats720" / f"s{sid:02d}_b{k}.mp4"))
    for k in (1, 2, 3):  # a failed upscale keeps the 720p beat; the assembler falls back to it
        try:
            name = f"film_s{sid:02d}_b{k}.mp4"
            comfy.upload(str(out / "beats720" / f"s{sid:02d}_b{k}.mp4"), name, "video/mp4")
            res = comfy.run(esrgan_graph(name, f"up4k_s{sid:02d}_b{k}"), 25 * 60, say, f"scene {sid} beat {k} 4K")
            comfy.fetch(comfy.mp4s(res, ["7"])[-1], str(out / "beats4k" / f"s{sid:02d}_b{k}.mp4"))
        except Exception as e:  # noqa: BLE001
            say(f"scene {sid} beat {k}: 4K FAILED {type(e).__name__} {str(e)[:200]} (720p beat kept)")


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
