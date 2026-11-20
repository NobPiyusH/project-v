"""Project V GPU worker for a rented GPU pod (RunPod, Jarvislabs, ... via com.projectv.gpu.broker) (Dream Shorts pictures + Wan clips).

Self-contained on purpose: the control plane ships THIS file to the container through env vars (SV_BOOT_00..NN,
zlib + base64; see com.projectv.gpu.broker.PodBoot) on the public image pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime,
so no private registry and no image build. Only the standard library is imported at module level; GPU libraries are
imported on the box after `boot()` installed them.

At start: install deps, fetch ComfyUI (pinned tag), download weights from Hugging Face (Apache-2.0; CLAUDE.md rule 9)
onto the container disk, start ComfyUI on 127.0.0.1. Serves on [::]:PORT (IPv6, for the Container Gateway):
  GET  /health              200 {"ready": true} once ready, else 503 {"ready": false, "phase": ...}
  POST /picture             {prompt, seed, width, height}                         -> 202 {"id": job}
  POST /clip                {image: b64 PNG, prompt, negative, seed, frames, fps, width, height} -> 202 {"id": job}
  GET  /jobs/<id>           {"state": queued|running|done|failed, "seconds", "error", ...}
  GET  /jobs/<id>/result    the PNG or MP4 bytes (404 until done)
Jobs are async: the gateway's timeouts are capped and a first clip can take minutes. One GPU job at a time.

  python server.py --dry-run      # no GPU, no download: print the plan, build a clip graph, exit 0
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

COMFY_TAG = "v0.45.0"
COMFY_TARBALL = f"https://github.com/comfyanonymous/ComfyUI/archive/refs/tags/{COMFY_TAG}.tar.gz"
ROOT = Path(os.environ.get("SV_ROOT", "/data"))
PORT = int(os.environ.get("SV_PORT", "8000"))
COMFY_PORT = 8188

# Same files as the fp8 benchmark (workers/bench/wan_fp8, checked on Hugging Face 20 Nov 2026; sizes in bytes).
COMFY_REPO = "Comfy-Org/Wan-2.2"
LORA_REPO = "lightx2v/Wan2.2-Distill-Loras"
UNET_HIGH = "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
UNET_LOW = "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
TEXT_ENCODER = "umt5_xxl_fp16.safetensors"
VAE = "wan_2.1_vae.safetensors"
LORA_HIGH = "wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors"
LORA_LOW = "wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors"
FILES = {  # name: (repo, path in repo, ComfyUI models subfolder, size)
    UNET_HIGH: (COMFY_REPO, f"split_files/diffusion_models/{UNET_HIGH}", "diffusion_models", 14294742832),
    UNET_LOW: (COMFY_REPO, f"split_files/diffusion_models/{UNET_LOW}", "diffusion_models", 14294742832),
    TEXT_ENCODER: (COMFY_REPO, f"split_files/text_encoders/{TEXT_ENCODER}", "text_encoders", 11366399385),
    VAE: (COMFY_REPO, f"split_files/vae/{VAE}", "vae", 253815318),
    LORA_HIGH: (LORA_REPO, LORA_HIGH, "loras", 634645944),
    LORA_LOW: (LORA_REPO, LORA_LOW, "loras", 739472104),
}
ZIMAGE_REPO = "Tongyi-MAI/Z-Image-Turbo"
PIP = ["huggingface_hub>=0.34", "diffusers>=0.36.0", "transformers>=4.57.0", "accelerate>=1.10", "safetensors",
       "sentencepiece", "pillow", "imageio-ffmpeg"]
NEGATIVE = ("blurry, low quality, low resolution, distorted, warped, morphing, flickering, jittery, text, captions, "
            "watermark, logo, frame border, static image, frozen, duplicate frames, oversaturated, cartoon")
STEPS, SHIFT, SAMPLER = 4, 3.0, "uni_pc_bh2"
MAX_PIXELS = 1280 * 720
KEEP_RESULTS = 64
# The in-pod self-stop guard (com.projectv.gpu.PodSelfStop) terminates the pod when this file is older than the idle
# limit and the GPU is quiet. Boot (pip, ComfyUI, ~42 GB of weights) is minutes of no GPU use, so the worker keeps it warm.
HEARTBEAT = os.environ.get("SV_HEARTBEAT", "/tmp/projectv-heartbeat")
BOOT_BEAT_SECONDS = 30


def touch_heartbeat(path: str | None = None) -> None:
    """Tells the pod's idle guard the worker is alive. Never raises (a missing /tmp must not break a request)."""
    try:
        p = Path(path or HEARTBEAT)
        p.touch()
    except OSError:
        pass


def log(msg: str) -> None:
    print(f"[pod-worker {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------------------------------- pure helpers (tested)

def expert_split(steps: int, shift: float = SHIFT, boundary_ratio: float = 0.9, train_steps: int = 1000) -> int:
    """High-noise expert steps exactly as Diffusers' Wan I2V pipeline splits them (4 steps -> 2 + 2)."""
    n = 0
    for i in range(steps):
        s = 1 + (1 / train_steps - 1) * i / steps
        s = shift * s / (1 + (shift - 1) * s)
        if s * train_steps >= boundary_ratio * train_steps:
            n += 1
    return max(1, min(steps - 1, n)) if steps > 1 else steps


def validate_clip(c: dict) -> list[str]:
    errors = []
    if not (c.get("prompt") or "").strip():
        errors.append("empty prompt")
    if not c.get("image"):
        errors.append("no image")
    frames, fps = c.get("frames", 81), c.get("fps", 16)
    if not isinstance(frames, int) or frames < 5 or (frames - 1) % 4:
        errors.append("frames must be 4k+1 (e.g. 81)")
    if not isinstance(fps, int) or fps <= 0:
        errors.append("fps must be a positive integer")
    for k in ("width", "height"):
        v = c.get(k)
        if not isinstance(v, int) or v < 16 or v % 16:
            errors.append(f"{k} must be a multiple of 16")
    if isinstance(c.get("width"), int) and isinstance(c.get("height"), int) and c["width"] * c["height"] > MAX_PIXELS:
        errors.append("larger than 720p is out of scope")
    seed = c.get("seed")
    if not isinstance(seed, int) or seed < 0:
        errors.append("seed must be a non-negative integer")
    return errors


def validate_picture(p: dict) -> list[str]:
    errors = []
    if not (p.get("prompt") or "").strip():
        errors.append("empty prompt")
    for k in ("width", "height"):
        v = p.get(k, 768)
        if not isinstance(v, int) or v < 64 or v > 2048 or v % 16:
            errors.append(f"{k} must be a multiple of 16 between 64 and 2048")
    seed = p.get("seed", 0)
    if not isinstance(seed, int) or seed < 0:
        errors.append("seed must be a non-negative integer")
    return errors


def clip_graph(c: dict, image_name: str, prefix: str, end_name: str | None = None) -> dict:
    """ComfyUI API graph for one clip: the fp8 benchmark's proven graph (4 steps, CFG 1, 2+2 expert split).
    With end_name (first-last frame chaining) node 12 is WanFirstLastFrameToVideo on the same I2V experts and LoRAs
    (no extra weights): the clip starts on image_name and ends on end_name. Same outputs, so nodes 13-16 are unchanged."""
    split = expert_split(STEPS)
    seed = int(c["seed"]) & 0xFFFFFFFFFFFFFFFF
    neg = NEGATIVE + (", " + c["negative"] if c.get("negative") else "")
    g = _clip_graph(c, image_name, prefix, split, seed, neg)
    if end_name:
        g["17"] = {"class_type": "LoadImage", "inputs": {"image": end_name}}
        g["12"] = {"class_type": "WanFirstLastFrameToVideo", "inputs": {**g["12"]["inputs"], "end_image": ["17", 0]}}
    return g


def _clip_graph(c: dict, image_name: str, prefix: str, split: int, seed: int, neg: str) -> dict:
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET_HIGH, "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET_LOW, "weight_dtype": "default"}},
        "3": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": LORA_HIGH, "strength_model": 1.0}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2", 0], "lora_name": LORA_LOW, "strength_model": 1.0}},
        "5": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["3", 0], "shift": SHIFT}},
        "6": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["4", 0], "shift": SHIFT}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODER, "type": "wan", "device": "default"}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"text": c["prompt"], "clip": ["7", 0]}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["7", 0]}},
        "10": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "11": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "12": {"class_type": "WanImageToVideo", "inputs": {
            "positive": ["8", 0], "negative": ["9", 0], "vae": ["10", 0], "width": c["width"], "height": c["height"],
            "length": c.get("frames", 81), "batch_size": 1, "start_image": ["11", 0]}},
        "13": {"class_type": "KSamplerAdvanced", "inputs": {
            "model": ["5", 0], "add_noise": "enable", "noise_seed": seed, "steps": STEPS, "cfg": 1.0,
            "sampler_name": SAMPLER, "scheduler": "simple", "positive": ["12", 0], "negative": ["12", 1],
            "latent_image": ["12", 2], "start_at_step": 0, "end_at_step": split, "return_with_leftover_noise": "enable"}},
        "14": {"class_type": "KSamplerAdvanced", "inputs": {
            "model": ["6", 0], "add_noise": "disable", "noise_seed": seed, "steps": STEPS, "cfg": 1.0,
            "sampler_name": SAMPLER, "scheduler": "simple", "positive": ["12", 0], "negative": ["12", 1],
            "latent_image": ["13", 0], "start_at_step": split, "end_at_step": 10000, "return_with_leftover_noise": "disable"}},
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["10", 0]}},
        "16": {"class_type": "SaveImage", "inputs": {"images": ["15", 0], "filename_prefix": prefix}},
    }


def plan() -> dict:
    return {"comfy": COMFY_TAG, "files": sorted(FILES), "zimage": ZIMAGE_REPO,
            "download_gb": round(sum(f[3] for f in FILES.values()) / 1e9, 1), "root": str(ROOT), "port": PORT,
            "steps": STEPS, "split": expert_split(STEPS), "sampler": SAMPLER}


# ------------------------------------------------------------------------------------------- jobs

class Jobs:
    """Async job table + one GPU thread. `runner(kind, payload) -> (bytes, extra)` does the work."""

    def __init__(self, runner):
        self.runner = runner
        self.jobs: dict[str, dict] = {}
        self.order: list[str] = []
        self.q: queue.Queue = queue.Queue()
        self.lock = threading.Lock()

    def submit(self, kind: str, payload: dict) -> str:
        jid = uuid.uuid4().hex
        with self.lock:
            self.jobs[jid] = {"id": jid, "kind": kind, "state": "queued", "error": None, "seconds": None,
                              "created": time.time()}
            self.order.append(jid)
            while len(self.order) > KEEP_RESULTS:  # results are fetched within seconds; keep memory bounded
                self.jobs.pop(self.order.pop(0), None)
        self.q.put((jid, kind, payload))
        return jid

    def get(self, jid: str) -> dict | None:
        with self.lock:
            j = self.jobs.get(jid)
            return None if j is None else {k: v for k, v in j.items() if k != "result"}

    def result(self, jid: str) -> tuple[bytes, str] | None:
        with self.lock:
            j = self.jobs.get(jid)
            if not j or j["state"] != "done":
                return None
            return j["result"], ("video/mp4" if j["kind"] == "clip" else "image/png")

    def work_one(self, block: bool = True) -> bool:
        try:
            jid, kind, payload = self.q.get(block=block, timeout=1 if block else None)
        except queue.Empty:
            return False
        with self.lock:
            if jid not in self.jobs:
                return True
            self.jobs[jid]["state"] = "running"
        t = time.time()
        try:
            data, extra = self.runner(kind, payload)
            update = {"state": "done", "result": data, **extra}
        except Exception as e:  # noqa: BLE001 - a failed job never kills the worker
            update = {"state": "failed", "error": f"{type(e).__name__}: {e}"[:600]}
        update["seconds"] = round(time.time() - t, 2)
        with self.lock:
            if jid in self.jobs:
                self.jobs[jid].update(update)
        return True

    def loop(self) -> None:
        while True:
            self.work_one()


# ------------------------------------------------------------------------------------------- HTTP

class State:
    def __init__(self):
        self.ready = False
        self.phase = "starting"
        self.error = None
        self.jobs: Jobs | None = None


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet: the gateway polls /health often
            pass

        def _json(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            touch_heartbeat()
            path = self.path.split("?")[0].rstrip("/")
            if path in ("/health", "/ready", ""):
                body = {"ready": state.ready, "phase": state.phase, "error": state.error}
                return self._json(200 if state.ready else 503, body)
            parts = path.split("/")
            if len(parts) >= 3 and parts[1] == "jobs" and state.jobs:
                jid = parts[2]
                if len(parts) == 4 and parts[3] == "result":
                    r = state.jobs.result(jid)
                    if r is None:
                        return self._json(404, {"error": "no result"})
                    data, ctype = r
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                j = state.jobs.get(jid)
                return self._json(200, j) if j else self._json(404, {"error": "unknown job"})
            return self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            touch_heartbeat()
            path = self.path.split("?")[0].rstrip("/")
            if path not in ("/clip", "/picture"):
                return self._json(404, {"error": "not found"})
            if not state.ready or not state.jobs:
                return self._json(503, {"error": "not ready", "phase": state.phase})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._json(400, {"error": "bad json"})
            kind = path[1:]
            errors = validate_clip(body) if kind == "clip" else validate_picture(body)
            if errors:
                return self._json(400, {"error": "; ".join(errors)})
            return self._json(202, {"id": state.jobs.submit(kind, body)})

    return Handler


class DualStackServer(ThreadingHTTPServer):
    """Listens on [::] (IPv6, required by the Container Gateway) and, where the OS allows, IPv4 too."""
    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (OSError, AttributeError):
            pass
        super().server_bind()


# ------------------------------------------------------------------------------------------- GPU side (box only)

def install() -> None:
    missing = []
    for mod, pkg in (("huggingface_hub", PIP[0]), ("diffusers", PIP[1]), ("transformers", PIP[2]),
                     ("accelerate", PIP[3]), ("imageio_ffmpeg", "imageio-ffmpeg")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        log(f"pip install {len(PIP)} packages")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", *PIP], check=True)


def fetch_comfy() -> Path:
    comfy = ROOT / f"ComfyUI-{COMFY_TAG}"
    if not (comfy / "main.py").exists():
        log(f"fetching ComfyUI {COMFY_TAG}")
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tgz = Path(tmp) / "comfy.tgz"
            urllib.request.urlretrieve(COMFY_TARBALL, tgz)
            with tarfile.open(tgz) as t:
                t.extractall(tmp, filter="data")
            src = next(p for p in Path(tmp).iterdir() if p.is_dir())
            shutil.move(str(src), comfy)
        req = comfy / "requirements.txt"
        lines = [ln for ln in req.read_text().splitlines() if ln.strip() and not ln.split("=")[0].split(">")[0].split("<")[0]
                 .strip().lower() in ("torch", "torchvision", "torchaudio")]  # keep the image's CUDA torch
        (ROOT / "comfy-req.txt").write_text("\n".join(lines))
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(ROOT / "comfy-req.txt")], check=True)
    return comfy


VOLUME = os.environ.get("SV_VOLUME", "")   # optional persistent (RunPod global) volume: weights are cached there
ZIMAGE_PATH = ZIMAGE_REPO                  # becomes a local snapshot folder once downloaded / found on the volume


def volume_file(volume: str, name: str, size: int) -> Path | None:
    """The cached copy of `name` on the volume when it is complete (right size), else None. A global volume is
    object-backed (no locks, no atomic rename), so completeness is judged by size alone, never by a lock file."""
    if not volume:
        return None
    p = Path(volume) / "wan-fp8" / name
    try:
        return p if p.stat().st_size == size else None
    except OSError:
        return None


def copy_to_volume(volume: str, name: str, local: Path) -> None:
    dest = Path(volume) / "wan-fp8" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(local, dest)   # a half-copied file has the wrong size, so the next start re-copies it


def copy_tree_plain(src: Path, dst: Path) -> None:
    """Copy file contents only. shutil.copytree also copies permissions and times, which RunPod's global
    volume rejects (Errno 1 Operation not permitted); the Hugging Face snapshot's links are followed."""
    for root, _dirs, files in os.walk(src):
        out = dst / Path(root).relative_to(src)
        out.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copyfile(Path(root) / f, out / f)


def download(comfy: Path, state: State) -> None:
    global ZIMAGE_PATH
    from huggingface_hub import hf_hub_download, snapshot_download

    weights = ROOT / "weights"
    for i, (name, (repo, path, sub, size)) in enumerate(FILES.items(), 1):
        t = time.time()
        cached = volume_file(VOLUME, name, size)
        if cached is not None:
            state.phase = f"using volume copy {i}/{len(FILES)} {name}"
            local, how = cached, "volume"
        else:
            state.phase = f"downloading {i}/{len(FILES)} {name}"
            local = Path(hf_hub_download(repo, path, local_dir=str(weights)))
            if local.stat().st_size != size:
                raise RuntimeError(f"{name}: size {local.stat().st_size} != expected {size}")
            how = "huggingface"
            if VOLUME:
                state.phase = f"caching {i}/{len(FILES)} {name} on the volume"
                copy_to_volume(VOLUME, name, local)
        dest = comfy / "models" / sub / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        dest.symlink_to(local)
        log(f"{name} {size / 1e9:.1f} GB from {how} in {time.time() - t:.0f} s")
    t = time.time()
    marker = Path(VOLUME) / "zimage-turbo" / ".complete" if VOLUME else None
    if marker is not None and marker.exists():
        state.phase = "using volume copy of Z-Image Turbo"
        ZIMAGE_PATH = str(marker.parent)
    else:
        state.phase = "downloading Z-Image Turbo"
        snap = snapshot_download(ZIMAGE_REPO, cache_dir=str(ROOT / "hf"))
        ZIMAGE_PATH = snap
        if VOLUME:
            state.phase = "caching Z-Image Turbo on the volume"
            target = Path(VOLUME) / "zimage-turbo"
            copy_tree_plain(Path(snap), target)
            (target / ".complete").write_text("ok")
    log(f"Z-Image Turbo ready in {time.time() - t:.0f} s")


def http_json(url: str, body: dict | None = None, timeout: float = 30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def start_comfy(comfy: Path) -> subprocess.Popen:
    out = ROOT / "comfy"
    (out / "in").mkdir(parents=True, exist_ok=True)
    (out / "out").mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([sys.executable, "main.py", "--listen", "127.0.0.1", "--port", str(COMFY_PORT),
                             "--disable-auto-launch", "--output-directory", str(out / "out"),
                             "--input-directory", str(out / "in")], cwd=comfy)
    for _ in range(600):
        if proc.poll() is not None:
            raise RuntimeError("ComfyUI exited during start")
        try:
            http_json(f"http://127.0.0.1:{COMFY_PORT}/system_stats", timeout=2)
            return proc
        except Exception:  # noqa: BLE001
            time.sleep(1)
    raise RuntimeError("ComfyUI did not come up in 600 s")


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


class Gpu:
    """Runs one job at a time. Z-Image (diffusers) and the Wan experts (ComfyUI) never share the VRAM."""

    def __init__(self):
        self.zimage = None

    def _free_comfy(self):
        try:
            http_json(f"http://127.0.0.1:{COMFY_PORT}/free", {"unload_models": True, "free_memory": True})
        except Exception as e:  # noqa: BLE001
            log(f"ComfyUI free failed: {e}")

    def _free_zimage(self):
        if self.zimage is not None:
            import torch

            self.zimage = None
            torch.cuda.empty_cache()

    def run(self, kind: str, p: dict) -> tuple[bytes, dict]:
        return self.picture(p) if kind == "picture" else self.clip(p)

    def picture(self, p: dict) -> tuple[bytes, dict]:
        import torch

        if self.zimage is None:
            from diffusers import ZImagePipeline

            self._free_comfy()
            self.zimage = ZImagePipeline.from_pretrained(ZIMAGE_PATH, torch_dtype=torch.bfloat16,
                                                         cache_dir=str(ROOT / "hf")).to("cuda")
        seed = int(p.get("seed") or 0)
        img = self.zimage(prompt=p["prompt"], width=int(p.get("width", 768)), height=int(p.get("height", 1344)),
                          num_inference_steps=9, guidance_scale=0.0,  # same as workers/modal_apps/zimage.py
                          generator=torch.Generator("cuda").manual_seed(seed)).images[0]
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), {"seed": seed}

    def clip(self, c: dict) -> tuple[bytes, dict]:
        from PIL import Image

        self._free_zimage()
        jid = uuid.uuid4().hex[:12]
        name = f"{jid}.png"
        Image.open(io.BytesIO(base64.b64decode(c["image"]))).convert("RGB") \
            .resize((c["width"], c["height"]), Image.LANCZOS).save(ROOT / "comfy" / "in" / name)
        end_name = None
        if c.get("endImage"):  # first-last frame: the clip must land on this picture
            end_name = f"{jid}-end.png"
            Image.open(io.BytesIO(base64.b64decode(c["endImage"]))).convert("RGB") \
                .resize((c["width"], c["height"]), Image.LANCZOS).save(ROOT / "comfy" / "in" / end_name)
        pid = http_json(f"http://127.0.0.1:{COMFY_PORT}/prompt",
                        {"prompt": clip_graph(c, name, f"clip/{jid}", end_name)})["prompt_id"]
        while True:
            hist = http_json(f"http://127.0.0.1:{COMFY_PORT}/history/{pid}").get(pid)
            status = (hist or {}).get("status", {})
            if status.get("status_str") == "error":
                msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                raise RuntimeError(msgs[0][1].get("exception_message", "ComfyUI error") if msgs else "ComfyUI error")
            if hist and hist.get("outputs"):
                break
            time.sleep(1)
        images = hist["outputs"]["16"]["images"]
        frames = [ROOT / "comfy" / "out" / im.get("subfolder", "") / im["filename"] for im in images]
        fps = int(c.get("fps", 16))
        with tempfile.TemporaryDirectory() as tmp:
            for i, f in enumerate(frames):
                os.symlink(f, Path(tmp) / f"{i:05d}.png")
            mp4 = Path(tmp) / "clip.mp4"
            subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-framerate", str(fps), "-i", f"{tmp}/%05d.png",
                            "-c:v", "libx264", "-crf", "12", "-preset", "slow", "-pix_fmt", "yuv420p", str(mp4)], check=True)
            data = mp4.read_bytes()
        for f in frames:
            f.unlink(missing_ok=True)
        (ROOT / "comfy" / "in" / name).unlink(missing_ok=True)
        return data, {"frames": len(frames), "fps": fps}


def boot_heartbeat(state: State, interval: float = BOOT_BEAT_SECONDS) -> None:
    """Keeps the idle guard quiet while booting; stops when boot ends (ready, or failed: a failed pod should die)."""
    while not state.ready and not state.error:
        touch_heartbeat()
        time.sleep(interval)


def boot(state: State) -> None:
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        state.phase = "installing"
        install()
        comfy = fetch_comfy()
        download(comfy, state)
        state.phase = "starting ComfyUI"
        start_comfy(comfy)
        gpu = Gpu()
        state.jobs = Jobs(gpu.run)
        threading.Thread(target=state.jobs.loop, daemon=True, name="gpu").start()
        state.phase, state.ready = "ready", True
        log("ready")
    except Exception as e:  # noqa: BLE001 - stay up and report it on /health; the control plane deletes the group
        state.phase, state.error = "failed", f"{type(e).__name__}: {e}"[:600]
        log(f"boot failed: {state.error}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Project V pod GPU worker")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and build a graph; no GPU, no download")
    args = ap.parse_args(argv)
    if args.dry_run:
        print(json.dumps(plan(), indent=1))
        clip_graph({"prompt": "test", "seed": 1, "width": 480, "height": 832, "frames": 81}, "x.png", "clip/x")
        print("dry run OK")
        return 0
    state = State()
    touch_heartbeat()
    threading.Thread(target=boot_heartbeat, args=(state,), daemon=True, name="boot-beat").start()
    threading.Thread(target=boot, args=(state,), daemon=True, name="boot").start()
    try:
        server = DualStackServer(("::", PORT), make_handler(state))
    except OSError:  # no IPv6 in this container (e.g. a RunPod pod): plain IPv4
        server = ThreadingHTTPServer(("0.0.0.0", PORT), make_handler(state))
    log(f"listening on [::]:{PORT}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
