"""Finishing on a GPU (M2c part 2a): smooth motion. Wan renders 16 fps, which looks steppy; Practical-RIFE v4.25 (MIT)
synthesises real in-between frames so every clip plays at a true 24 fps (cinematic), instead of repeating frames.

Deploy:  uv run modal deploy workers/modal_apps/finish.py

Weights come from Hugging Face (CLAUDE.md rule 9), never Google Drive: the matched v4.25 code + weights mirror
MonsterMMORPG/Practical-RIFE (upstream hzwer/Practical-RIFE, MIT), pinned to a revision. They are loaded strictly
(every tensor must match the code) and tensor-only (no pickled code runs). Two helper modules (warp layer, losses)
come from the official GitHub repo at a pinned commit. Upscaling (SeedVR2) is part 2b.

GPU: one L4 for a whole batch (RIFE is light); the container scales down after 2 idle minutes (rule 8).
"""

import io
import time

import modal

APP_NAME = "projectv-finish"
RIFE_REPO = "MonsterMMORPG/Practical-RIFE"
RIFE_REVISION = "4bbe4c19fb5363333cca83b6e82bb65ee02d741b"     # v4.25 (verified in RIFE_HDv3.py: version = 4.25)
UPSTREAM = "https://github.com/hzwer/Practical-RIFE"
UPSTREAM_COMMIT = "bbfd2ea90910789a860ea3e2b32a240cd577b75e"
CODE = "/opt/rife"

app = modal.App(APP_NAME)

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ffmpeg", "git")
    .pip_install("torch==2.8.0", "numpy<2.3", "huggingface_hub>=0.35", "imageio[ffmpeg]")
    .run_commands(
        f"git clone {UPSTREAM} {CODE} && cd {CODE} && git checkout {UPSTREAM_COMMIT}",
        f"python -c \"from huggingface_hub import snapshot_download; snapshot_download('{RIFE_REPO}', "
        f"revision='{RIFE_REVISION}', allow_patterns=['train_log/*.py', 'train_log/flownet.pkl'], local_dir='{CODE}')\"",
    )
)

progress = modal.Dict.from_name("projectv-progress", create_if_missing=True)


def plan_frames(n_src: int, src_fps: float, dst_fps: float) -> list[tuple[int, float]]:
    """For each output frame: (source frame index, fraction toward the next one). Same duration, new frame rate."""
    duration = n_src / src_fps
    n_out = max(1, round(duration * dst_fps))
    out = []
    for k in range(n_out):
        pos = min(k * src_fps / dst_fps, n_src - 1)
        i = int(pos)
        out.append((i, pos - i))
    return out


@app.cls(image=image, gpu="L4", timeout=3600, scaledown_window=120, max_containers=1, memory=16384)
class Smooth:

    @modal.enter()
    def load(self) -> None:
        import sys

        import torch

        sys.path.insert(0, CODE)
        from train_log.IFNet_HDv3 import IFNet  # noqa: E402  (code from the pinned mirror)

        t = time.time()
        net = IFNet()
        state = torch.load(f"{CODE}/train_log/flownet.pkl", map_location="cpu", weights_only=True)
        state = {k.replace("module.", ""): v for k, v in state.items()}
        missing, unexpected = net.load_state_dict(state, strict=False)
        if missing:  # every weight the code needs must be present; extras (training-only) are fine
            raise RuntimeError(f"RIFE weights don't match the code: {len(missing)} missing, e.g. {missing[:3]}")
        self.net = net.to("cuda").eval().half()
        self.load_seconds = round(time.time() - t, 1)
        print(f"RIFE v4.25 loaded in {self.load_seconds}s ({len(unexpected)} unused keys)")

    def _interp(self, a, b, t: float):
        import torch

        with torch.no_grad():
            return self.net(torch.cat((a, b), 1), timestep=t, scale_list=[8, 4, 2, 1])[2][-1]

    def _run(self, job: dict) -> dict:
        import imageio.v2 as imageio
        import numpy as np
        import torch
        import torch.nn.functional as F

        t0 = time.time()
        try:
            reader = imageio.get_reader(io.BytesIO(job["mp4"]), format="ffmpeg")
            meta = reader.get_meta_data()
            src_fps = float(job.get("srcFps") or meta.get("fps") or 16)
            frames = [f for f in reader]
            reader.close()
            dst_fps = float(job.get("dstFps") or 24)
            h, w = frames[0].shape[:2]
            ph, pw = ((h - 1) // 128 + 1) * 128, ((w - 1) // 128 + 1) * 128

            def tensor(f):
                x = torch.from_numpy(np.ascontiguousarray(f)).to("cuda").permute(2, 0, 1).unsqueeze(0).half() / 255.0
                return F.pad(x, (0, pw - w, 0, ph - h))

            src = [tensor(f) for f in frames]
            plan = plan_frames(len(src), src_fps, dst_fps)
            out_path = f"/tmp/{job['id']}.mp4"
            writer = imageio.get_writer(out_path, fps=dst_fps, codec="libx264", quality=None, macro_block_size=16,
                                        ffmpeg_params=["-crf", "12", "-preset", "slow", "-pix_fmt", "yuv420p"])
            try:
                for n, (i, frac) in enumerate(plan):
                    if frac < 1e-3 or i + 1 >= len(src):
                        img = src[i]
                    else:
                        img = self._interp(src[i], src[i + 1], frac)
                    arr = (img[0, :, :h, :w].float().clamp(0, 1) * 255).round().byte().permute(1, 2, 0).cpu().numpy()
                    writer.append_data(arr)
                    if n % 24 == 0:
                        progress[job["id"]] = {"state": "smoothing", "step": n, "steps": len(plan)}
            finally:
                writer.close()
            with open(out_path, "rb") as f:
                data = f.read()
            return {"id": job["id"], "mp4": data, "seconds": round(time.time() - t0, 2), "frames": len(plan),
                    "fps": dst_fps, "srcFrames": len(src), "error": None}
        except Exception as e:  # one bad clip must not sink the batch
            return {"id": job["id"], "mp4": None, "seconds": round(time.time() - t0, 2), "error": f"{type(e).__name__}: {e}"[:500]}

    @modal.method(is_generator=True)
    def smooth_stream(self, jobs: list[dict], stop_key: str | None = None):
        """Clips in turn on one GPU, each yielded as soon as it is done; honours the stop flag between clips."""
        for job in jobs:
            if stop_key and progress.get(stop_key):
                yield {"id": job["id"], "mp4": None, "seconds": 0, "error": "stopped"}
                continue
            yield self._run(job)

    @modal.method()
    def info(self) -> dict:
        return {"model": "Practical-RIFE v4.25", "revision": RIFE_REVISION, "load_seconds": getattr(self, "load_seconds", None)}
