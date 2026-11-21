"""Project V GPU worker for a rented GPU pod (RunPod, Jarvislabs, ... via com.projectv.gpu.broker) (Dream Shorts pictures + Wan clips).

Self-contained on purpose: the control plane ships THIS file to the container through env vars (SV_BOOT_00..NN,
zlib + base64; see com.projectv.gpu.broker.PodBoot) on the public image pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime,
so no private registry and no image build. Only the standard library is imported at module level; GPU libraries are
imported on the box after `boot()` installed them.

At start: install deps, fetch ComfyUI (pinned tag), download weights from Hugging Face (Apache-2.0; CLAUDE.md rule 9)
onto the container disk, start ComfyUI on 127.0.0.1. Serves on [::]:PORT (IPv6, for the Container Gateway):
  GET  /live                200 {"alive": true}, never needs the token (bare liveness, no information)
  GET  /health              200 {"ready": true} once ready, else 503 {"ready": false, "phase": ...}
  POST /picture             {prompt, seed, width, height, model?}                 -> 202 {"id": job}
                            model: "zimage" (default) or "flux2-klein" (FLUX.2 klein 4B on ComfyUI, fetched on first use)
  POST /clip                {image: b64 PNG, prompt, negative, seed, frames, fps, width, height} -> 202 {"id": job}
  POST /scene               SVI scene {image: b64 PNG (the anchor), beats: [{prompt, seed}], startBeat, prevLatent: b64
                            .latent (startBeat > 1), negative, frames, fps, width, height} -> 202 {"id": job}; result: a
                            zip of beatK.mp4 + beatK.latent for each beat rendered. The first scene job installs
                            KJNodes and fetches the SVI LoRAs (~2.5 GB), so normal Shorts never pay for them.
  GET  /jobs/<id>           {"state": queued|running|done|failed, "seconds", "error", ...}
  GET  /jobs/<id>/result    the PNG or MP4 bytes (404 until done)
With SV_TOKEN set, every other route answers 401 without "Authorization: Bearer <token>" (constant-time compare), and an
unauthorised request does not touch the idle heartbeat (a port scanner must not keep a rented GPU alive).
Jobs are async: the gateway's timeouts are capped and a first clip can take minutes. One GPU job at a time.

  python server.py --dry-run      # no GPU, no download: print the plan, build a clip graph, exit 0
"""

from __future__ import annotations

import argparse
import base64
import hmac
import zipfile
import io
import json
import os
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

def hub_env(environ=os.environ, has_hf_transfer=None) -> None:
    """Weight downloads skip Hugging Face xet from the first try (21 Nov 2026: xet failed every FLUX download on two
    pods, "File reconstruction error ... receiver dropped"). Must run before huggingface_hub is imported: the library
    reads these once at import. hf_transfer is used only if the pod already has it (nothing new is installed)."""
    environ.setdefault("HF_HUB_DISABLE_XET", "1")
    if has_hf_transfer is None:
        import importlib.util
        has_hf_transfer = importlib.util.find_spec("hf_transfer") is not None
    if has_hf_transfer:
        environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")


hub_env()  # at import, before any `from huggingface_hub import ...` below (they are all inside functions)

COMFY_TAG = "v0.45.0"
COMFY_TARBALL = f"https://github.com/comfyanonymous/ComfyUI/archive/refs/tags/{COMFY_TAG}.tar.gz"
ROOT = Path(os.environ.get("SV_ROOT", "/data"))
PORT = int(os.environ.get("SV_PORT", "8000"))
COMFY_PORT = 8188
# Per-rental bearer token (set by the control plane in SV_TOKEN). With it, every route except the bare /live needs
# "Authorization: Bearer <token>" (a public Vast.ai port is open to the whole internet). Without it the worker stays
# open, as before (RunPod proxy pods of older builds). Taken out of the environment at once so ComfyUI and anything
# else started from here never inherits it.
WORKER_TOKEN = os.environ.pop("SV_TOKEN", "")

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
# SVI 2.0 Pro (paid bench 21 Nov 2026, docs/svi-scenes.md): KJNodes' WanImageToVideoSVIPro on the native loaders, the
# two SVI LoRAs stacked at 1.0 on top of LightX2V. Only fetched by the first scene job (see Gpu.ensure_svi).
KJ_SHA = "aa2b5ea5e7f61b51f5d26d726f23c1d74e951016"
KJ_TARBALL = f"https://github.com/kijai/ComfyUI-KJNodes/archive/{KJ_SHA}.tar.gz"
SVI_REPO = "Kijai/WanVideo_comfy"
SVI_HIGH = "SVI_v2_PRO_Wan2.2-I2V-A14B_HIGH_lora_rank_128_fp16.safetensors"
SVI_LOW = "SVI_v2_PRO_Wan2.2-I2V-A14B_LOW_lora_rank_128_fp16.safetensors"
SVI_FILES = {
    SVI_HIGH: (SVI_REPO, f"LoRAs/Stable-Video-Infinity/v2.0/{SVI_HIGH}", "loras", 1226934120),
    SVI_LOW: (SVI_REPO, f"LoRAs/Stable-Video-Infinity/v2.0/{SVI_LOW}", "loras", 1226934120),
}
SVI_MOTION_LATENTS = 3   # ~12 frames carried into the next beat; 5 (20 frames) measured WORSE on 21 Nov
SVI_MAX_BEATS = 5        # 20 s: content drifts after that (bench)
ZIMAGE_REPO = "Tongyi-MAI/Z-Image-Turbo"
# FLUX.2 klein 4B distilled (Black Forest Labs, Apache-2.0, not gated; checked on Hugging Face 21 Nov 2026; ComfyUI v0.45.0
# has Flux2Scheduler / EmptyFlux2LatentImage / CLIPLoader type "flux2"). The 9B is non-commercial and gated: not used.
# Fetched on the first flux2-klein job, not at boot, so pods that never make FLUX pictures pay nothing for it.
FLUX_REPO = "Comfy-Org/flux2-klein-4B"
FLUX_UNET = "flux-2-klein-4b.safetensors"
FLUX_TEXT = "qwen_3_4b.safetensors"
FLUX_VAE = "flux2-vae.safetensors"
FLUX_FILES = {  # name: (repo, path in repo, ComfyUI models subfolder, size in bytes)
    FLUX_UNET: (FLUX_REPO, f"split_files/diffusion_models/{FLUX_UNET}", "diffusion_models", 7751105712),
    FLUX_TEXT: (FLUX_REPO, f"split_files/text_encoders/{FLUX_TEXT}", "text_encoders", 8044982048),
    FLUX_VAE: (FLUX_REPO, f"split_files/vae/{FLUX_VAE}", "vae", 336211292),
}
FLUX_STEPS, FLUX_CFG = 4, 1.0   # the distilled model's settings (ComfyUI's own template: 4 steps, CFG 1, euler)
PICTURE_MODELS = ("zimage", "flux2-klein")
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
    if (p.get("model") or "zimage") not in PICTURE_MODELS:
        errors.append(f"model must be one of {', '.join(PICTURE_MODELS)}")
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


def validate_scene(c: dict) -> list[str]:
    beats = c.get("beats")
    if not isinstance(beats, list) or not beats:
        return ["no beats"]
    start = c.get("startBeat", 1)
    errors = validate_clip({**c, "prompt": "x", "seed": 0})
    if not isinstance(start, int) or start < 1 or start + len(beats) - 1 > SVI_MAX_BEATS:
        errors.append(f"beats must stay within 1..{SVI_MAX_BEATS}")
    if c.get("frames", 81) != 81:
        errors.append("SVI beats are 81 frames")
    if isinstance(start, int) and start > 1 and not c.get("prevLatent"):
        errors.append("a scene resumed after beat 1 needs prevLatent")
    for k, b in enumerate(beats, 1):
        if not isinstance(b, dict) or not (b.get("prompt") or "").strip():
            errors.append(f"beat {k}: empty prompt")
        elif not isinstance(b.get("seed"), int) or b["seed"] < 0:
            errors.append(f"beat {k}: seed must be a non-negative integer")
    return errors


def scene_graph(c: dict, image_name: str, prefix: str, prev_latent: str | None = None) -> dict:
    """ComfyUI API graph for one SVI scene: every beat in ONE prompt (the bench's graph_b). The start picture is the
    anchor of every beat; beat k > 1 continues from the previous beat's sampler latent (3 motion latents). A scene
    resumed at beat s > 1 (a redo or a retry) starts from the saved latent of beat s-1 (LoadLatent). Each beat's frames
    go to SaveImage "{prefix}_b{k}" and its latent to SaveLatent "{prefix}_b{k}", so a later redo can resume there."""
    split = expert_split(STEPS)
    neg = NEGATIVE + (", " + c["negative"] if c.get("negative") else "")
    g = _clip_graph({**c, "prompt": c["beats"][0]["prompt"]}, image_name, prefix, split, 0, neg)
    for n in ("12", "13", "14", "15", "16"):  # the plain I2V conditioning and its samplers are replaced below
        g.pop(n)
    g["30"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["3", 0], "lora_name": SVI_HIGH, "strength_model": 1.0}}
    g["31"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["4", 0], "lora_name": SVI_LOW, "strength_model": 1.0}}
    g["5"]["inputs"]["model"] = ["30", 0]
    g["6"]["inputs"]["model"] = ["31", 0]
    g["21"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["11", 0], "vae": ["10", 0]}}
    prev = None
    if prev_latent:
        g["22"] = {"class_type": "LoadLatent", "inputs": {"latent": prev_latent}}
        prev = "22"
    start = int(c.get("startBeat", 1))
    for i, b in enumerate(c["beats"]):
        k = start + i
        pos = "8" if i == 0 else f"p{k}"
        if i > 0:  # each beat may have its own motion prompt
            g[pos] = {"class_type": "CLIPTextEncode", "inputs": {"text": b["prompt"], "clip": ["7", 0]}}
        inp = {"positive": [pos, 0], "negative": ["9", 0], "length": int(c.get("frames", 81)),
               "anchor_samples": ["21", 0], "motion_latent_count": SVI_MOTION_LATENTS}
        if prev:
            inp["prev_samples"] = [prev, 0]
        seed = int(b["seed"]) & 0xFFFFFFFFFFFFFFFF
        common = {"noise_seed": seed, "steps": STEPS, "cfg": 1.0, "sampler_name": SAMPLER, "scheduler": "simple",
                  "positive": [f"c{k}", 0], "negative": [f"c{k}", 1]}
        g[f"c{k}"] = {"class_type": "WanImageToVideoSVIPro", "inputs": inp}
        g[f"k{k}a"] = {"class_type": "KSamplerAdvanced", "inputs": {
            **common, "model": ["5", 0], "add_noise": "enable", "latent_image": [f"c{k}", 2], "start_at_step": 0,
            "end_at_step": split, "return_with_leftover_noise": "enable"}}
        g[f"k{k}b"] = {"class_type": "KSamplerAdvanced", "inputs": {
            **common, "model": ["6", 0], "add_noise": "disable", "latent_image": [f"k{k}a", 0], "start_at_step": split,
            "end_at_step": 10000, "return_with_leftover_noise": "disable"}}
        g[f"d{k}"] = {"class_type": "VAEDecode", "inputs": {"samples": [f"k{k}b", 0], "vae": ["10", 0]}}
        g[f"s{k}"] = {"class_type": "SaveImage", "inputs": {"images": [f"d{k}", 0], "filename_prefix": f"{prefix}_b{k}"}}
        g[f"l{k}"] = {"class_type": "SaveLatent", "inputs": {"samples": [f"k{k}b", 0], "filename_prefix": f"{prefix}_b{k}"}}
        prev = f"k{k}b"
    return g


def flux_graph(p: dict, prefix: str) -> dict:
    """ComfyUI API graph for one FLUX.2 klein 4B (distilled) picture: ComfyUI's official template, 4 steps, CFG 1.
    The negative is the zeroed positive (the distilled model takes no negative prompt)."""
    w, h = int(p.get("width", 768)), int(p.get("height", 1344))
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": FLUX_UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": FLUX_TEXT, "type": "flux2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": FLUX_VAE}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": p["prompt"], "clip": ["2", 0]}},
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "cfg": FLUX_CFG}},
        "7": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "8": {"class_type": "Flux2Scheduler", "inputs": {"steps": FLUX_STEPS, "width": w, "height": h}},
        "9": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(p.get("seed") or 0) & 0xFFFFFFFFFFFFFFFF}},
        "10": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": w, "height": h, "batch_size": 1}},
        "11": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["9", 0], "guider": ["6", 0], "sampler": ["7", 0], "sigmas": ["8", 0], "latent_image": ["10", 0]}},
        "12": {"class_type": "VAEDecode", "inputs": {"samples": ["11", 0], "vae": ["3", 0]}},
        "13": {"class_type": "SaveImage", "inputs": {"images": ["12", 0], "filename_prefix": prefix}},
    }


def plan() -> dict:
    return {"comfy": COMFY_TAG, "files": sorted(FILES), "zimage": ZIMAGE_REPO,
            "flux2_klein": {"files": sorted(FLUX_FILES), "gb": round(sum(f[3] for f in FLUX_FILES.values()) / 1e9, 1)},
            "download_gb": round(sum(f[3] for f in FILES.values()) / 1e9, 1), "root": str(ROOT), "port": PORT,
            "steps": STEPS, "split": expert_split(STEPS), "sampler": SAMPLER}


# ------------------------------------------------------------------------------------------- jobs

def model_of(kind: str) -> str:
    """The model a job kind keeps in VRAM: clips and SVI scenes both run on the Wan experts."""
    return "zimage" if kind == "picture" else "wan"


class Jobs:
    """Async job table + one GPU thread. `runner(kind, payload) -> (bytes, extra)` does the work.

    Batch order (21 Nov 2026): Z-Image and Wan never share the VRAM, so every change of job kind costs a model swap.
    The next job is the oldest one of the kind that ran last (the loaded model); the other kind runs when none of that
    kind is queued, or when its oldest job has waited SWITCH_AFTER_S (so a picture is never starved by a long clip run).
    With nothing loaded yet, pictures go first (a Short needs its pictures before its clips)."""

    SWITCH_AFTER_S = 15 * 60

    def __init__(self, runner, clock=time.time):
        self.runner = runner
        self.clock = clock
        self.jobs: dict[str, dict] = {}
        self.order: list[str] = []
        self.pending: list[tuple[str, str, dict, float]] = []  # (jid, kind, payload, queued at), oldest first
        self.cv = threading.Condition()
        self.lock = threading.Lock()
        self.last_kind: str | None = None

    def submit(self, kind: str, payload: dict) -> str:
        jid = uuid.uuid4().hex
        with self.lock:
            self.jobs[jid] = {"id": jid, "kind": kind, "state": "queued", "error": None, "seconds": None,
                              "created": time.time()}
            self.order.append(jid)
            while len(self.order) > KEEP_RESULTS:  # results are fetched within seconds; keep memory bounded
                self.jobs.pop(self.order.pop(0), None)
        with self.cv:
            self.pending.append((jid, kind, payload, self.clock()))
            self.cv.notify()
        return jid

    def _pick(self) -> tuple[str, str, dict, float] | None:
        """The next job by the batch order above (called with self.cv held)."""
        if not self.pending:
            return None
        loaded = model_of(self.last_kind or "picture")
        same = [p for p in self.pending if model_of(p[1]) == loaded]
        other = [p for p in self.pending if model_of(p[1]) != loaded]
        if same and not (other and self.clock() - other[0][3] >= self.SWITCH_AFTER_S):
            chosen = same[0]
        else:
            chosen = other[0]
        self.pending.remove(chosen)
        return chosen

    def get(self, jid: str) -> dict | None:
        with self.lock:
            j = self.jobs.get(jid)
            return None if j is None else {k: v for k, v in j.items() if k != "result"}

    def result(self, jid: str) -> tuple[bytes, str] | None:
        with self.lock:
            j = self.jobs.get(jid)
            if not j or j["state"] != "done":
                return None
            return j["result"], {"clip": "video/mp4", "scene": "application/zip"}.get(j["kind"], "image/png")

    def work_one(self, block: bool = True) -> bool:
        with self.cv:
            if block and not self.pending:
                self.cv.wait(timeout=1)
            picked = self._pick()
        if picked is None:
            return False
        jid, kind, payload, _ = picked
        self.last_kind = kind
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


def authorized(header: str | None, token: str) -> bool:
    """True when no token is required, or the header is exactly "Bearer <token>" (constant-time compare)."""
    if not token:
        return True
    prefix = "Bearer "
    if not header or not header.startswith(prefix):
        return False
    return hmac.compare_digest(header[len(prefix):].encode(), token.encode())


def make_handler(state: State, token: str | None = None):
    required = WORKER_TOKEN if token is None else token

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet: the gateway polls /health often
            pass

        def _auth(self, path: str) -> bool:
            """Answers 401 and returns False when the request has no valid token (only /live is open)."""
            if authorized(self.headers.get("Authorization"), required):
                return True
            self._json(401, {"error": "unauthorized"})
            return False

        def _json(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0].rstrip("/")
            if path == "/live":
                return self._json(200, {"alive": True})
            if not self._auth(path):
                return
            touch_heartbeat()
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
            path = self.path.split("?")[0].rstrip("/")
            if not self._auth(path):
                return
            touch_heartbeat()
            if path not in ("/clip", "/picture", "/scene"):
                return self._json(404, {"error": "not found"})
            if not state.ready or not state.jobs:
                return self._json(503, {"error": "not ready", "phase": state.phase})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._json(400, {"error": "bad json"})
            kind = path[1:]
            errors = (validate_clip(body) if kind == "clip" else validate_scene(body) if kind == "scene"
                      else validate_picture(body))
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


def volume_file(volume: str, name: str, size: int, folder: str = "wan-fp8") -> Path | None:
    """The cached copy of `name` on the volume when it is complete (right size), else None. A global volume is
    object-backed (no locks, no atomic rename), so completeness is judged by size alone, never by a lock file."""
    if not volume:
        return None
    p = Path(volume) / folder / name
    try:
        return p if p.stat().st_size == size else None
    except OSError:
        return None


def copy_to_volume(volume: str, name: str, local: Path, folder: str = "wan-fp8") -> None:
    dest = Path(volume) / folder / name
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


def fetch_kjnodes(comfy: Path) -> None:
    """ComfyUI-KJNodes at the pinned commit (the bench's), with its requirements (the image's torch is kept)."""
    dest = comfy / "custom_nodes" / "ComfyUI-KJNodes"
    if (dest / "__init__.py").exists():
        return
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        tgz = Path(tmp) / "kj.tgz"
        urllib.request.urlretrieve(KJ_TARBALL, tgz)
        with tarfile.open(tgz) as t:
            t.extractall(tmp, filter="data")
        src = next(p for p in Path(tmp).iterdir() if p.is_dir())
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), dest)
    req = dest / "requirements.txt"
    if req.exists():
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--retries", "10", "--timeout", "60", "-r", str(req)],
                       check=True)


DOWNLOAD_TRIES = 3


def hf_download(repo: str, path: str, local_dir: str, tries: int = DOWNLOAD_TRIES, sleep=time.sleep) -> Path:
    """hf_hub_download with retries (21 Nov 2026: Hugging Face xet failed every FLUX download on two pods with
    "File reconstruction error ... receiver dropped"). Short backoff between tries; the last try turns xet off for a
    plain-HTTP download. huggingface_hub reads HF_HUB_DISABLE_XET once at import, so the switch is made on
    huggingface_hub.constants itself (setting the env var alone does nothing then). Logs which try worked."""
    from huggingface_hub import constants, hf_hub_download

    for attempt in range(1, tries + 1):
        plain = attempt == tries and tries > 1
        old = constants.HF_HUB_DISABLE_XET
        if plain:
            constants.HF_HUB_DISABLE_XET = True
        try:
            local = Path(hf_hub_download(repo, path, local_dir=local_dir))
            log(f"{path}: downloaded on try {attempt}/{tries}" + (" (plain HTTP, xet off)" if plain else ""))
            return local
        except Exception as e:  # noqa: BLE001 - network trouble: try again, the last time without xet
            log(f"{path}: download try {attempt}/{tries} failed: {type(e).__name__}: {e}"[:400])
            if attempt == tries:
                raise
            sleep(5 * attempt)
        finally:
            if plain:
                constants.HF_HUB_DISABLE_XET = old
    raise RuntimeError("unreachable")


def fetch_files(comfy: Path, state: State, files: dict, volume_dir: str) -> None:
    """Puts each (repo, path, subfolder, size) of `files` into ComfyUI's models folder, from the volume when cached."""
    weights = ROOT / "weights"
    for i, (name, (repo, path, sub, size)) in enumerate(files.items(), 1):
        t = time.time()
        cached = volume_file(VOLUME, name, size, volume_dir)
        if cached is not None:
            state.phase = f"using volume copy {i}/{len(files)} {name}"
            local, how = cached, "volume"
        else:
            state.phase = f"downloading {i}/{len(files)} {name}"
            local = hf_download(repo, path, str(weights))
            if local.stat().st_size != size:
                raise RuntimeError(f"{name}: size {local.stat().st_size} != expected {size}")
            how = "huggingface"
            if VOLUME:
                state.phase = f"caching {i}/{len(files)} {name} on the volume"
                copy_to_volume(VOLUME, name, local, volume_dir)
        dest = comfy / "models" / sub / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        dest.symlink_to(local)
        log(f"{name} {size / 1e9:.1f} GB from {how} in {time.time() - t:.0f} s")


def download(comfy: Path, state: State) -> None:
    global ZIMAGE_PATH
    from huggingface_hub import snapshot_download

    fetch_files(comfy, state, FILES, "wan-fp8")
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
    """Runs one job at a time. Z-Image (diffusers) and the ComfyUI models (Wan experts, FLUX.2 klein) never share the
    VRAM. A model stays loaded between jobs: Z-Image until a ComfyUI job needs the card, FLUX in ComfyUI's own cache
    (never freed per picture), so send the pictures of one model back to back."""

    def __init__(self, comfy: Path | None = None, state: State | None = None,
                 proc: subprocess.Popen | None = None):
        self.zimage = None
        self.comfy, self.state = comfy, state
        self.flux_ready = False
        self.proc = proc
        self.svi_ready = False

    def ensure_svi(self) -> None:
        """First scene job only: KJNodes + the two SVI LoRAs, then a ComfyUI restart (custom nodes load at start).
        Normal Shorts never reach this, so their boot stays as it was."""
        if self.svi_ready:
            return
        t = time.time()
        fetch_kjnodes(self.comfy)
        fetch_files(self.comfy, self.state or State(), SVI_FILES, "svi-pro")
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = start_comfy(self.comfy)
        info = http_json(f"http://127.0.0.1:{COMFY_PORT}/object_info/WanImageToVideoSVIPro")
        if "WanImageToVideoSVIPro" not in (info or {}):
            raise RuntimeError("KJNodes loaded without WanImageToVideoSVIPro")
        self.svi_ready = True
        log(f"SVI ready in {time.time() - t:.0f} s")

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
        if kind == "scene":
            return self.scene(p)
        if kind == "picture" and (p.get("model") or "zimage") == "flux2-klein":
            return self.flux_picture(p)
        return self.picture(p) if kind == "picture" else self.clip(p)

    def _run_graph(self, graph: dict) -> dict:
        """Queues a ComfyUI graph and waits for its history entry (raises on a ComfyUI error)."""
        pid = http_json(f"http://127.0.0.1:{COMFY_PORT}/prompt", {"prompt": graph})["prompt_id"]
        while True:
            hist = http_json(f"http://127.0.0.1:{COMFY_PORT}/history/{pid}").get(pid)
            status = (hist or {}).get("status", {})
            if status.get("status_str") == "error":
                msgs = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                raise RuntimeError(msgs[0][1].get("exception_message", "ComfyUI error") if msgs else "ComfyUI error")
            if hist and hist.get("outputs"):
                return hist
            time.sleep(1)

    def flux_picture(self, p: dict) -> tuple[bytes, dict]:
        if not self.flux_ready:
            if self.comfy is None or self.state is None:
                raise RuntimeError("FLUX.2 klein is not set up on this worker")
            fetch_files(self.comfy, self.state, FLUX_FILES, "flux2-klein")
            self.flux_ready = True
        self._free_zimage()  # ComfyUI keeps FLUX cached between pictures; only Z-Image's diffusers copy must go
        hist = self._run_graph(flux_graph(p, f"pic/{uuid.uuid4().hex[:12]}"))
        im = hist["outputs"]["13"]["images"][0]
        f = ROOT / "comfy" / "out" / im.get("subfolder", "") / im["filename"]
        data = f.read_bytes()
        f.unlink(missing_ok=True)
        return data, {"seed": int(p.get("seed") or 0), "model": "flux2-klein"}

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
        hist = self._run_graph(clip_graph(c, name, f"clip/{jid}", end_name))
        images = hist["outputs"]["16"]["images"]
        frames = [ROOT / "comfy" / "out" / im.get("subfolder", "") / im["filename"] for im in images]
        fps = int(c.get("fps", 16))
        data = encode_frames(frames, fps)
        (ROOT / "comfy" / "in" / name).unlink(missing_ok=True)
        return data, {"frames": len(frames), "fps": fps}

    def scene(self, c: dict) -> tuple[bytes, dict]:
        """One SVI scene (or the rest of it, from startBeat): all beats in one ComfyUI prompt. Returns a zip with each
        beat's MP4 (81 frames; the laptop does the 12-frame cross-fade) and its latent (for a later resume)."""
        from PIL import Image

        self._free_zimage()
        self.ensure_svi()
        jid = uuid.uuid4().hex[:12]
        name = f"{jid}.png"
        Image.open(io.BytesIO(base64.b64decode(c["image"]))).convert("RGB") \
            .resize((c["width"], c["height"]), Image.LANCZOS).save(ROOT / "comfy" / "in" / name)
        prev = None
        if c.get("prevLatent"):
            prev = f"{jid}-prev.latent"
            (ROOT / "comfy" / "in" / prev).write_bytes(base64.b64decode(c["prevLatent"]))
        start = int(c.get("startBeat", 1))
        prefix = f"scene/{jid}"
        hist = self._run_graph(scene_graph(c, name, prefix, prev))
        fps = int(c.get("fps", 16))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for k in range(start, start + len(c["beats"])):
                z.writestr(f"beat{k}.mp4", encode_frames(frames_of(hist, f"s{k}"), fps))
                lat = sorted((ROOT / "comfy" / "out" / "scene").glob(f"{jid}_b{k}_*.latent"))
                if not lat:
                    raise RuntimeError(f"beat {k}: no latent saved")
                z.writestr(f"beat{k}.latent", lat[-1].read_bytes())
                for f in lat:
                    f.unlink(missing_ok=True)
        for f in (name, prev):
            if f:
                (ROOT / "comfy" / "in" / f).unlink(missing_ok=True)
        return buf.getvalue(), {"beats": len(c["beats"]), "startBeat": start, "fps": fps}


def frames_of(hist: dict, node: str) -> list[Path]:
    return [ROOT / "comfy" / "out" / im.get("subfolder", "") / im["filename"] for im in hist["outputs"][node]["images"]]


def encode_frames(frames: list[Path], fps: int) -> bytes:
    """PNG frames -> H.264 MP4 (CRF 12, as every studio clip) and the PNGs deleted."""
    with tempfile.TemporaryDirectory() as tmp:
        for i, f in enumerate(frames):
            os.symlink(f, Path(tmp) / f"{i:05d}.png")
        mp4 = Path(tmp) / "clip.mp4"
        subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-framerate", str(fps), "-i", f"{tmp}/%05d.png",
                        "-c:v", "libx264", "-crf", "12", "-preset", "slow", "-pix_fmt", "yuv420p", str(mp4)], check=True)
        data = mp4.read_bytes()
    for f in frames:
        f.unlink(missing_ok=True)
    return data


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
        proc = start_comfy(comfy)
        gpu = Gpu(comfy, state, proc)
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
        scene_graph({"beats": [{"prompt": "a", "seed": 1}, {"prompt": "b", "seed": 2}], "width": 480, "height": 832},
                    "x.png", "scene/x")
        flux_graph({"prompt": "test", "seed": 1, "width": 768, "height": 1344}, "pic/x")
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
