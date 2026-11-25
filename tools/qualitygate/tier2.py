"""Tier 2: free local per-shot scores from ffmpeg frame statistics (no model, no GPU).

Two ffmpeg passes over the whole video (gray, scaled to `analysis_width`):
  pass A  signalstats           -> per frame YDIF (difference to the previous frame) and YAVG (mean luma)
  pass B  laplacian + signalstats -> per frame YAVG of the edge response = sharpness
Per shot we then take: motion = mean YDIF, sharpness = mean edge response, flicker = mean |second difference| of YAVG.
Limit: a panned still has a steady, non-zero YDIF, so motion only catches near-static shots; the Tier 3 judge checks real motion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import THRESHOLDS
from .types import CheckResult, Shot, Status

_LAP = "0 -1 0 -1 4 -1 0 -1 0"
_FRAME = re.compile(r"\] frame:\d+\s+pts:\S+\s+pts_time:([\d.]+)")
_STAT = re.compile(r"lavfi\.signalstats\.(\w+)=([-\d.eE+]+)")


def _prefix(cfg: dict[str, float]) -> str:
    return f"scale={int(cfg['analysis_width'])}:-2,format=gray"


def motion_args(cfg: dict[str, float] = THRESHOLDS) -> list[str]:
    return ["-an", "-vf", f"{_prefix(cfg)},signalstats,metadata=print"]


def sharpness_args(cfg: dict[str, float] = THRESHOLDS) -> list[str]:
    kernel = ":".join([_LAP] * 4)
    return ["-an", "-vf", f"{_prefix(cfg)},convolution='{kernel}',signalstats,metadata=print"]


@dataclass(frozen=True)
class FrameStat:
    t: float
    stats: dict[str, float]


def parse_signalstats(stderr: str) -> list[FrameStat]:
    """Frames in order with every signalstats key printed for them (`metadata=print` lines in ffmpeg's log)."""
    frames: list[FrameStat] = []
    for line in stderr.splitlines():
        m = _FRAME.search(line)
        if m:
            frames.append(FrameStat(float(m.group(1)), {}))
            continue
        s = _STAT.search(line)
        if s and frames:
            frames[-1].stats[s.group(1)] = float(s.group(2))
    return frames


def _in_shot(frames: list[FrameStat], shot: Shot) -> list[FrameStat]:
    return [f for f in frames if shot.start <= f.t < shot.end]


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def second_difference_mean(values: list[float]) -> float:
    """Mean |x[i] - (x[i-1] + x[i+1]) / 2|: zero for linear ramps (smooth fades/moves), large for frame-to-frame jumps."""
    if len(values) < 3:
        return 0.0
    return _mean([abs(values[i] - (values[i - 1] + values[i + 1]) / 2) for i in range(1, len(values) - 1)])


def max_deviation_from_moving_average(values: list[float], half: int = 8) -> float:
    """Largest |x[i] - mean(x[i-half .. i+half])|: a short excursion from the shot's own recent level."""
    out = 0.0
    for i, v in enumerate(values):
        w = values[max(0, i - half) : i + half + 1]
        out = max(out, abs(v - sum(w) / len(w)))
    return out


def _grade(name: str, value: float, shot: Shot, fail_below: float | None, warn_below: float | None, fail_above: float | None,
           warn_above: float | None, msg: str) -> CheckResult:  # fmt: skip
    status = Status.PASS
    if (warn_below is not None and value < warn_below) or (warn_above is not None and value > warn_above):
        status = Status.WARN
    if (fail_below is not None and value < fail_below) or (fail_above is not None and value > fail_above):
        status = Status.FAIL
    return CheckResult(name, status, round(value, 3), "", ((shot.start, shot.end),), msg, shot.id)


def score_shot(motion_frames: list[FrameStat], sharp_frames: list[FrameStat], shot: Shot,
               cfg: dict[str, float] = THRESHOLDS) -> list[CheckResult]:  # fmt: skip
    """Motion, sharpness and flicker results for one shot."""
    a = _in_shot(motion_frames, shot)
    b = _in_shot(sharp_frames, shot)
    # the shot's first frame is compared with the previous shot (a cut) or has no predecessor: leave it out of the motion mean
    ydif = [f.stats["YDIF"] for f in a[1:] if "YDIF" in f.stats]
    if not a or not b:
        return [CheckResult("shot_analysis", Status.FAIL, message="no frames inside the shot window", shot=shot.id)]
    motion = _mean(ydif)
    sharp = _mean([f.stats["YAVG"] for f in b if "YAVG" in f.stats])
    flicker = second_difference_mean([f.stats["YAVG"] for f in a if "YAVG" in f.stats])
    chroma = max(
        max_deviation_from_moving_average([f.stats[k] for f in a if k in f.stats]) for k in ("UAVG", "VAVG")
    )
    drift = CheckResult("colour_drift", Status.WARN if chroma > cfg["chroma_warn_above"] else Status.PASS, round(chroma, 3), "",
                        ((shot.start, shot.end),), f"chroma excursion {chroma:.2f} (warn above {cfg['chroma_warn_above']})", shot.id)  # fmt: skip
    return [
        drift,
        _grade("motion", motion, shot, cfg["motion_fail_below"], cfg["motion_warn_below"], None, None,
               f"mean frame difference {motion:.2f} (near-static below {cfg['motion_warn_below']})"),
        _grade("sharpness", sharp, shot, cfg["sharp_fail_below"], cfg["sharp_warn_below"], None, None,
               f"edge energy {sharp:.2f} (soft below {cfg['sharp_warn_below']})"),
        _grade("flicker", flicker, shot, None, None, cfg["flicker_fail_above"], cfg["flicker_warn_above"],
               f"luma jitter {flicker:.2f} (flicker above {cfg['flicker_warn_above']})"),
    ]  # fmt: skip


def chroma_excursion_moment(frames: list[FrameStat], shot: Shot, half: int = 8) -> tuple[float, float] | None:
    """(deviation, time) of the largest U/V chroma excursion in the shot, to point the judge at it. None without chroma data."""
    fs = [f for f in _in_shot(frames, shot) if "UAVG" in f.stats and "VAVG" in f.stats]
    best: tuple[float, float] | None = None
    for key in ("UAVG", "VAVG"):
        vals = [f.stats[key] for f in fs]
        for i, v in enumerate(vals):
            w = vals[max(0, i - half) : i + half + 1]
            dev = abs(v - sum(w) / len(w))
            if best is None or dev > best[0]:
                best = (dev, fs[i].t)
    return best


def chroma_args(cfg: dict[str, float] = THRESHOLDS) -> list[str]:
    """Colour pass (the gray passes above carry no chroma): per-frame UAVG/VAVG for `chroma_excursion_moment` and the colour_drift check."""
    return ["-an", "-vf", f"scale={int(cfg['analysis_width'])}:-2,signalstats,metadata=print"]
