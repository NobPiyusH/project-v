"""Shot-cut detection (free, ffmpeg `scene` score) so a video can be split into shots without a hand-written shots.json."""

from __future__ import annotations

import re

from .config import THRESHOLDS
from .types import Shot

_CUT = re.compile(r"pts_time:([\d.]+)")
_SCORE = re.compile(r"lavfi\.scene_score=([\d.]+)")


def cut_args(cfg: dict[str, float] = THRESHOLDS) -> list[str]:
    return ["-an", "-vf", f"scale=320:-2,select='gt(scene,{cfg['scene_threshold']})',metadata=print:key=lavfi.scene_score"]


def parse_cuts(stderr: str) -> list[float]:
    """Times (s) of frames whose scene score passed the threshold."""
    times: list[float] = []
    for line in stderr.splitlines():
        m = _CUT.search(line)
        if m and "frame:" in line:
            times.append(float(m.group(1)))
    return times


def shots_from_cuts(cuts: list[float], duration: float, cfg: dict[str, float] = THRESHOLDS) -> list[Shot]:
    """Shots between cuts. A cut closer than `min_shot_s` to the previous boundary is dropped (flash or fade, not a new shot)."""
    bounds = [0.0]
    for t in sorted(cuts):
        if t - bounds[-1] >= cfg["min_shot_s"] and duration - t >= cfg["min_shot_s"]:
            bounds.append(t)
    bounds.append(duration)
    return [Shot(f"shot{i + 1:02d}", a, b) for i, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False))]
