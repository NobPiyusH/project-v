"""Independent-motion score: how much of a shot's motion is NOT explained by one global camera move.

Why: a panned or zoomed still has a big, steady frame difference (the whole image slides), so the plain frame-difference motion score in tier2
rates it as lively. Calibrated 25 Nov 2026: a panned still scored 4.2 there, higher than real but gentle shots (0.9-1.9).
Method (numpy only): sample frames at `lm_fps`, take pairs half a second apart, estimate a block-wise translation (phase correlation on a
4x4 grid), fit ONE affine camera model (pan, zoom, rotate) to those vectors, warp the earlier frame with it, and measure what is left against
the later frame. A slid or zoomed still leaves almost nothing; real subject motion, parallax and animation leave a lot.
numpy is optional: without it the check is reported as SKIP. It is not a quality judgement, only a cheap test that something really moves.
"""

from __future__ import annotations

import subprocess

from .config import THRESHOLDS
from .ffprobe import _exe
from .types import CheckResult, Shot, Status

try:
    import numpy as np
except ImportError:  # pragma: no cover - exercised only where numpy is missing
    np = None  # type: ignore[assignment]


def available() -> bool:
    return np is not None


def read_gray_frames(video: str, width: int, height: int, fps: float, cfg: dict[str, float] = THRESHOLDS):
    """Gray frames at `fps`, scaled to the analysis width. Returns an array (n, h, w) of float32."""
    w = int(cfg["lm_width"])
    h = int(round(w * height / width / 2) * 2)
    raw = subprocess.run([_exe("ffmpeg"), "-v", "error", "-i", video, "-an", "-vf", f"fps={fps},scale={w}:{h},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout  # fmt: skip
    n = len(raw) // (w * h)
    return np.frombuffer(raw[: n * w * h], np.uint8).reshape(n, h, w).astype(np.float32)


def _shift(a, b) -> tuple[float, float]:
    """Sub-pixel translation of block b relative to block a by phase correlation."""
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    fa, fb = np.fft.fft2((a - a.mean()) * win), np.fft.fft2((b - b.mean()) * win)
    r = fb * np.conj(fa)
    r /= np.abs(r) + 1e-6
    c = np.fft.fftshift(np.real(np.fft.ifft2(r)))
    y, x = np.unravel_index(np.argmax(c), c.shape)

    def refine(lo: float, mid: float, hi: float, p: int) -> float:
        d = lo - 2 * mid + hi
        return p + (0.5 * (lo - hi) / d if abs(d) > 1e-9 else 0.0)

    ys = refine(c[y - 1, x], c[y, x], c[y + 1, x], y) if 0 < y < c.shape[0] - 1 else float(y)
    xs = refine(c[y, x - 1], c[y, x], c[y, x + 1], x) if 0 < x < c.shape[1] - 1 else float(x)
    return xs - a.shape[1] // 2, ys - a.shape[0] // 2


def _warp(a, lin, off):
    h, w = a.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    x2 = np.clip(lin[0, 0] * xx + lin[0, 1] * yy + off[0], 0, w - 1.001)
    y2 = np.clip(lin[1, 0] * xx + lin[1, 1] * yy + off[1], 0, h - 1.001)
    x0, y0 = x2.astype(int), y2.astype(int)
    fx, fy = x2 - x0, y2 - y0
    return (a[y0, x0] * (1 - fx) * (1 - fy) + a[y0, x0 + 1] * fx * (1 - fy)
            + a[y0 + 1, x0] * (1 - fx) * fy + a[y0 + 1, x0 + 1] * fx * fy)  # fmt: skip


def residual_after_camera(a, b) -> float:
    """Mean |b - warp(a)| (0..255) over the central area after the best global affine camera move is removed."""
    g = 4
    h, w = a.shape
    bh, bw = h // g, w // g
    pts, vecs = [], []
    for i in range(g):
        for j in range(g):
            sl = (slice(i * bh, (i + 1) * bh), slice(j * bw, (j + 1) * bw))
            dx, dy = _shift(a[sl], b[sl])
            pts.append((j * bw + bw / 2, i * bh + bh / 2))
            vecs.append((dx, dy))
    p, v = np.array(pts), np.array(vecs)
    x = np.c_[p, np.ones(len(p))]
    keep = np.ones(len(p), bool)
    for _ in range(2):  # second pass drops blocks that move on their own (subject motion) so they do not bend the camera fit
        coef, *_ = np.linalg.lstsq(x[keep], v[keep], rcond=None)
        err = np.linalg.norm(x @ coef - v, axis=1)
        keep = err <= max(float(np.median(err)) * 2, 0.3)
    lin, off = np.eye(2) - coef[:2].T, -coef[2]
    m = int(0.12 * min(h, w))
    inner = (slice(m, h - m), slice(m, w - m))
    return float(np.abs(b - _warp(a, lin, off))[inner].mean())


def shot_independent_motion(frames, fps: float, shot: Shot, cfg: dict[str, float] = THRESHOLDS) -> float | None:
    """Mean residual over frame pairs `lm_gap_s` apart that lie inside the shot; None when the shot is too short for a pair."""
    k = max(1, round(cfg["lm_gap_s"] * fps))
    first, last = int(shot.start * fps + 0.999), int(shot.end * fps)
    scores = [residual_after_camera(frames[i], frames[i + k]) for i in range(first, min(last, len(frames)) - k)]
    return sum(scores) / len(scores) if scores else None


def check_independent_motion(value: float | None, shot: Shot, cfg: dict[str, float] = THRESHOLDS) -> CheckResult:
    if value is None:
        return CheckResult("independent_motion", Status.SKIP, message="shot too short to measure", shot=shot.id)
    status = Status.FAIL if value < cfg["lm_fail_below"] else Status.WARN if value < cfg["lm_warn_below"] else Status.PASS
    return CheckResult("independent_motion", status, round(value, 3), "", ((shot.start, shot.end),),
                       f"motion left after removing the camera move: {value:.2f} (slid/zoomed still or static below {cfg['lm_warn_below']})",
                       shot.id)  # fmt: skip
