"""LTX-2 image-to-video on Modal (18 Nov 2026): A/B candidate against Wan 2.2 (cheaper/faster per clip?).

Deploy:  uv run modal deploy workers/modal_apps/ltx.py

Model: Lightricks LTX-2 19B (LTX-2 Community License: free commercial use for entities under $10M annual revenue;
attribution notices kept; Attachment A use restrictions apply). Weights from Hugging Face at a pinned revision
(rule 9), baked into the image; diffusers pinned to a commit that has LTX2ImageToVideoPipeline.
One H100; model CPU offload keeps the 19B transformer + text encoder within 80 GB; 30 s idle shutdown (rule 8).
Not wired into productions: test only until an A/B shows it holds up (owner's quality rule).
"""

import io
import time

import modal

APP_NAME = "projectv-ltx"
REPO = "Lightricks/LTX-2"
REVISION = "64f65d09e0b76c96202b5bc09997673184cf9b5d"
DIFFUSERS_COMMIT = "e84bda1c0005da3f48cc4c899635438cb0aa13c1"
WEIGHTS = "/models/ltx2"

app = modal.App(APP_NAME)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "git")
    .pip_install("torch==2.8.0", "torchvision==0.23.0", "transformers>=4.57", "accelerate", "safetensors",
                 "sentencepiece", "huggingface_hub>=0.35", "imageio[ffmpeg]", "av", "pillow", "numpy<2.3",
                 f"git+https://github.com/huggingface/diffusers@{DIFFUSERS_COMMIT}")
    .run_commands(
        f"python -c \"from huggingface_hub import snapshot_download; snapshot_download('{REPO}', revision='{REVISION}', "
        f"local_dir='{WEIGHTS}', allow_patterns=['model_index.json', 'transformer/*', 'vae/*', 'audio_vae/*', "
        f"'connectors/*', 'text_encoder/*', 'tokenizer/*', 'scheduler/*', 'vocoder/*'])\"",
    )
)


@app.cls(image=image, gpu="H100", timeout=3600, scaledown_window=30, max_containers=1, memory=131072)
class Ltx:

    @modal.enter()
    def load(self) -> None:
        import torch
        from diffusers import LTX2ImageToVideoPipeline

        t = time.time()
        self.pipe = LTX2ImageToVideoPipeline.from_pretrained(WEIGHTS, torch_dtype=torch.bfloat16)
        self.pipe.enable_model_cpu_offload()
        self.load_seconds = round(time.time() - t, 1)
        print(f"LTX-2 loaded in {self.load_seconds}s")

    @modal.method()
    def generate(self, image_bytes: bytes, prompt: str, width: int = 576, height: int = 1024, num_frames: int = 121,
                 steps: int = 30, seed: int = 7) -> dict:
        import imageio.v2 as imageio
        import numpy as np
        import torch
        from PIL import Image

        t = time.time()
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((width, height), Image.LANCZOS)
        video, _audio = self.pipe(
            image=img, prompt=prompt,
            negative_prompt="worst quality, inconsistent motion, blurry, jittery, distorted, text, letters, watermark",
            width=width, height=height, num_frames=num_frames, frame_rate=24.0, num_inference_steps=steps,
            guidance_scale=3.0, output_type="np", return_dict=False,
            generator=torch.Generator("cuda").manual_seed(seed))
        frames = (np.clip(video[0], 0, 1) * 255).round().astype("uint8")
        path = "/tmp/ltx.mp4"
        imageio.mimwrite(path, list(frames), fps=24, codec="libx264", quality=None,
                         ffmpeg_params=["-crf", "14", "-pix_fmt", "yuv420p"])
        return {"mp4": open(path, "rb").read(), "seconds": round(time.time() - t, 1), "frames": len(frames),
                "loadSeconds": self.load_seconds}
