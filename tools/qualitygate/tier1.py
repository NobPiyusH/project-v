"""Tier 1: free whole-video checks (ffmpeg only). Parsers are pure so they are tested on canned ffmpeg output."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import THRESHOLDS
from .types import CheckResult, Status

# ---------- loudness ----------

_I = re.compile(r"^\s*I:\s+(-?[\d.]+|-inf)\s+LUFS", re.M)
_TP = re.compile(r"^\s*Peak:\s+(-?[\d.]+|-inf)\s+dBFS", re.M)


def parse_ebur128(stderr: str) -> tuple[float | None, float | None]:
    """Integrated loudness (LUFS) and true peak from the summary block ebur128 prints at the end (last match wins)."""
    i = _I.findall(stderr)
    p = _TP.findall(stderr)

    def f(v: str) -> float | None:
        return None if v == "-inf" else float(v)

    return (f(i[-1]) if i else None), (f(p[-1]) if p else None)


def check_loudness(lufs: float | None, true_peak: float | None, cfg: dict[str, float] = THRESHOLDS) -> CheckResult:
    if lufs is None:
        return CheckResult("loudness", Status.FAIL, message="no measurable audio (silent track)")
    delta = abs(lufs - cfg["target_lufs"])
    status = Status.FAIL if delta > cfg["lufs_fail_delta"] else Status.WARN if delta > cfg["lufs_warn_delta"] else Status.PASS
    msg = f"{lufs:.1f} LUFS vs target {cfg['target_lufs']:.0f}"
    if true_peak is not None and true_peak > cfg["true_peak_fail_db"]:
        status, msg = Status.FAIL, msg + f"; true peak {true_peak:.1f} dBTP clips"
    return CheckResult("loudness", status, round(lufs, 2), "LUFS", message=msg)


def loudness_args() -> list[str]:
    return ["-vn", "-af", "ebur128=peak=true"]


# ---------- black frames ----------

_BLACK = re.compile(r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)")


def parse_blackdetect(stderr: str) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b, _ in _BLACK.findall(stderr)]


def check_black(spans: list[tuple[float, float]], duration: float, cfg: dict[str, float] = THRESHOLDS) -> CheckResult:
    """Black at the very start or end up to `black_edge_ok_s` is a fade and ignored; any other black run warns, a long one fails."""
    eps = 0.15
    bad, worst_len = [], 0.0
    for a, b in spans:
        edge = (a <= eps or b >= duration - eps) and (b - a) <= cfg["black_edge_ok_s"]
        if not edge:
            bad.append((a, b))
            worst_len = max(worst_len, b - a)
    if not bad:
        return CheckResult("black_frames", Status.PASS, 0.0, "s", message="no black frames inside the video")
    status = Status.FAIL if worst_len >= cfg["black_fail_s"] else Status.WARN
    return CheckResult("black_frames", status, round(sum(b - a for a, b in bad), 2), "s", tuple(bad),
                       f"{len(bad)} black run(s), longest {worst_len:.2f}s")  # fmt: skip


def black_args(cfg: dict[str, float] = THRESHOLDS) -> list[str]:
    return ["-an", "-vf", f"blackdetect=d={cfg['black_min_s']}:pic_th=0.98"]


# ---------- frozen frames ----------

_FREEZE_START = re.compile(r"lavfi\.freezedetect\.freeze_start:\s*([\d.]+)")
_FREEZE_END = re.compile(r"lavfi\.freezedetect\.freeze_end:\s*([\d.]+)")


def parse_freezedetect(stderr: str, duration: float) -> list[tuple[float, float]]:
    """Frozen spans. A freeze still running at end of file has a start but no end: it runs to `duration`."""
    starts = [float(x) for x in _FREEZE_START.findall(stderr)]
    ends = [float(x) for x in _FREEZE_END.findall(stderr)]
    return [(s, ends[i] if i < len(ends) else duration) for i, s in enumerate(starts)]


def check_freeze(spans: list[tuple[float, float]], cfg: dict[str, float] = THRESHOLDS) -> CheckResult:
    if not spans:
        return CheckResult("frozen_frames", Status.PASS, 0.0, "s", message="no frozen frames")
    longest = max(b - a for a, b in spans)
    status = Status.FAIL if longest >= cfg["freeze_fail_s"] else Status.WARN
    return CheckResult("frozen_frames", status, round(sum(b - a for a, b in spans), 2), "s", tuple(spans),
                       f"{len(spans)} frozen run(s), longest {longest:.2f}s")  # fmt: skip


def freeze_args(cfg: dict[str, float] = THRESHOLDS) -> list[str]:
    return ["-an", "-vf", f"freezedetect=n=-60dB:d={cfg['freeze_min_s']}"]


# ---------- captions ----------


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d{1,3})")


def _secs(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_srt(text: str) -> list[Cue]:
    cues = []
    for block in re.split(r"\r?\n\r?\n+", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        for i, ln in enumerate(lines):
            m = _TIME.search(ln)
            if m:
                g = m.groups()
                cues.append(Cue(_secs(*g[:4]), _secs(*g[4:]), " ".join(lines[i + 1 :]).strip()))
                break
    return cues


def check_captions(cues: list[Cue], duration: float, cfg: dict[str, float] = THRESHOLDS) -> list[CheckResult]:
    """Four results: range (cues inside the video), overlap, duration (min/max), reading speed."""
    ordered = sorted(cues, key=lambda c: c.start)
    out_of_range = tuple((c.start, c.end) for c in ordered if c.start < 0 or c.end <= c.start or c.end > duration + 0.05)
    overlaps = tuple((b.start, min(a.end, b.end)) for a, b in zip(ordered, ordered[1:]) if b.start < a.end - 0.001)
    short = [(c.start, c.end) for c in ordered if 0 < c.end - c.start < cfg["cap_min_dur_s"]]
    long_ = [(c.start, c.end) for c in ordered if c.end - c.start > cfg["cap_max_dur_s"]]
    cps = [(len(c.text) / (c.end - c.start), c) for c in ordered if c.end > c.start]
    fast_fail = tuple((c.start, c.end) for v, c in cps if v > cfg["cap_cps_fail"])
    fast_warn = tuple((c.start, c.end) for v, c in cps if cfg["cap_cps_warn"] < v <= cfg["cap_cps_fail"])
    peak = max((v for v, _ in cps), default=0.0)

    def res(name: str, bad: tuple, status_if_bad: Status, msg: str) -> CheckResult:
        return CheckResult(name, status_if_bad if bad else Status.PASS, float(len(bad)), "cues", tuple(bad), msg if bad else "ok")

    speed_status = Status.FAIL if fast_fail else Status.WARN if fast_warn else Status.PASS
    return [
        res("captions_range", out_of_range, Status.FAIL, f"{len(out_of_range)} cue(s) outside 0..{duration:.2f}s or end <= start"),
        res("captions_overlap", overlaps, Status.FAIL, f"{len(overlaps)} overlapping cue pair(s)"),
        res("captions_duration", tuple(short + long_), Status.WARN, f"{len(short)} too short, {len(long_)} too long"),
        CheckResult("captions_reading_speed", speed_status, round(peak, 1), "cps", fast_fail + fast_warn,
                    f"fastest {peak:.1f} cps (warn>{cfg['cap_cps_warn']:.0f}, fail>{cfg['cap_cps_fail']:.0f})"),
    ]  # fmt: skip
