"""Edit-and-sync polish for Dream Shorts (22 Nov 2026). Every switch is a per-Short request flag, default OFF until the
owner has judged the A/B (reports/edit-sync-ab):

  beatSnap          cuts snap to the nearest bar line / beat, trimming OR gently slowing a shot (<= 6%), not trim-only
  smartTransitions  per cut: hard-ish cut where the two shots flow, a short dissolve where the cut would jar
  hqInterp          finer-grained FFmpeg motion interpolation (bidirectional search, small blocks, scene-change guard)
  twoPassLoudness   measure then apply linear loudnorm (-14 LUFS) plus a true-peak limiter, no "pumping"
  softEndFade       the music's end fade is longer and eased (quarter-sine) instead of a straight 1.5 s ramp
  (voiceUri)        a Short given a narration track ducks its bed under the voice; no voice, nothing changes

All functions here are pure or take explicit files; finish.py wires them in. Deterministic: no randomness.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from workers.common import ffmpeg_bin

FLAGS = ("beatSnap", "smartTransitions", "hqInterp", "twoPassLoudness", "softEndFade")


def flag(req: dict, name: str) -> bool:
    """Polish flags are opt-in: only an explicit true turns one on."""
    return req.get(name) is True


# ------------------------------------------------------------------------------------------------ beat snapping

def snap_cuts(durations: list[float], dissolves: list[float], beats: list[float], bars: list[float] | None = None,
              max_trim: float = 1.0, max_stretch: float = 0.06, min_len: float = 0.0,
              prefer: dict[int, float] | None = None) -> tuple[list[float], list[float]]:
    """Place every cut (the middle of every dissolve) on the music's grid.

    Unlike the older trim-only sync, a shot may also be played up to `max_stretch` (6%) slower so a cut that sits just
    after a beat can reach it. Candidates: bar lines in [natural - trim, natural + stretch room] first, else beats;
    the nearest wins, and `prefer[i]` (the music's biggest rise, for the climax cut) wins when it is reachable. Returns (new durations, stretch factor per shot, 1.0 = untouched). The last shot is untouched.
    `dissolves[i]` is the dissolve into shot i (dissolves[0] unused)."""
    out, stretch = list(durations), [1.0] * len(durations)
    base = 0.0
    for i in range(len(out) - 1):
        d = dissolves[i + 1]
        natural = base + out[i] - d / 2
        trim = min(max_trim, out[i] / 3, max(0.0, out[i] - min_len) if min_len else max_trim)
        room = out[i] * max_stretch
        cands = []
        for grid in ([bars] if bars else []) + [beats]:
            cands = [b for b in grid if natural - trim <= b <= natural + room + 1e-6]
            if cands:
                break
        if cands:
            want = prefer.get(i) if prefer else None
            if want is not None and any(abs(b - want) < 0.5 for b in cands):
                at = min(cands, key=lambda b: abs(b - want))
            else:
                at = min(cands, key=lambda b: abs(b - natural))
            new = round(out[i] + (at - natural), 3)
            if new > out[i]:
                stretch[i] = round(new / out[i], 4)
            out[i] = new
        base += out[i] - d
    return out, stretch


def grid_error(durations: list[float], dissolves: list[float], grid: list[float]) -> float:
    """Mean distance (s) from each cut to the nearest grid point: the number the A/B reports as 'cut-to-beat error'."""
    if not grid or len(durations) < 2:
        return 0.0
    errs, base = [], 0.0
    for i in range(len(durations) - 1):
        d = dissolves[i + 1]
        cut = base + durations[i] - d / 2
        errs.append(min(abs(g - cut) for g in grid))
        base += durations[i] - d
    return round(sum(errs) / len(errs), 3)


# ------------------------------------------------------------------------------------------------ transitions

JARRING = 38.0  # mean luma change (0-255) between one shot's last and the next one's first frame: a jarring cut
FLOWING = 14.0  # at or below this the two shots continue each other (SVI chaining): barely any dissolve needed


def transition_lengths(diffs: list[float], base: float) -> list[float]:
    """Dissolve length for the cut after each pair of shots, from how different the frames either side of it are.
    Flowing shots get a quick 0.12 s blend (a clean cut, no pop); a jarring cut gets the full `base`; in between,
    half. `diffs[k]` is the difference at the cut between shot k and k+1. Returns one length per cut."""
    if base <= 0:
        return [0.0] * len(diffs)
    out = []
    for d in diffs:
        if d <= FLOWING:
            out.append(min(base, 0.12))
        elif d >= JARRING:
            out.append(base)
        else:
            out.append(round(max(0.12, base / 2), 3))
    return out


def boundary_diff(a: Path, b: Path) -> float:
    """Mean absolute luma difference between the last frame of clip a and the first of clip b (tiny grey copies)."""
    def frame(path: Path, last: bool) -> bytes:
        pre = ["-sseof", "-0.25"] if last else []
        res = subprocess.run([ffmpeg_bin(), "-hide_banner", "-loglevel", "error", *pre, "-i", str(path), "-an",
                              "-vf", "scale=36:64,format=gray", "-f", "rawvideo", "-"], capture_output=True, check=False)
        size = 36 * 64
        data = res.stdout
        if last:
            return data[-size:] if len(data) >= size else b""
        return data[:size]
    fa, fb = frame(a, True), frame(b, False)
    if not fa or len(fa) != len(fb):
        return JARRING  # unknown: treat as a normal cut and keep the full dissolve
    return sum(abs(x - y) for x, y in zip(fa, fb)) / len(fa)


# ------------------------------------------------------------------------------------------------ interpolation

# Default is mi_mode=mci / aobmc (finish._smooth). The finer search finds motion in small details (hands, hair, water)
# and the scene-change detector stops it smearing across a cut inside a clip.
MINTERPOLATE_HQ = "mi_mode=mci:mc_mode=aobmc:me_mode=bidir:me=umh:mb_size=8:search_param=24:vsbmc=1:scd=fdiff:scd_threshold=8"


def minterpolate_filter(dst_fps: int, hq: bool) -> str:
    return f"minterpolate=fps={dst_fps}:" + (MINTERPOLATE_HQ if hq else "mi_mode=mci:mc_mode=aobmc:vsbmc=1")


# ------------------------------------------------------------------------------------------------ loudness / audio

TRUE_PEAK = -1.5
LUFS = -14.0


def parse_loudnorm(stderr: str) -> dict | None:
    """The JSON block FFmpeg's loudnorm prints (print_format=json) -> its numbers as floats, or None."""
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, re.S)
    if not m:
        return None
    try:
        raw = json.loads(m.group(0))
        return {k: float(raw[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (ValueError, KeyError):
        return None


def limiter(true_peak: float = TRUE_PEAK) -> str:
    """Brick-wall safety after loudnorm: no sample above the true-peak ceiling (less 0.3 dB for inter-sample overs)."""
    ceiling = 10 ** ((true_peak - 0.3) / 20)
    return f"alimiter=limit={ceiling:.4f}:attack=5:release=60:level=disabled"


def loudness_filter(stats: dict | None, lufs: float = LUFS, true_peak: float = TRUE_PEAK) -> str:
    """Two-pass loudnorm (linear, measured) + limiter when the measurement exists; else the one-pass filter + limiter."""
    base = f"loudnorm=I={lufs}:TP={true_peak}:LRA=11"
    if stats:
        base += (f":measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:measured_LRA={stats['input_lra']}"
                 f":measured_thresh={stats['input_thresh']}:offset={stats['target_offset']}:linear=true")
    return f"{base},{limiter(true_peak)}"


def measure_loudness(graph: str, label: str, inputs: list[str], cwd: Path, lufs: float = LUFS,
                     true_peak: float = TRUE_PEAK) -> dict | None:
    """Render the audio graph to nowhere through loudnorm's measure pass; None if FFmpeg gave no numbers."""
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-y", *inputs, "-filter_complex",
                           f"{graph};{label}loudnorm=I={lufs}:TP={true_peak}:LRA=11:print_format=json[m]",
                           "-map", "[m]", "-f", "null", "-"], capture_output=True, text=True, check=False, cwd=cwd)
    return parse_loudnorm(proc.stderr)


def end_fade(length: float, soft: bool) -> str:
    """The closing fade of the music: straight 1.5 s (as before) or a 2.5 s eased quarter-sine tail."""
    d = min(2.5 if soft else 1.5, max(0.5, length / 3))
    curve = ":curve=qsin" if soft else ""
    return f"afade=t=out:st={max(0.0, length - d):.3f}:d={d:.3f}{curve}"


def duck_filters(bed: str, voice: str, out: str = "mix") -> list[str]:
    """Bed ducked under the voice: the voice is split (one copy steers the compressor, one is heard), music/ambience
    drop ~8-10 dB while it speaks and rise back over 0.4 s. Same settings as the main mix (activities/mixing.py)."""
    return [f"{voice}asplit=2[dv1][dv2]",
            f"{bed}[dv2]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[dbed]",
            f"[dbed][dv1]amix=inputs=2:normalize=0[{out}]"]
