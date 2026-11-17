"""Smooth motion on the laptop's own GPU (RTX 3050, 18 Nov 2026): Practical-RIFE v4.25, the same pinned code and
weights as the projectv-finish Modal app, re-times a 16 fps clip to 24 fps. Free, and it keeps working when Modal is
busy; the activity falls back to Modal when there is no CUDA GPU or this fails.

Weights and code come from Hugging Face (rule 9) and the official GitHub repo, both pinned; loaded tensor-only.
Frames go through FFmpeg pipes, so no extra video library is needed.
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from workers.common import ffmpeg_bin, ffprobe_bin, log

RIFE_REPO = "MonsterMMORPG/Practical-RIFE"
RIFE_REVISION = "4bbe4c19fb5363333cca83b6e82bb65ee02d741b"  # v4.25, same as workers/modal_apps/finish.py
UPSTREAM_RAW = "https://raw.githubusercontent.com/hzwer/Practical-RIFE/bbfd2ea90910789a860ea3e2b32a240cd577b75e"

_net = None


def available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:  # noqa: BLE001 - torch missing or broken: use Modal
        return False


def _load():
    global _net
    if _net is not None:
        return _net
    import torch
    from huggingface_hub import snapshot_download

    t = time.time()
    root = Path(snapshot_download(RIFE_REPO, revision=RIFE_REVISION,
                                  allow_patterns=["train_log/*.py", "train_log/flownet.pkl"]))
    warp = root / "model" / "warplayer.py"
    if not warp.exists():  # helper module from the official repo, pinned commit
        warp.parent.mkdir(parents=True, exist_ok=True)
        (warp.parent / "__init__.py").touch()
        with urllib.request.urlopen(f"{UPSTREAM_RAW}/model/warplayer.py", timeout=60) as r:
            warp.write_bytes(r.read())
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from train_log.IFNet_HDv3 import IFNet  # noqa: E402  (code from the pinned mirror)

    net = IFNet()
    state = torch.load(root / "train_log" / "flownet.pkl", map_location="cpu", weights_only=True)
    missing, _ = net.load_state_dict({k.replace("module.", ""): v for k, v in state.items()}, strict=False)
    if missing:
        raise RuntimeError(f"RIFE weights don't match the code: {len(missing)} missing")
    _net = net.to("cuda").eval()  # full precision: RIFE mixes dtypes internally
    log.info("Local RIFE v4.25 loaded in %.1fs", time.time() - t)
    return _net


def plan_frames(n_src: int, src_fps: float, dst_fps: float) -> list[tuple[int, float]]:
    """For each output frame: (source frame index, fraction toward the next one). Same duration, new frame rate."""
    n_out = max(1, round(n_src / src_fps * dst_fps))
    out = []
    for k in range(n_out):
        pos = min(k * src_fps / dst_fps, n_src - 1)
        out.append((int(pos), pos - int(pos)))
    return out


def _probe(path: Path) -> tuple[int, int]:
    r = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    w, h = r.stdout.strip().split(",")[:2]
    return int(w), int(h)


def smooth(src: Path, dst: Path, src_fps: float = 16, dst_fps: float = 24) -> dict:
    """One clip -> dst (24 fps, CRF 12). Returns {seconds, frames, fps, srcFrames}."""
    import numpy as np
    import torch
    import torch.nn.functional as F

    t0 = time.time()
    net = _load()
    w, h = _probe(src)
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(src), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)
    ph, pw = ((h - 1) // 128 + 1) * 128, ((w - 1) // 128 + 1) * 128

    def tensor(f):
        x = torch.from_numpy(np.ascontiguousarray(f)).to("cuda").permute(2, 0, 1).unsqueeze(0).float() / 255.0
        return F.pad(x, (0, pw - w, 0, ph - h))

    plan = plan_frames(len(frames), src_fps, dst_fps)
    enc = subprocess.Popen([ffmpeg_bin(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                            "-r", str(dst_fps), "-i", "-", "-c:v", "libx264", "-crf", "12", "-preset", "slow",
                            "-pix_fmt", "yuv420p", str(dst)], stdin=subprocess.PIPE)
    cache: dict[int, object] = {}

    def src_t(i):  # keep only the two frames in use on the 4 GB GPU
        if i not in cache:
            for k in [k for k in cache if k < i - 1]:
                del cache[k]
            cache[i] = tensor(frames[i])
        return cache[i]

    try:
        with torch.no_grad():
            for i, frac in plan:
                if frac < 1e-3 or i + 1 >= len(frames):
                    img = src_t(i)
                else:
                    a, b = src_t(i), src_t(i + 1)
                    img = net(torch.cat((a, b), 1), frac, [16, 8, 4, 2, 1])[2][-1]  # v4.25: five scales
                arr = (img[0, :, :h, :w].float().clamp(0, 1) * 255).round().byte().permute(1, 2, 0).cpu().numpy()
                enc.stdin.write(arr.tobytes())
    finally:
        enc.stdin.close()
        enc.wait()
    if enc.returncode != 0:
        raise RuntimeError(f"FFmpeg encode failed ({enc.returncode})")
    return {"seconds": round(time.time() - t0, 2), "frames": len(plan), "fps": dst_fps, "srcFrames": len(frames)}
