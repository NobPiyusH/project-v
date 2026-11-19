"""Wan 2.2 I2V A14B in fp8 on ONE 24 GB GPU (RTX 4090): render the benchmark clips and time everything.

Runs ON THE POD (see docs/wan-fp8-4090-runbook.md). Route: ComfyUI (pinned tag) driven over its local HTTP API with the
Comfy-Org fp8_scaled experts. ComfyUI swaps the two 14.3 GB experts between GPU and host RAM by itself, which is what
lets a 14B+14B model run in 24 GB. Settings mirror workers/modal_apps/wan.py "fast" mode so the A/B is fair:
  - same lightx2v 4-step LoRAs (the 1022 rank-64 files, ComfyUI-native key format), strength 1.0 on each expert
  - 4 steps, CFG 1.0 on both experts, UniPC (bh2) with flow shift 3.0 (Diffusers' scheduler_config for this model)
  - high-noise expert for the first steps, low-noise for the rest: the split is computed exactly as Diffusers does it
    (boundary_ratio 0.9) -> 2 + 2 for 4 steps
  - keyframe resized with LANCZOS to the clip size (as Modal does), 81 frames, 16 fps, H.264 CRF 12 preset slow yuv420p
Known differences (cannot be removed): fp8 weights (the variable under test); noise is drawn by ComfyUI on the CPU,
Diffusers draws it on CUDA, so the same seed gives a different (equally random) clip; ComfyUI restarts the UniPC
history when it switches experts.

  python bench_wan_fp8.py --manifest in/manifest.json --dry-run          # no GPU, no download: validate only
  python bench_wan_fp8.py --manifest in/manifest.json --weights /workspace/weights --comfy /workspace/ComfyUI --out out

Prints no secrets (public repos; HF_TOKEN, if set, is used by huggingface_hub and never printed).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

COMFY_TAG = "v0.45.0"
HEARTBEAT = "/tmp/projectv-heartbeat"   # PodSelfStop's idle heartbeat (com.projectv.gpu.PodSelfStop.HEARTBEAT)

# Real files, checked via the Hugging Face API on 20 Nov 2026 (sizes in bytes). All Apache-2.0.
# Comfy-Org/Wan_2.2_ComfyUI_Repackaged now redirects to Comfy-Org/Wan-2.2 (revision 5df3a4a3).
COMFY_REPO = "Comfy-Org/Wan-2.2"
LORA_REPO = "lightx2v/Wan2.2-Distill-Loras"   # same repo + files as workers/modal_apps/wan.py
LORA_HIGH = "wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors"
LORA_LOW = "wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors"
UNET_HIGH = "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
UNET_LOW = "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
VAE = "wan_2.1_vae.safetensors"   # the A14B models use the Wan 2.1 VAE (the 2.2 VAE is for TI2V-5B)
TEXT_ENCODERS = {"fp16": "umt5_xxl_fp16.safetensors", "fp8": "umt5_xxl_fp8_e4m3fn_scaled.safetensors"}

# (repo, path in repo, ComfyUI models subfolder, size in bytes)
FILES = {
    UNET_HIGH: (COMFY_REPO, f"split_files/diffusion_models/{UNET_HIGH}", "diffusion_models", 14294742832),
    UNET_LOW: (COMFY_REPO, f"split_files/diffusion_models/{UNET_LOW}", "diffusion_models", 14294742832),
    TEXT_ENCODERS["fp16"]: (COMFY_REPO, f"split_files/text_encoders/{TEXT_ENCODERS['fp16']}", "text_encoders", 11366399385),
    TEXT_ENCODERS["fp8"]: (COMFY_REPO, f"split_files/text_encoders/{TEXT_ENCODERS['fp8']}", "text_encoders", 6735906897),
    VAE: (COMFY_REPO, f"split_files/vae/{VAE}", "vae", 253815318),
    LORA_HIGH: (LORA_REPO, LORA_HIGH, "loras", 634645944),
    LORA_LOW: (LORA_REPO, LORA_LOW, "loras", 739472104),
}

# Same negative prompt as the Modal app (with CFG 1.0 it has no effect there or here; kept for parity).
NEGATIVE = ("blurry, low quality, low resolution, distorted, warped, morphing, flickering, jittery, text, captions, "
            "watermark, logo, frame border, static image, frozen, duplicate frames, oversaturated, cartoon")


def needed_files(text_encoder: str) -> list[str]:
    return [UNET_HIGH, UNET_LOW, TEXT_ENCODERS[text_encoder], VAE, LORA_HIGH, LORA_LOW]


def expert_split(steps: int, shift: float = 3.0, boundary_ratio: float = 0.9, train_steps: int = 1000) -> int:
    """How many of the steps Diffusers' WanImageToVideoPipeline gives the high-noise expert: UniPC flow sigmas
    linspace(1, 1/1000, steps+1)[:-1], shifted s' = shift*s / (1 + (shift-1)*s); high-noise while t >= 0.9 * 1000."""
    n = 0
    for i in range(steps):
        s = 1 + (1 / train_steps - 1) * i / steps
        s = shift * s / (1 + (shift - 1) * s)
        if s * train_steps >= boundary_ratio * train_steps:
            n += 1
    return max(1, min(steps - 1, n)) if steps > 1 else steps


def validate_manifest(manifest: dict, base: Path) -> list[str]:
    """Return a list of problems (empty = OK). Paths are relative to the manifest's folder."""
    errors = []
    clips = manifest.get("clips")
    if not isinstance(clips, list) or not clips:
        return ["manifest has no clips"]
    seen = set()
    for i, c in enumerate(clips):
        where = f"clip {i} ({c.get('id', '?')})"
        cid = c.get("id")
        if not cid or not str(cid).replace("-", "").replace("_", "").isalnum():
            errors.append(f"{where}: id must be letters, digits, - or _")
        elif cid in seen:
            errors.append(f"{where}: duplicate id")
        seen.add(cid)
        if not (c.get("prompt") or "").strip():
            errors.append(f"{where}: empty prompt")
        img = c.get("image")
        if not img or not (base / img).is_file():
            errors.append(f"{where}: image not found: {img}")
        seed = c.get("seed")
        if not isinstance(seed, int) or seed < 0 or seed > 0xFFFFFFFFFFFFFFFF:
            errors.append(f"{where}: seed must be a non-negative integer")
        frames, fps = c.get("frames", 81), c.get("fps", 16)
        if not isinstance(frames, int) or frames < 5 or (frames - 1) % 4:
            errors.append(f"{where}: frames must be 4k+1 (e.g. 81)")
        if not isinstance(fps, int) or fps <= 0:
            errors.append(f"{where}: fps must be a positive integer")
        for k in ("width", "height"):
            v = c.get(k)
            if not isinstance(v, int) or v < 16 or v % 16:
                errors.append(f"{where}: {k} must be a multiple of 16")
        if isinstance(c.get("width"), int) and isinstance(c.get("height"), int) and c["width"] * c["height"] > 1280 * 720:
            errors.append(f"{where}: larger than 720p is out of scope for a 24 GB card")
    return errors


def workflow(clip: dict, image_name: str, args) -> dict:
    """ComfyUI API-format graph for one clip (node ids are strings; inputs reference [node, output])."""
    steps = args.steps
    split = expert_split(steps, args.shift)
    seed = int(clip["seed"])
    neg = NEGATIVE + (", " + clip["negative"] if clip.get("negative") else "")
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET_HIGH, "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET_LOW, "weight_dtype": "default"}},
        "3": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": LORA_HIGH, "strength_model": 1.0}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2", 0], "lora_name": LORA_LOW, "strength_model": 1.0}},
        "5": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["3", 0], "shift": args.shift}},
        "6": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["4", 0], "shift": args.shift}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODERS[args.text_encoder], "type": "wan", "device": "default"}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"text": clip["prompt"], "clip": ["7", 0]}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"text": neg, "clip": ["7", 0]}},
        "10": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "11": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "12": {"class_type": "WanImageToVideo", "inputs": {
            "positive": ["8", 0], "negative": ["9", 0], "vae": ["10", 0], "width": clip["width"], "height": clip["height"],
            "length": clip.get("frames", 81), "batch_size": 1, "start_image": ["11", 0]}},
        "13": {"class_type": "KSamplerAdvanced", "inputs": {
            "model": ["5", 0], "add_noise": "enable", "noise_seed": seed, "steps": steps, "cfg": 1.0,
            "sampler_name": args.sampler, "scheduler": "simple", "positive": ["12", 0], "negative": ["12", 1],
            "latent_image": ["12", 2], "start_at_step": 0, "end_at_step": split, "return_with_leftover_noise": "enable"}},
        "14": {"class_type": "KSamplerAdvanced", "inputs": {
            "model": ["6", 0], "add_noise": "disable", "noise_seed": seed, "steps": steps, "cfg": 1.0,
            "sampler_name": args.sampler, "scheduler": "simple", "positive": ["12", 0], "negative": ["12", 1],
            "latent_image": ["13", 0], "start_at_step": split, "end_at_step": 10000, "return_with_leftover_noise": "disable"}},
        "15": {"class_type": "VAEDecode", "inputs": {"samples": ["14", 0], "vae": ["10", 0]}},
        "16": {"class_type": "SaveImage", "inputs": {"images": ["15", 0], "filename_prefix": f"bench/{clip['id']}"}},
    }


# ---------------------------------------------------------------- pod-side helpers (need the GPU box)

def heartbeat() -> None:
    try:
        Path(HEARTBEAT).touch()
    except OSError:
        pass


class VramSampler(threading.Thread):
    """Peak GPU memory used (MiB) from nvidia-smi every 0.5 s (ComfyUI runs in another process)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.peak, self.stop = 0, threading.Event()

    def run(self):
        while not self.stop.is_set():
            try:
                out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=5).stdout
                self.peak = max([self.peak] + [int(x) for x in out.split() if x.strip().isdigit()])
            except Exception:  # noqa: BLE001 - never let sampling kill the benchmark
                pass
            self.stop.wait(0.5)


def download(weights: Path, comfy: Path, names: list[str]) -> dict:
    """Fetch each file from Hugging Face into `weights`, check its size, link it into ComfyUI's models folders."""
    from huggingface_hub import hf_hub_download

    timings = {}
    for name in names:
        repo, path, sub, size = FILES[name]
        t = time.time()
        local = Path(hf_hub_download(repo, path, local_dir=str(weights)))
        got = local.stat().st_size
        if got != size:
            raise RuntimeError(f"{name}: size {got} != expected {size}")
        timings[name] = round(time.time() - t, 1)
        print(f"downloaded {name} ({size / 1e9:.2f} GB) in {timings[name]} s", flush=True)
        dest = comfy / "models" / sub / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        dest.symlink_to(local)
        heartbeat()
    return timings


def http(url: str, body: dict | None = None, timeout: float = 30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def start_comfy(comfy: Path, port: int, out: Path, log: Path) -> subprocess.Popen:
    cmd = [sys.executable, "main.py", "--listen", "127.0.0.1", "--port", str(port), "--disable-auto-launch",
           "--output-directory", str(out / "comfy_out"), "--input-directory", str(out / "comfy_in")]
    proc = subprocess.Popen(cmd, cwd=comfy, stdout=open(log, "w"), stderr=subprocess.STDOUT)
    for _ in range(300):
        if proc.poll() is not None:
            raise RuntimeError(f"ComfyUI exited early, see {log}")
        try:
            http(f"http://127.0.0.1:{port}/system_stats", timeout=2)
            return proc
        except Exception:  # noqa: BLE001
            time.sleep(1)
    raise RuntimeError("ComfyUI did not come up in 300 s")


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def encode(frames: list[Path], dest: Path, fps: int) -> None:
    """Same encoder settings as the Modal app: H.264 CRF 12, preset slow, yuv420p."""
    with tempfile.TemporaryDirectory() as tmp:
        for i, f in enumerate(frames):
            os.symlink(f, Path(tmp) / f"{i:05d}.png")
        subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", str(fps), "-i", f"{tmp}/%05d.png",
                        "-c:v", "libx264", "-crf", "12", "-preset", "slow", "-pix_fmt", "yuv420p", str(dest)], check=True)


def render(clip: dict, base: Path, out: Path, port: int, args) -> dict:
    from PIL import Image

    t = time.time()
    name = f"{clip['id']}.png"
    Image.open(base / clip["image"]).convert("RGB").resize((clip["width"], clip["height"]), Image.LANCZOS) \
        .save(out / "comfy_in" / name)
    pid = http(f"http://127.0.0.1:{port}/prompt", {"prompt": workflow(clip, name, args)})["prompt_id"]
    while True:
        heartbeat()
        hist = http(f"http://127.0.0.1:{port}/history/{pid}").get(pid)
        if hist and hist.get("status", {}).get("completed") is not None or (hist and hist.get("outputs")):
            break
        if hist and hist.get("status", {}).get("status_str") == "error":
            break
        time.sleep(2)
    status = hist.get("status", {})
    if status.get("status_str") == "error":
        msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
        detail = msgs[0][1].get("exception_message", "") if msgs else "unknown"
        return {"id": clip["id"], "error": str(detail)[:600], "render_s": round(time.time() - t, 1)}
    images = hist["outputs"]["16"]["images"]
    frames = [out / "comfy_out" / im.get("subfolder", "") / im["filename"] for im in images]
    render_s = time.time() - t
    mp4 = out / f"{clip['id']}.mp4"
    encode(frames, mp4, clip.get("fps", 16))
    return {"id": clip["id"], "mp4": mp4.name, "frames": len(frames), "fps": clip.get("fps", 16),
            "render_s": round(render_s, 1), "encode_s": round(time.time() - t - render_s, 1), "error": None,
            "baseline_asset_id": clip.get("baseline_asset_id")}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--weights", type=Path, default=Path("/workspace/weights"))
    p.add_argument("--comfy", type=Path, default=Path("/workspace/ComfyUI"))
    p.add_argument("--out", type=Path, default=Path("/workspace/out"))
    p.add_argument("--port", type=int, default=8188)
    p.add_argument("--steps", type=int, default=4)
    p.add_argument("--shift", type=float, default=3.0)
    p.add_argument("--sampler", default="uni_pc_bh2", help="uni_pc_bh2 matches Diffusers' UniPC bh2; euler = lightx2v's Comfy default")
    p.add_argument("--text-encoder", choices=sorted(TEXT_ENCODERS), default="fp16",
                   help="fp16 keeps the text encoder as in the baseline so fp8 experts are the only variable")
    p.add_argument("--limit", type=int, default=0, help="render only the first N clips (0 = all)")
    p.add_argument("--skip-download", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="validate manifest + arguments, print the plan; no GPU")
    args = p.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    base = args.manifest.parent
    defaults = manifest.get("defaults", {})
    clips = [{**defaults, **c} for c in manifest.get("clips", [])]
    errors = validate_manifest({"clips": clips}, base)
    if args.steps < 2:
        errors.append("--steps must be >= 2 (two experts)")
    if args.limit:
        clips = clips[:args.limit]
    names = needed_files(args.text_encoder)
    plan = {"clips": len(clips), "steps": args.steps, "split": expert_split(args.steps, args.shift),
            "shift": args.shift, "sampler": args.sampler, "text_encoder": TEXT_ENCODERS[args.text_encoder],
            "download_gb": round(sum(FILES[n][3] for n in names) / 1e9, 2), "comfy_tag": COMFY_TAG}
    print(json.dumps(plan, indent=1))
    if errors:
        print("MANIFEST/ARGUMENT ERRORS:\n  " + "\n  ".join(errors))
        return 2
    if args.dry_run:
        for c in clips:   # the graph builds for every clip
            workflow(c, f"{c['id']}.png", args)
        print("dry run OK")
        return 0

    t0 = time.time()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "comfy_in").mkdir(exist_ok=True)
    results = {"plan": plan, "gpu": None, "download_s": None, "download_files_s": {}, "comfy_start_s": None,
               "clips": [], "peak_vram_mib": None}
    try:
        results["gpu"] = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                                        capture_output=True, text=True).stdout.strip()
    except OSError:
        pass
    vram = VramSampler()
    vram.start()
    proc = None
    try:
        if not args.skip_download:
            t = time.time()
            results["download_files_s"] = download(args.weights, args.comfy, names)
            results["download_s"] = round(time.time() - t, 1)
        t = time.time()
        proc = start_comfy(args.comfy, args.port, args.out, args.out / "comfy.log")
        results["comfy_start_s"] = round(time.time() - t, 1)
        for c in clips:
            try:
                r = render(c, base, args.out, args.port, args)
            except Exception as e:  # noqa: BLE001 - one bad clip must not sink the batch
                r = {"id": c["id"], "error": f"{type(e).__name__}: {e}"[:600]}
            r["peak_vram_mib_so_far"] = vram.peak
            results["clips"].append(r)
            print(json.dumps(r), flush=True)
            (args.out / "results.json").write_text(json.dumps(results, indent=1))
    finally:
        vram.stop.set()
        vram.join(timeout=2)
        if proc:
            proc.terminate()
        ok = [r["render_s"] for r in results["clips"] if not r.get("error")]
        results["peak_vram_mib"] = vram.peak
        # The first clip includes ComfyUI loading both experts + text encoder from disk; later clips are warm.
        results["first_clip_s"] = ok[0] if ok else None
        results["warm_clip_median_s"] = sorted(ok[1:])[len(ok[1:]) // 2] if len(ok) > 1 else None
        results["load_s_estimate"] = (round(ok[0] - results["warm_clip_median_s"], 1)
                                      if results["warm_clip_median_s"] is not None else None)
        results["oom"] = any("OutOfMemory" in (r.get("error") or "") or "out of memory" in (r.get("error") or "").lower()
                             for r in results["clips"])
        results["total_s"] = round(time.time() - t0, 1)
        (args.out / "results.json").write_text(json.dumps(results, indent=1))
        print(f"total {results['total_s']} s, peak VRAM {vram.peak} MiB, results in {args.out / 'results.json'}")
    return 0 if ok and len(ok) == len(clips) else 1


if __name__ == "__main__":
    sys.exit(main())
