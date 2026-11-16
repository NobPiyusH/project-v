"""Z-Image Turbo (Apache-2.0) keyframe generator on Modal.

Deploy:  uv run modal deploy workers/modal_apps/zimage.py
Called by the media worker via modal.Cls.from_name("projectv-zimage", "ZImage").

Design: one call renders a whole batch of keyframes, so the model is loaded once per video, not once per image.
Weights come from Hugging Face and are cached in a Modal Volume (CLAUDE.md rule 9: never from Google Drive).
"""

import io
import time

import modal

APP_NAME = "projectv-zimage"
MODEL_ID = "Tongyi-MAI/Z-Image-Turbo"

app = modal.App(APP_NAME)

image = modal.Image.debian_slim(python_version="3.11").pip_install(
    "torch==2.8.0",
    "diffusers>=0.36.0",
    "transformers>=4.57.0",
    "accelerate>=1.10",
    "safetensors",
    "sentencepiece",
    "pillow",
)

hf_cache = modal.Volume.from_name("projectv-hf-cache", create_if_missing=True)


@app.cls(
    gpu="L40S",
    image=image,
    volumes={"/cache": hf_cache},
    timeout=1800,
    scaledown_window=120,  # shut down 2 min after the last call (CLAUDE.md rule 8)
)
class ZImage:
    @modal.enter()
    def load(self) -> None:
        import torch
        from diffusers import ZImagePipeline

        t = time.time()
        self.pipe = ZImagePipeline.from_pretrained(
            MODEL_ID, torch_dtype=torch.bfloat16, cache_dir="/cache/hf"
        ).to("cuda")
        hf_cache.commit()
        self.load_seconds = time.time() - t

    @modal.method()
    def generate(self, jobs: list[dict]) -> list[dict]:
        """jobs: [{id, prompt, width, height, seed}] -> [{id, png: bytes, seconds, seed}]"""
        import torch

        out = []
        for job in jobs:
            t = time.time()
            seed = int(job.get("seed") or 0)
            generator = torch.Generator("cuda").manual_seed(seed)
            img = self.pipe(
                prompt=job["prompt"],
                width=int(job.get("width", 1344)),
                height=int(job.get("height", 768)),
                num_inference_steps=9,  # Turbo is distilled for ~8 NFEs
                guidance_scale=0.0,
                generator=generator,
            ).images[0]
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            out.append({"id": job["id"], "png": buf.getvalue(), "seconds": time.time() - t, "seed": seed})
        return out

    @modal.method()
    def info(self) -> dict:
        return {"model": MODEL_ID, "load_seconds": getattr(self, "load_seconds", None)}
