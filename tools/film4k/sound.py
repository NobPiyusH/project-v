"""Procedural sound bed for the film cut: wind, low rumble, a dark drone and a hit on every cut. One ffmpeg filter graph, no samples.

It is a PLACEHOLDER bed (24 Nov 2026: Lyria was blocked by a Google billing 403): good enough to judge the edit by ear. Pass a real
music file with `music=` and it is mixed under the effects instead of the drone.
"""

from __future__ import annotations

DRONE_HZ = (55.0, 82.41, 110.0)


def build_graph(duration: float, cuts: list[float], music_index: int | None = None) -> tuple[list[str], str]:
    """Return (extra ffmpeg inputs, filter_complex ending in [aout]). `music_index` = ffmpeg input number of a music file, if any."""
    d = f"{duration:.3f}"
    parts = [
        f"anoisesrc=d={d}:c=pink:a=0.5:r=48000,lowpass=f=900,highpass=f=120,tremolo=f=0.17:d=0.55,volume=0.30[wind]",
        f"anoisesrc=d={d}:c=brown:a=0.9:r=48000,lowpass=f=95,volume=0.70[rumble]",
    ]
    mix = ["[wind]", "[rumble]"]
    if music_index is None:
        for i, hz in enumerate(DRONE_HZ):
            parts.append(f"sine=f={hz}:d={d}:r=48000,tremolo=f={0.1 + 0.05 * i:.2f}:d=0.4,volume=0.10[dr{i}]")
            mix.append(f"[dr{i}]")
    else:
        parts.append(
            f"[{music_index}:a]atrim=0:{d},afade=t=in:d=2,afade=t=out:st={max(0.0, duration - 3):.2f}:d=3,volume=0.55[mus]"
        )
        mix.append("[mus]")
    n = len(cuts)
    if n:
        parts.append("sine=f=52:d=1.3:r=48000,afade=t=out:st=0.04:d=1.25,volume=1.0[boom]")
        parts.append("anoisesrc=d=0.5:c=white:r=48000,highpass=f=700,afade=t=out:st=0.0:d=0.45,volume=0.35[crack]")
        parts.append("[boom][crack]amix=inputs=2:normalize=0,asplit=" + str(n) + "".join(f"[h{i}]" for i in range(n)))
        for i, t in enumerate(cuts):
            ms = int(round(t * 1000))
            parts.append(f"[h{i}]adelay={ms}|{ms},apad=whole_dur={d}[hd{i}]")
            mix.append(f"[hd{i}]")
    parts.append(
        "".join(mix) + f"amix=inputs={len(mix)}:normalize=0,alimiter=limit=0.9,atrim=0:{d},"
        f"afade=t=out:st={max(0.0, duration - 1.5):.2f}:d=1.5[aout]"
    )
    return [], ";".join(parts)


# --- final loudness normalisation (two-pass ffmpeg loudnorm, linear mode) -----------------------------------------------------
# The procedural bed measured -21.1 LUFS on act1_cut_v3; YouTube plays about -14. Last audio step of the render, flag-controlled.
LOUDNORM_I = -14.0
LOUDNORM_TP = -1.5
LOUDNORM_LRA = 11.0


def loudnorm_enabled(value: str | None = None) -> bool:
    """Default ON. FILM_LOUDNORM=0/false/off/no switches it off."""
    import os

    raw = os.environ.get("FILM_LOUDNORM", "") if value is None else value
    return raw.strip().lower() not in {"0", "false", "off", "no"}


def loudnorm_measure_filter(i: float = LOUDNORM_I, tp: float = LOUDNORM_TP, lra: float = LOUDNORM_LRA) -> str:
    """Pass 1: analyse only (JSON printed to stderr)."""
    return f"loudnorm=I={i}:TP={tp}:LRA={lra}:print_format=json"


def parse_loudnorm_json(stderr: str) -> dict[str, float]:
    """The measured_* values from the JSON block loudnorm prints at the end of pass 1."""
    import json

    start, end = stderr.rfind("{"), stderr.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no loudnorm JSON in ffmpeg output")
    data = json.loads(stderr[start : end + 1])
    keys = {"input_i": "i", "input_tp": "tp", "input_lra": "lra", "input_thresh": "thresh", "target_offset": "offset"}
    try:
        return {short: float(data[k]) for k, short in keys.items()}
    except KeyError as e:
        raise ValueError(f"loudnorm JSON lacks {e}") from e


def loudnorm_apply_filter(m: dict[str, float], i: float = LOUDNORM_I, tp: float = LOUDNORM_TP, lra: float = LOUDNORM_LRA) -> str:
    """Pass 2: linear mode with the pass-1 measurements. Output stays at 48 kHz (loudnorm upsamples internally)."""
    return (
        f"loudnorm=I={i}:TP={tp}:LRA={lra}:measured_I={m['i']}:measured_TP={m['tp']}:measured_LRA={m['lra']}"
        f":measured_thresh={m['thresh']}:offset={m['offset']}:linear=true:print_format=summary,aresample=48000"
    )


def linear_feasible(m: dict[str, float], i: float = LOUDNORM_I, tp: float = LOUDNORM_TP) -> bool:
    """Linear loudnorm needs the peaks to survive the gain. The procedural bed (hits 20 dB over its average) never does:
    ffmpeg then silently falls back to dynamic mode and clips after AAC, so that case takes the gain + limiter path."""
    return m["tp"] + (i - m["i"]) <= tp


def gain_limit_filter(gain_db: float, tp: float = LOUDNORM_TP) -> str:
    """Fallback pass 2: plain gain, a pre-limiter that tames the hits, then a 4x-oversampled limiter 0.5 dB under the
    true-peak target so AAC overshoot stays below it. (Tuned on act1_cut_v3: 12 dB gain gave -14.0 LUFS, -1.5 dBTP after AAC.)"""
    pre = 10 ** ((tp - 1.5) / 20)  # about -3 dBFS
    ceiling = 10 ** ((tp - 1.0) / 20)  # about -2.5 dBFS
    return (
        f"volume={gain_db:.2f}dB,alimiter=limit={pre:.3f}:attack=5:release=50:level=disabled,"
        f"aresample=192000,alimiter=limit={ceiling:.3f}:attack=2:release=30:level=disabled,aresample=48000"
    )
