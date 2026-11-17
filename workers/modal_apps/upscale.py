"""Finishing on a GPU (M2c part 2b): sharpness. SeedVR2 (ByteDance-Seed, Apache-2.0) restores and upscales each clip
to a crisp 1080p-short-side frame (1080x1920 for a Short) with real recovered detail, temporally consistent across a
batch of frames, instead of a plain resize.

Deploy:  uv run modal deploy workers/modal_apps/upscale.py

Code: numz/ComfyUI-SeedVR2_VideoUpscaler (Apache-2.0) standalone CLI, pinned to a commit. Weights come from Hugging
Face (CLAUDE.md rule 9), numz/SeedVR2_comfyUI (Apache-2.0) at a pinned revision, baked into the image and checked
against the SHA-256 hashes in the code's own model registry. Quality first: the 7B model in fp16 (no fp8, no GGUF),
no block swapping (an H100's 80 GB holds it).

GPU: one H100 for a whole batch; all clips go through one CLI run so the model loads once; the container scales down
after 2 idle minutes (rule 8).
"""

import os
import shutil
import subprocess
import time

import modal

APP_NAME = "projectv-upscale"
CODE_REPO = "https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler"
CODE_COMMIT = "4490bd1f482e026674543386bb2a4d176da245b9"
WEIGHTS_REPO = "numz/SeedVR2_comfyUI"
WEIGHTS_REVISION = "09ced71023636e9bc8cdf9cdecfb2625d1e691e8"
DIT = "seedvr2_ema_7b_fp16.safetensors"
VAE = "ema_vae_fp16.safetensors"
CODE = "/opt/seedvr2"
MODELS = "/models"
FIT_MAX_FRAMES = 101  # one-batch "fit" mode tested at 85 frames (18 Nov 2026); longer clips keep 61-frame batches
PREPEND = 4  # reversed lead-in frames that steady the first frames; cut again after the run

app = modal.App(APP_NAME)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "git", "libglib2.0-0")
    .pip_install("torch==2.8.0", "torchvision==0.23.0", "safetensors", "numpy<2.3", "tqdm", "psutil", "einops",
                 "omegaconf>=2.3.0", "diffusers>=0.33.1", "peft>=0.17.0", "rotary_embedding_torch>=0.5.3",
                 "opencv-python-headless", "gguf", "matplotlib", "huggingface_hub>=0.35")
    .run_commands(
        f"git clone {CODE_REPO} {CODE} && cd {CODE} && git checkout {CODE_COMMIT}",
        f"python -c \"from huggingface_hub import hf_hub_download as d; "
        f"[d('{WEIGHTS_REPO}', f, revision='{WEIGHTS_REVISION}', local_dir='{MODELS}') for f in ('{DIT}', '{VAE}')]\"",
        # Hash-check both files now (the code's registry holds their SHA-256), so a bad download fails the build
        # and each container start skips the slow re-hash.
        f"cd {CODE} && python -c \"import sys; from src.utils.downloads import download_weight; "
        f"sys.exit(0 if download_weight(dit_model='{DIT}', vae_model='{VAE}', model_dir='{MODELS}') else 1)\"",
    )
)

progress = modal.Dict.from_name("projectv-progress", create_if_missing=True)


def fit_batch(frames: int) -> int:
    """Smallest 4n+1 batch that holds the whole clip: one batch, no padding (66 frames -> 69)."""
    return ((max(frames, 1) + 2) // 4) * 4 + 1


def batch_size_for(frames: int, cap: int = 61) -> int:
    """SeedVR2 wants 4n+1 frames per batch; a bigger batch means steadier detail over time. Whole clip when it fits."""
    n = min(max(frames, 1), cap)
    return ((n - 1) // 4) * 4 + 1 if n > 1 else 1


def frame_count(path: str) -> int:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-count_packets", "-show_entries",
                        "stream=nb_read_packets", "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 121


def fps_of(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=r_frame_rate",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        num, _, den = r.stdout.strip().partition("/")
        return round(float(num) / float(den or 1), 3)
    except (ValueError, ZeroDivisionError):
        return 24.0


# The whole batch runs in one call; idle time after it is pure cost, so the container goes after 30 s (rule 8).
@app.cls(image=image, gpu="H100", timeout=4 * 3600, scaledown_window=30, max_containers=1, memory=65536)
class Upscale:

    @modal.method(is_generator=True)
    def upscale_stream(self, jobs: list[dict], resolution: int = 1080, batch_size: int = 61,
                       stop_key: str | None = None, group: int = 8, fit: bool = False):
        """Clips in groups (one CLI run each, model loaded once per group), so progress shows and Stop works between
        groups; each finished clip is yielded with its share of its group's time."""
        for i in range(0, len(jobs), group):
            chunk = jobs[i:i + group]
            if stop_key and progress.get(stop_key):
                for job in chunk:
                    yield {"id": job["id"], "mp4": None, "seconds": 0, "error": "stopped"}
                continue
            for job in chunk:
                progress[job["id"]] = {"state": "upscaling", "step": 0, "steps": 1}
            yield from self._group(chunk, resolution, batch_size, fit)

    def _group(self, jobs: list[dict], resolution: int, batch_size: int, fit: bool = False):
        t0 = time.time()
        src, out = "/tmp/up-in", "/tmp/up-out"
        for d in (src, out):
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d)
        todo = []
        for job in jobs:
            with open(f"{src}/{job['id']}.mp4", "wb") as f:
                f.write(job["mp4"])
            todo.append(job)
        frames = max(frame_count(f"{src}/{j['id']}.mp4") for j in todo) + PREPEND
        fit = fit and frames <= FIT_MAX_FRAMES
        # fit (on test, 18 Nov 2026): every clip is one batch sized to the longest clip in the group, so nothing is
        # padded and no clip has a second batch that could "pop"; the default keeps 61-frame batches with padding.
        batching = (["--batch_size", str(fit_batch(frames))] if fit
                    else ["--batch_size", str(batch_size_for(frames, batch_size)), "--uniform_batch_size"])
        cmd = ["python", "inference_cli.py", src, "--output", out, "--output_format", "mp4", "--video_backend", "ffmpeg",
               "--model_dir", MODELS, "--dit_model", DIT, "--resolution", str(resolution), *batching,
               "--temporal_overlap", "4", "--prepend_frames", str(PREPEND), "--color_correction", "lab",
               # VAE tiles (1024 px, 128 px blended overlap) keep the 1080p decode of a long batch within memory.
               "--vae_encode_tiled", "--vae_decode_tiled", "--cache_dit", "--cache_vae"]
        print(" ".join(cmd))
        run = subprocess.run(cmd, cwd=CODE, capture_output=True, text=True)
        if fit and run.returncode != 0:  # e.g. out of memory on a long batch: redo this group the proven way
            print("fit mode failed; re-running this group with 61-frame batches")
            yield from self._group(jobs, resolution, batch_size, False)
            return
        log = (run.stdout + run.stderr)[-3000:]
        print(log)
        seconds = round(time.time() - t0, 2)
        for job in todo:
            path = f"{out}/{job['id']}.mp4"
            if run.returncode == 0 and os.path.exists(path):
                # The CLI keeps its PREPEND lead-in frames in the file (seen in the 2-clip test: 62 in, 66 out, the
                # picture 4 frames late), so cut them here to keep every clip in sync with the narration.
                fixed = f"{out}/{job['id']}-sync.mp4"
                cut = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-vf",
                                      f"trim=start_frame={PREPEND},setpts=PTS-STARTPTS", "-c:v", "libx264", "-crf",
                                      "12", "-preset", "slow", "-pix_fmt", "yuv420p", fixed],
                                     capture_output=True, text=True)
                if cut.returncode != 0:
                    yield {"id": job["id"], "mp4": None, "seconds": round(seconds / len(todo), 2),
                           "error": f"lead-in trim failed: {cut.stderr[-300:]}"}
                    continue
                with open(fixed, "rb") as f:
                    data = f.read()
                yield {"id": job["id"], "mp4": data, "seconds": round(seconds / len(todo), 2),
                       "frames": frame_count(fixed), "fps": fps_of(fixed), "error": None}
            else:
                yield {"id": job["id"], "mp4": None, "seconds": round(seconds / len(todo), 2),
                       "error": f"SeedVR2 exit {run.returncode}: {log[-400:]}"}

    @modal.method()
    def info(self) -> dict:
        return {"model": f"SeedVR2 {DIT}", "code": CODE_COMMIT, "weights": WEIGHTS_REVISION}
