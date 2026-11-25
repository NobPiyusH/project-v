"""A separate Moon layer composited over a finished plate shot (pure functions: they build ffmpeg argv lists and expressions; nothing runs here
except `main`, the by-hand entry point).

Why (25 Nov 2026): the five reference Shorts are ONE continuous vertical shot of an everyday plate with one huge celestial object that slowly
grows / rises (docs/shorts/moon-reference-analysis.md). Wan draws the plate (waves, light) well but cannot be trusted to grow one object over
10 s, so the Moon is a separate picture (a lit disc on black or transparent) scaled and moved in code over the whole 10 s timeline, with a
soft glow under it. The scale curve is driven by the global time `t`, so it is continuous across the seam between two chained 5 s beats.

Growth only: a shrinking `scale=eval=frame` crashed ffmpeg in the VACE tests (docs/film4k.md), so `scale_to >= scale_from` is enforced.

By hand:  python tools/film4k/moon_layer.py plate.mp4 moon.png out.mp4 [--caption "What if the Moon were 10x closer?"] [--dry]
"""

from __future__ import annotations

import argparse
import subprocess
import sys

FONT_WIN = "C\\:/Windows/Fonts/arialbd.ttf"  # drawtext fontfile syntax: colon escaped, forward slashes


def scale_curve(t: float, dur: float, s_from: float, s_to: float, ease: str = "smooth") -> float:
    """Scale factor at time t (seconds) of a `dur`-second layer: s_from at 0, s_to at dur. `smooth` = smoothstep (slow in, slow out), `linear`."""
    if dur <= 0:
        raise ValueError("dur must be positive")
    if s_from <= 0 or s_to < s_from:
        raise ValueError("scale must start above 0 and only grow (s_to >= s_from)")
    p = min(max(t / dur, 0.0), 1.0)
    if ease == "smooth":
        p = p * p * (3 - 2 * p)
    elif ease != "linear":
        raise ValueError(f"unknown ease {ease!r}")
    return s_from + (s_to - s_from) * p


def progress_expr(dur: float, ease: str = "smooth") -> str:
    """The same progress curve as an ffmpeg expression in `t` (no commas outside single quotes: wrap the result in '...')."""
    p = f"min(max(t/{dur:g},0),1)"
    if ease == "smooth":
        return f"({p}*{p}*(3-2*{p}))"
    if ease == "linear":
        return p
    raise ValueError(f"unknown ease {ease!r}")


def lerp_expr(a: float, b: float, dur: float, ease: str = "smooth") -> str:
    return f"({a:g}+({b:g}-({a:g}))*{progress_expr(dur, ease)})"


def moon_matte_cmd(src: str, out_png: str, threshold: float = 0.05) -> list[str]:
    """A Moon picture on a black sky -> a PNG whose black becomes transparent (luma key). For a picture that already has alpha, skip this."""
    return ["ffmpeg", "-y", "-i", src, "-vf", f"format=rgba,lumakey=threshold={threshold}:tolerance=0.06:softness=0.08", "-frames:v", "1", out_png]


def drawtext_filter(text: str, start: float, end: float, fontsize: int, y: str = "h*0.07", fontfile: str = FONT_WIN) -> str:
    """Small caption centred near the top, with a thin shadow, shown from `start` to `end` seconds and faded in over 0.4 s."""
    safe = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")
    alpha = f"if(lt(t\\,{start:g})\\,0\\,if(lt(t\\,{start + 0.4:g})\\,(t-{start:g})/0.4\\,if(lt(t\\,{end:g})\\,1\\,0)))"
    return (f"drawtext=fontfile='{fontfile}':text='{safe}':fontsize={fontsize}:fontcolor=white:shadowcolor=black@0.6:shadowx=2:shadowy=2"
            f":x=(w-text_w)/2:y={y}:alpha='{alpha}'")


def composite_cmd(
    plate: str, moon_png: str, out: str, *, w: int = 720, h: int = 1280, dur: float = 10.0, fps: int = 16,
    diameter_frac: float = 0.30, scale_from: float = 1.0, scale_to: float = 2.2, cx: float = 0.5, cy_from: float = 0.62, cy_to: float = 0.36,
    ease: str = "smooth", glow: float = 0.5, glow_scale: float = 1.7, glow_sigma: int = 60,
    caption: str | None = None, caption_start: float = 0.0, caption_end: float = 4.0, fontfile: str = FONT_WIN, keep_audio: bool = False,
) -> list[str]:
    """ffmpeg argv: the Moon PNG scaled from diameter_frac*w x scale_from to x scale_to, its centre moving from (cx, cy_from) to (cx, cy_to)
    (fractions of the frame), a blurred glow copy underneath (padded so the blur is not clipped to a square), an optional top caption, over `plate`. Output yuv420p, no audio (narration and
    music are mixed later) unless `keep_audio` (the studio composites over the finished master: its audio track is copied). `plate` is trimmed to `dur`."""
    scale_curve(0, dur, scale_from, scale_to, ease)  # validates the arguments
    if not 0 < diameter_frac <= 1 or not 0 <= glow <= 1:
        raise ValueError("diameter_frac must be in (0, 1] and glow in [0, 1]")
    d0 = diameter_frac * w
    side = f"trunc({d0:g}*{lerp_expr(scale_from, scale_to, dur, ease)}/2)*2"
    gside = f"trunc({d0:g}*{glow_scale:g}*2*{lerp_expr(scale_from, scale_to, dur, ease)}/2)*2"
    px = f"{w * cx:g}-overlay_w/2"
    py = f"({h}*{lerp_expr(cy_from, cy_to, dur, ease)})-overlay_h/2"
    graph = [
        "[1:v]format=rgba,split=2[m0][g0]",
        # blur at a FIXED size, then scale: gblur breaks when its input size changes per frame (hard-edged ring, buffer reuse)
        f"[g0]scale=512:512,pad=w=1024:h=1024:x=256:y=256:color=0x00000000,gblur=sigma={glow_sigma},"
        f"scale=w='{gside}':h='{gside}':eval=frame,fade=t=in:st=0:d=1.5:alpha=1,colorchannelmixer=aa={glow:g}[glow]",
        f"[m0]scale=w='{side}':h='{side}':eval=frame[moon]",
        f"[0:v]trim=duration={dur:g},setpts=PTS-STARTPTS[bg]",
        f"[bg][glow]overlay=x='{px}':y='{py}':eval=frame:format=auto[a]",
        f"[a][moon]overlay=x='{px}':y='{py}':eval=frame:format=auto[b]",
    ]
    last = "b"
    if caption:
        graph.append(f"[b]{drawtext_filter(caption, caption_start, caption_end, max(24, round(w * 0.045)), fontfile=fontfile)}[c]")
        last = "c"
    graph.append(f"[{last}]format=yuv420p[v]")
    audio = ["-map", "0:a?", "-c:a", "copy"] if keep_audio else ["-an"]
    return [
        "ffmpeg", "-y", "-i", plate, "-loop", "1", "-framerate", str(fps), "-t", f"{dur:g}", "-i", moon_png,
        "-filter_complex", ";".join(graph), "-map", "[v]", "-r", str(fps), "-t", f"{dur:g}", "-c:v", "libx264", "-crf", "14", *audio, out,
    ]  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("plate")
    ap.add_argument("moon_png")
    ap.add_argument("out")
    ap.add_argument("--caption")
    ap.add_argument("--dur", type=float, default=10.0)
    ap.add_argument("--scale-to", type=float, default=2.2)
    ap.add_argument("--dry", action="store_true", help="print the command, run nothing")
    a = ap.parse_args(argv)
    cmd = composite_cmd(a.plate, a.moon_png, a.out, dur=a.dur, scale_to=a.scale_to, caption=a.caption)
    if a.dry:
        print(" ".join(f'"{c}"' if " " in c or ";" in c else c for c in cmd))
        return 0
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
