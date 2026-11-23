"""Check 6: frozen or black stretches in the cut. Short black at the very start/end is a fade and fine; a long black
or a frozen picture in the middle is a fault. Parsers read ffmpeg blackdetect / freezedetect logs."""
from __future__ import annotations

import re
import subprocess

from . import FAIL, WARN, CheckResult, Finding


def parse_black(text: str) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:(-?[\d.]+) black_end:(-?[\d.]+)", text)]


def parse_freeze(text: str) -> list[tuple[float, float]]:
    starts = [float(x) for x in re.findall(r"freeze_start: (-?[\d.]+)", text)]
    ends = [float(x) for x in re.findall(r"freeze_end: (-?[\d.]+)", text)]
    return [(s, ends[i] if i < len(ends) else float("inf")) for i, s in enumerate(starts)]


def shot_at(shots: list[dict], t: float) -> str:
    for s in shots:
        if s.get("startSec") is not None and s["startSec"] <= t < s["startSec"] + s["durationSec"]:
            return f"shot {s['n']}"
    return f"{t:.1f}s"


def judge(black: list[tuple[float, float]], freeze: list[tuple[float, float]], total: float, shots: list[dict], cfg: dict) -> list[Finding]:
    f = cfg["freeze"]
    out = []
    for a, b in black:
        b = min(b, total)
        edge = a < 0.05 or b >= total - 0.05
        length = b - a
        if edge and length <= f["black_max_ok_sec"]:
            continue
        if length > f["black_max_ok_sec"]:
            out.append(Finding("freeze", FAIL, "BLACK_FRAMES", f"{length:.1f}s of black ({a:.1f}s to {b:.1f}s).", shot_at(shots, a)))
        else:
            out.append(Finding("freeze", WARN, "BLACK_FLASH", f"{length:.1f}s of black in the middle ({a:.1f}s).", shot_at(shots, a)))
    for a, b in freeze:
        b = min(b, total)
        length = b - a
        slot = next((s for s in shots if s.get("startSec") is not None and s["startSec"] - 0.1 <= a < s["startSec"] + s["durationSec"]), None)
        whole = slot is not None and length >= 0.6 * slot["durationSec"]
        sev = FAIL if (whole and slot.get("lane") == "diagram") or length > 5 else WARN
        out.append(Finding("freeze", sev, "FROZEN_PICTURE", f"Picture frozen for {length:.1f}s ({a:.1f}s to {b:.1f}s).", shot_at(shots, a)))
    return out


def analyse(path, cfg: dict, ffmpeg: str = "ffmpeg") -> tuple[list, list]:
    f = cfg["freeze"]
    vf = f"scale=640:-2,blackdetect=d={f['black_min_sec']}:pix_th=0.08,freezedetect=n=-55dB:d={f['freeze_min_sec']}"
    p = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf", vf, "-f", "null", "-"],
                       capture_output=True, text=True, check=False)
    return parse_black(p.stderr), parse_freeze(p.stderr)


def run(path, total: float, shots: list[dict], cfg: dict, ffmpeg: str = "ffmpeg") -> CheckResult:
    res = CheckResult("freeze")
    black, freeze = analyse(path, cfg, ffmpeg)
    res.findings = judge(black, freeze, total, shots, cfg)
    res.stats = {"black_segments": black, "freeze_segments": freeze}
    return res
