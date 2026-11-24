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
        parts.append(f"[{music_index}:a]atrim=0:{d},afade=t=in:d=2,afade=t=out:st={max(0.0, duration - 3):.2f}:d=3,volume=0.55[mus]")
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
    parts.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0,alimiter=limit=0.9,atrim=0:{d}[aout]")
    return [], ";".join(parts)
