"""Wan 2.2 image-to-video (A14B, Apache-2.0) on Modal: turns a shot's keyframe into a real moving clip.

Deploy:     uv run modal deploy workers/modal_apps/wan.py
Pre-fetch:  uv run modal run workers/modal_apps/wan.py::download   (CPU only: no GPU billed while downloading ~70 GB)

Efficiency: the lightx2v 4-step distillation LoRAs (Apache-2.0) cut sampling from ~30-40 steps to 4. Both modes run in
the same container (the LoRAs are switched on/off per clip), so quality can be compared without a second model load.
Weights live in a Modal Volume from Hugging Face (CLAUDE.md rule 9); the container scales down after 2 idle minutes.
"""

import io
import time

import modal

APP_NAME = "projectv-wan"
MODEL_ID = "Wan-AI/Wan2.2-I2V-A14B-Diffusers"
LORA_REPO = "lightx2v/Wan2.2-Distill-Loras"
LORA_HIGH = "wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors"
LORA_LOW = "wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors"
CACHE = "/cache/hf"

app = modal.App(APP_NAME)

image = modal.Image.debian_slim(python_version="3.11").apt_install("ffmpeg").pip_install(
    "torch==2.8.0",
    "diffusers>=0.43.0",
    "transformers>=4.57.0",
    "accelerate>=1.10",
    "peft>=0.17",
    "safetensors",
    "sentencepiece",
    "ftfy",
    "imageio[ffmpeg]",
    "pillow",
    "huggingface_hub>=0.35",
)

hf_cache = modal.Volume.from_name("projectv-hf-cache", create_if_missing=True)

NEGATIVE = ("blurry, low quality, low resolution, distorted, warped, morphing, flickering, jittery, text, captions, "
            "watermark, logo, frame border, static image, frozen, duplicate frames, oversaturated, cartoon")


@app.function(image=image, volumes={"/cache": hf_cache}, timeout=3600, cpu=4, memory=8192)
def download() -> str:
    """Fetch weights into the volume without a GPU attached."""
    from huggingface_hub import hf_hub_download, snapshot_download

    t = time.time()
    snapshot_download(MODEL_ID, cache_dir=CACHE)
    for name in (LORA_HIGH, LORA_LOW):
        hf_hub_download(LORA_REPO, name, cache_dir=CACHE)
    hf_cache.commit()
    return f"downloaded in {time.time() - t:.0f}s"


# Live progress per clip (written every denoising step; read by the media worker / dashboard).
progress = modal.Dict.from_name("projectv-progress", create_if_missing=True)


@app.cls(
    gpu="H100",
    image=image,
    volumes={"/cache": hf_cache},
    timeout=3600,
    memory=98304,          # room for the offload fallback
    scaledown_window=120,  # CLAUDE.md rule 8: never leave a GPU idling
    max_containers=4,      # parallel clips across at most 4 GPUs: faster, same total GPU time, capped spend rate
)
class Wan:
    @modal.enter()
    def load(self) -> None:
        import torch
        from diffusers import WanImageToVideoPipeline
        from huggingface_hub import hf_hub_download

        t = time.time()
        pipe = WanImageToVideoPipeline.from_pretrained(MODEL_ID, torch_dtype=torch.bfloat16, cache_dir=CACHE)
        # 4-step distillation LoRAs: one for each expert (high-noise -> transformer, low-noise -> transformer_2).
        pipe.load_lora_weights(hf_hub_download(LORA_REPO, LORA_HIGH, cache_dir=CACHE), adapter_name="fast")
        pipe.load_lora_weights(hf_hub_download(LORA_REPO, LORA_LOW, cache_dir=CACHE), adapter_name="fast_2",
                               load_into_transformer_2=True)
        # Work X: keep everything on the 80 GB H100. The first benchmark swapped experts to CPU every clip, which cost
        # ~3/4 of a fast clip's time. If memory ever runs out, _run() falls back to offloading and retries once.
        try:
            pipe.to("cuda")
            self.offloaded = False
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            pipe.enable_model_cpu_offload()
            self.offloaded = True
        self.pipe = pipe
        self.load_seconds = time.time() - t

    def _offload(self) -> None:
        import torch

        torch.cuda.empty_cache()
        self.pipe.to("cpu")
        self.pipe.enable_model_cpu_offload()
        self.offloaded = True

    # Quality modes (benchmarked 16 Nov 2026 on an H100, 720p, 81 frames):
    #   fast     - 4-step LoRA on both experts.            ~2 min/clip. Detail turns soft/mushy late in the clip.
    #   hybrid   - full model (with CFG) for the high-noise expert that sets composition and motion, 4-step LoRA
    #              only on the low-noise expert that adds detail. Middle ground in time and quality.
    #   standard - full model on both experts, 30 steps. ~19 min/clip. Best detail; for hero shots only.
    MODES = {
        "fast": {"steps": 4, "g1": 1.0, "g2": 1.0, "lora_high": True, "lora_low": True},
        "hybrid": {"steps": 12, "g1": 3.5, "g2": 1.0, "lora_high": False, "lora_low": True},
        "standard": {"steps": 30, "g1": 3.5, "g2": 3.5, "lora_high": False, "lora_low": False},
    }

    def _set_mode(self, cfg: dict) -> None:
        # Each expert carries its own LoRA, so the speed-up can be switched per expert.
        for module, on in ((self.pipe.transformer, cfg["lora_high"]), (self.pipe.transformer_2, cfg["lora_low"])):
            if on:
                module.enable_adapters()
            else:
                module.disable_adapters()

    @staticmethod
    def _encode(frames, path: str, fps: int) -> None:
        """High-quality H.264 (CRF 12). The diffusers default (~1.5 Mbps at 720p) visibly blurs stars and texture."""
        import imageio.v2 as imageio
        import numpy as np

        writer = imageio.get_writer(path, fps=fps, codec="libx264", quality=None, macro_block_size=16,
                                    ffmpeg_params=["-crf", "12", "-preset", "slow", "-pix_fmt", "yuv420p"])
        try:
            for f in frames:
                arr = np.asarray(f)
                if arr.dtype != np.uint8:
                    arr = (np.clip(arr, 0, 1) * 255).round().astype(np.uint8)
                writer.append_data(arr)
        finally:
            writer.close()

    def _render(self, job: dict, cfg: dict, steps: int):
        import torch
        from PIL import Image

        img = Image.open(io.BytesIO(job["image"])).convert("RGB")
        w, h = int(job.get("width", 1280)), int(job.get("height", 720))
        img = img.resize((w, h), Image.LANCZOS)
        started = time.time()

        def on_step(pipe, step, timestep, kwargs):
            # Progress for the dashboard: which step, and a time-left estimate from the steps done so far.
            done = step + 1
            elapsed = time.time() - started
            progress[job["id"]] = {"step": done, "steps": steps, "elapsed": round(elapsed, 1),
                                   "etaSec": round(elapsed / done * (steps - done), 1), "state": "rendering"}
            return kwargs

        return self.pipe(
            image=img,
            prompt=job["prompt"],
            negative_prompt=NEGATIVE,
            width=w,
            height=h,
            num_frames=int(job.get("numFrames", 81)),   # 81 frames = ~5 s at Wan's native 16 fps
            num_inference_steps=steps,
            guidance_scale=cfg["g1"],
            guidance_scale_2=cfg["g2"],
            generator=torch.Generator("cuda").manual_seed(int(job.get("seed") or 0)),
            callback_on_step_end=on_step,
        ).frames[0]

    def _run(self, job: dict) -> dict:
        """One clip. Never raises: errors come back in the result so one bad clip can't sink a batch."""
        import torch

        t = time.time()
        try:
            mode = job.get("mode") or ("fast" if job.get("fast", True) else "standard")
            cfg = self.MODES[mode]
            steps = int(job.get("steps") or cfg["steps"])
            self._set_mode(cfg)
            progress[job["id"]] = {"step": 0, "steps": steps, "elapsed": 0, "etaSec": None, "state": "starting"}
            try:
                frames = self._render(job, cfg, steps)
            except torch.cuda.OutOfMemoryError:
                if self.offloaded:
                    raise
                self._offload()   # fall back to swapping experts, then retry this clip once
                frames = self._render(job, cfg, steps)
            progress[job["id"]] = {"step": steps, "steps": steps, "elapsed": round(time.time() - t, 1), "etaSec": 0,
                                   "state": "encoding"}
            path = f"/tmp/{job['id']}.mp4"
            self._encode(frames, path, fps=16)
            with open(path, "rb") as f:
                data = f.read()
            progress[job["id"]] = {"step": steps, "steps": steps, "elapsed": round(time.time() - t, 1), "etaSec": 0,
                                   "state": "done"}
            return {"id": job["id"], "mp4": data, "seconds": time.time() - t, "frames": len(frames), "fps": 16,
                    "mode": mode, "steps": steps, "offloaded": self.offloaded, "error": None}
        except Exception as e:
            progress[job["id"]] = {"state": "failed", "error": f"{type(e).__name__}: {e}"[:300]}
            return {"id": job["id"], "mp4": None, "seconds": time.time() - t, "mode": job.get("mode"),
                    "error": f"{type(e).__name__}: {e}"[:600]}

    @modal.method()
    def generate_one(self, job: dict) -> dict:
        """One clip per call: callers use generate_one.map(jobs, order_outputs=False) so clips render in parallel
        on up to max_containers GPUs and each result arrives (and can be saved) as soon as it is finished."""
        return self._run(job)

    @modal.method(is_generator=True)
    def generate_stream(self, jobs: list[dict], stop_key: str | None = None):
        """Several clips in ONE container, one after another, each yielded as soon as it is finished. Used for drafts:
        the container start + model load (~2 min of H100 time) is paid once per batch instead of once per GPU.
        The caller sets progress[stop_key] to stop: the clips not started yet come back as "stopped"."""
        for job in jobs:
            if stop_key and progress.get(stop_key):
                yield {"id": job["id"], "mp4": None, "seconds": 0, "mode": job.get("mode"), "error": "stopped"}
                continue
            yield self._run(job)

    @modal.method()
    def generate(self, jobs: list[dict]) -> list[dict]:
        """Batch in one container (kept for the first benchmark script)."""
        return [self._run(job) for job in jobs]

    @modal.method()
    def info(self) -> dict:
        return {"model": MODEL_ID, "lora": LORA_REPO, "load_seconds": getattr(self, "load_seconds", None)}
