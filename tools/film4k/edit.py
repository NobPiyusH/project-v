"""Re-cut finished scenes into short shots with real coverage (pure planning functions + a laptop CLI).

Why (24 Nov 2026, owner: "editing is not good"): each 11 s scene played as ONE framing with a barely visible zoom and soft 0.5 s
cross-fades at the SVI beat seams, so the film felt like a slideshow. Here every scene is cut into 3-6 shots of about 1.5-4 s:
  * the cross-fade windows are DROPPED, so the cut lands where the beats do not join (hard cut, no dissolve look);
  * each shot gets a different framing cut from the 4K plate (wide / push / close / whip), never the same framing twice in a row;
  * shots get shorter toward the end of the film (pace curve) and cut on motion, with a hit sound on every cut.
All zooms only grow inside a shot (a shrinking scale=eval=frame crashes ffmpeg).

Usage: python tools/film4k/edit.py --dir E:/project-data/outputs/game-4k --name act1_cut --scenes 1 2 3 4 5 6 7 8 [--w 720 --h 1280] [--audio]
Reads scenes/sceneNN.mp4 (already 48 fps, camera move and grade baked in). Writes shots to cut/, then <name>.mp4 (+ audio if asked).
"""
from __future__ import annotations

import argparse
import random
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from camera import BEAT_S, FADE_S  # noqa: E402

FPS = 48
SPEED = 1.25            # the playback speed baked in by the assembler (RIFE pass)
MIN_SHOT_S = 1.3
KINDS = ("wide", "push", "close", "whip")


@dataclass(frozen=True)
class Shot:
    scene: int
    start: float          # seconds into the scene file
    dur: float
    kind: str
    z0: float
    z1: float
    fx0: float            # crop centre offset, -1..1 of the available margin
    fx1: float
    fy0: float
    fy1: float
    whip: float = 0.0     # extra x offset at the first frames (fraction of margin), decays in about 3 frames
    shake: int = 6        # px at 1080 wide, scaled to the output


def seam_gaps(speed: float = SPEED) -> list[tuple[float, float]]:
    """Time windows (in scene-file seconds) where beat 1|2 and 2|3 are cross-faded. Cuts must skip them."""
    gaps = []
    for k in (1, 2):
        s = (k * (BEAT_S - FADE_S)) / speed
        gaps.append((s, s + FADE_S / speed))
    return gaps


def segments(duration: float, speed: float = SPEED, pad: float = 0.03) -> list[tuple[float, float]]:
    """The playable parts of a scene: everything outside the cross-fade windows."""
    segs, cur = [], 0.0
    for a, b in seam_gaps(speed):
        if a - pad > cur:
            segs.append((cur, a - pad))
        cur = b + pad
    if duration > cur:
        segs.append((cur, duration))
    return segs


def _lengths(total: float, target: float, rng: random.Random) -> list[float]:
    """Split `total` into shot lengths around `target`, none shorter than MIN_SHOT_S, varied (not metronomic)."""
    if total < MIN_SHOT_S * 1.6:
        return [total]
    n = max(1, round(total / target))
    while n > 1 and total / n < MIN_SHOT_S:
        n -= 1
    w = [rng.uniform(0.7, 1.4) for _ in range(n)]
    s = sum(w)
    out = [total * x / s for x in w]
    # push any too-short shot's excess from the longest
    for i, x in enumerate(out):
        if x < MIN_SHOT_S:
            j = max(range(n), key=lambda k: out[k])
            d = MIN_SHOT_S - x
            out[i], out[j] = MIN_SHOT_S, out[j] - d
    return out


def _framing(kind: str, rng: random.Random) -> dict:
    side = rng.choice((-1, 1))
    if kind == "wide":
        return dict(z0=1.04, z1=1.14, fx0=-0.3 * side, fx1=0.3 * side, fy0=0.0, fy1=0.0)
    if kind == "push":
        return dict(z0=1.45, z1=1.65, fx0=0.2 * side, fx1=0.0, fy0=rng.uniform(-0.3, 0.3), fy1=0.0)
    if kind == "close":
        return dict(z0=2.05, z1=2.25, fx0=rng.uniform(-0.6, 0.6), fx1=rng.uniform(-0.6, 0.6), fy0=rng.uniform(-0.6, 0.6),
                    fy1=rng.uniform(-0.6, 0.6))
    return dict(z0=1.4, z1=1.5, fx0=0.8 * side, fx1=-0.1 * side, fy0=0.0, fy1=0.0)   # whip


def _differs(a: Shot, b: Shot) -> bool:
    """Consecutive shots must change framing clearly, otherwise the cut looks like a glitch."""
    ratio = max(a.z1, b.z0) / min(a.z1, b.z0)
    return ratio >= 1.25 or abs(a.fx1 - b.fx0) >= 0.6


def _gap(a: Shot, b: Shot) -> float:
    return max(a.z1, b.z0) / min(a.z1, b.z0) + abs(a.fx1 - b.fx0)


def plan_shots(scene_id: int, duration: float, act_pos: float, prev: Shot | None = None, seed: int = 0) -> list[Shot]:
    """Shot list for one scene. act_pos 0..1 = position in the film: early shots about 3.6 s, late shots about 2.0 s."""
    rng = random.Random(scene_id * 7919 + seed)
    target = 3.6 - 1.6 * min(max(act_pos, 0.0), 1.0)
    shots: list[Shot] = []
    last = prev
    for a, b in segments(duration):
        t = a
        for L in _lengths(b - a, target, rng):
            options = [k for k in KINDS if not (last and last.kind == k)]
            if last is None:
                options = ["wide"]                                 # the very first shot establishes
            elif not shots:
                options = [k for k in options if k != "wide"] or options   # a new scene opens on a changed framing
            best = None
            for _ in range(24):
                kind = rng.choice(options)
                s = Shot(scene_id, round(t, 3), round(L, 3), kind, whip=0.7 if kind == "whip" else 0.0, **_framing(kind, rng))
                if last is None or _differs(last, s):
                    best = s
                    break
                if best is None or _gap(last, s) > _gap(last, best):
                    best = s
            s = best
            shots.append(s)
            last = s
            t += L
    return shots


def cut_times(shots: list[Shot]) -> list[float]:
    """Film-time of every cut (start of each shot except the first): where the hit sounds go."""
    out, t = [], 0.0
    for i, s in enumerate(shots):
        if i:
            out.append(round(t, 3))
        t += s.dur
    return out


def shot_filter(s: Shot, w: int = 2160, h: int = 3840) -> str:
    """ffmpeg -vf for one shot: grow-only scale, animated crop (centre drift, whip, shake), cut length enforced by -t."""
    frames = max(2, round(s.dur * FPS))
    last = frames - 1
    z = f"({s.z0}+({s.z1}-{s.z0})*n/{last})"
    mx, my = f"(in_w-{w})/2", f"(in_h-{h})/2"
    fx = f"({s.fx0}+({s.fx1}-{s.fx0})*n/{last})"
    fy = f"({s.fy0}+({s.fy1}-{s.fy0})*n/{last})"
    k = s.shake * w / 1080
    x = f"{mx}*(1+{fx}+{s.whip}*exp(-n/3))+{k:.2f}*sin(n*0.41)"
    y = f"{my}*(1+{fy})+{k:.2f}*sin(n*0.33+1)"
    vf = (f"scale=w='trunc({w}*{z}/2)*2':h='trunc({h}*{z}/2)*2':eval=frame:flags=bilinear,"
          f"crop={w}:{h}:x='{x}':y='{y}'")
    if s.whip:
        vf += f",avgblur=sizeX={max(3, w // 18) | 1}:sizeY=1:enable='lt(n,4)'"
    return vf + ",format=yuv420p"


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", *args], check=True)


def probe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def render(base: Path, name: str, scene_ids: list[int], w: int, h: int, seed: int = 0, audio: bool = False) -> Path:
    cut = base / "cut" / name
    cut.mkdir(parents=True, exist_ok=True)
    all_shots: list[Shot] = []
    for i, sid in enumerate(scene_ids):
        src = base / "scenes" / f"scene{sid:02d}.mp4"
        all_shots += plan_shots(sid, probe_duration(src), i / max(1, len(scene_ids) - 1), all_shots[-1] if all_shots else None, seed)
    parts = []
    for i, s in enumerate(all_shots):
        out = cut / f"shot{i:03d}.mp4"
        src = base / "scenes" / f"scene{s.scene:02d}.mp4"
        ff("-ss", f"{s.start}", "-t", f"{s.dur}", "-i", str(src), "-vf", shot_filter(s, w, h), "-r", str(FPS), "-an",
           "-c:v", "libx264", "-crf", "14", "-preset", "veryfast", str(out), "-y")
        parts.append(out)
        print(f"shot {i + 1}/{len(all_shots)} scene {s.scene} {s.kind} {s.dur:.2f}s", flush=True)
    lst = cut / "list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    silent = base / f"{name}_silent.mp4"
    ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(silent), "-y")
    final = silent
    if audio:
        import sound
        total = sum(s.dur for s in all_shots)
        final = base / f"{name}.mp4"
        inputs, graph = sound.build_graph(total, cut_times(all_shots))
        ff("-i", str(silent), *inputs, "-filter_complex", graph, "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac",
           "-b:a", "192k", "-shortest", str(final), "-y")
    print(f"EDIT DONE {final} shots={len(all_shots)} avg={sum(s.dur for s in all_shots) / len(all_shots):.2f}s", flush=True)
    return final


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--scenes", nargs="+", type=int, required=True)
    ap.add_argument("--w", type=int, default=2160)
    ap.add_argument("--h", type=int, default=3840)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--audio", action="store_true")
    a = ap.parse_args()
    render(Path(a.dir), a.name, a.scenes, a.w, a.h, a.seed, a.audio)


if __name__ == "__main__":
    main()
