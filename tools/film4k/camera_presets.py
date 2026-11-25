"""Camera presets for generated shots (pure functions; no ffmpeg run, no pod).

Why (24 Nov 2026): Wan 2.2 + SVI animates the subject but barely moves the camera, and a post zoom reads as a slideshow. The pod tests showed
Wan 2.2 Fun Camera makes a REAL camera move (Zoom In, Pan Left good; the roll presets warp) and Fun VACE follows a camera path drawn as an
edge video. This module names the moves, plans a varied sequence per scene, and builds the ffmpeg filter for the VACE control video.

Two engines:
  fun_camera: one of ComfyUI's 9 WanCameraEmbedding poses plus a speed (0 to 10, tested at 1.0).
  vace_path:  an edge video made from the keyframe by an ffmpeg camera path; shrinking scales crash ffmpeg, so a pull-back is a push-in
              played backwards (`reverse`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

FUN_CAMERA_POSES = (
    "Static", "Pan Up", "Pan Down", "Pan Left", "Pan Right", "Zoom In", "Zoom Out", "Anti Clockwise (ACW)", "ClockWise (CW)",
)  # fmt: skip
TESTED = {"zoom_in", "pan_left", "pan_right", "slow_push", "drift_right"}
# Judged good by eye: zoom_in and pan_left on 24 Nov 2026 (480p, 2 s); pan_right, slow_push, drift_right on 25 Nov 2026 (720x1280, 81 frames, 5 s,
# docs/film4k.md "Motion block"). Usable but not trusted: zoom_out, pan_up, drift_left. Bad at 5 s: pan_down (black branch-like artefacts), fast_push (tower bends,
# scene rewrites itself); use fast_push only trimmed to about 2 s.


@dataclass(frozen=True)
class Preset:
    name: str
    engine: str  # "fun_camera" or "vace_path"
    pose: str = ""  # Fun Camera pose
    speed: float = 1.0
    path: str = ""  # VACE path name
    note: str = ""


def _fun(name, pose, speed=1.0, note=""):
    return Preset(name, "fun_camera", pose=pose, speed=speed, note=note)


def _vace(name, path, note=""):
    return Preset(name, "vace_path", path=path, note=note)


PRESETS: dict[str, Preset] = {
    p.name: p
    for p in (
        _fun("static", "Static", note="locked-off; subject motion only"),
        _fun("zoom_in", "Zoom In", note="tested: real forward move"),
        _fun("slow_push", "Zoom In", 0.5, "tested 25 Nov: good gentle push-in"),
        _fun("fast_push", "Zoom In", 1.8, "tested 25 Nov: bends buildings and rewrites the scene after about 2 s; trim or avoid"),
        _fun("zoom_out", "Zoom Out", note="tested 25 Nov: usable, modest pull-back"),
        _fun("pan_left", "Pan Left", note="tested: believable sideways move"),
        _fun("pan_right", "Pan Right", note="tested 25 Nov: good, clean sideways move"),
        _fun("drift_left", "Pan Left", 0.4, "tested 25 Nov: usable, ground texture shifts"),
        _fun("drift_right", "Pan Right", 0.4, "tested 25 Nov: good slow sideways drift"),
        _fun("pan_up", "Pan Up", note="tested 25 Nov: usable, weak tilt"),
        _fun("pan_down", "Pan Down", note="tested 25 Nov: bad, black branch artefacts grow over the ground"),
        _fun("roll_cw", "ClockWise (CW)", note="tested: roll only, last frame warps: use rarely"),
        _fun("roll_acw", "Anti Clockwise (ACW)", note="tested: roll only, last frame warps: use rarely"),
        _vace("push_in", "push_in", "custom path through VACE"),
        _vace("pull_back", "pull_back", "push-in played backwards"),
        _vace("pedestal_up", "pedestal_up", "camera rises"),
        _vace("pedestal_down", "pedestal_down", "camera lowers"),
        _vace("truck_left", "truck_left", "camera slides left with a slight push"),
        _vace("truck_right", "truck_right", "camera slides right with a slight push"),
        _vace("handheld_walk", "handheld_walk", "forward walk with shake"),
    )
}
RARE = {"roll_cw", "roll_acw"}
DEFAULT_CYCLE = ("zoom_in", "pan_left", "slow_push", "drift_right", "pan_right")  # fast_push dropped 25 Nov (bends buildings)


def preset(name: str) -> Preset:
    try:
        return PRESETS[name]
    except KeyError:
        raise KeyError(f"unknown camera preset {name!r}; choose one of: {', '.join(sorted(PRESETS))}") from None


def plan_camera(
    scene_id: int, n_beats: int, hint: str | list[str] | None = None, seed: int = 0, allow_untested: bool = True
) -> list[str]:
    """One preset per beat. A hint (a name, or a list of names per beat) wins. Otherwise a varied sequence: never the same
    move twice in a row, no roll presets, and a scene never ends on the move it began with."""
    if isinstance(hint, list):
        if len(hint) != n_beats:
            raise ValueError(f"camera hint lists {len(hint)} presets for {n_beats} beats")
        return [preset(h).name for h in hint]
    if isinstance(hint, str):
        return [preset(hint).name] * n_beats
    rng = random.Random(scene_id * 104729 + seed)
    pool = [n for n in DEFAULT_CYCLE if allow_untested or n in TESTED]
    if len(pool) < 2:
        pool = ["zoom_in", "pan_left"]
    out: list[str] = []
    for i in range(n_beats):
        options = [n for n in pool if (not out or n != out[-1]) and not (i == n_beats - 1 and n_beats > 1 and n == out[0])]
        out.append(rng.choice(options or pool))
    return out


# --- VACE camera paths: (z0, z1, fx0, fx1, fy0, fy1, shake px at width 480, reverse). fx/fy in -1..1 of the crop margin.
PATHS = {
    "push_in": (1.0, 1.35, 0.0, 0.0, 0.0, 0.0, 0, False),
    "pull_back": (1.0, 1.35, 0.0, 0.0, 0.0, 0.0, 0, True),
    "pedestal_up": (1.25, 1.25, 0.0, 0.0, 0.9, -0.9, 0, False),
    "pedestal_down": (1.25, 1.25, 0.0, 0.0, -0.9, 0.9, 0, False),
    "truck_left": (1.1, 1.22, 0.9, -0.9, 0.0, 0.0, 0, False),
    "truck_right": (1.1, 1.22, -0.9, 0.9, 0.0, 0.0, 0, False),
    "handheld_walk": (1.1, 1.3, 0.0, 0.0, 0.0, 0.0, 7, False),
}


def vace_path_filter(path: str, w: int, h: int, frames: int) -> str:
    """ffmpeg -vf that turns a looped still into the edge-video camera path VACE follows. Zoom only grows (shrinking scale=eval=frame
    crashes ffmpeg); a pull-back reverses the clip at the end."""
    if path not in PATHS:
        raise KeyError(f"unknown VACE path {path!r}; choose one of: {', '.join(sorted(PATHS))}")
    z0, z1, fx0, fx1, fy0, fy1, shake, rev = PATHS[path]
    last = max(1, frames - 1)
    z = f"({z0}+({z1}-{z0})*n/{last})"
    fx = f"({fx0}+({fx1}-{fx0})*n/{last})"
    fy = f"({fy0}+({fy1}-{fy0})*n/{last})"
    k = shake * w / 480
    x = f"(in_w-{w})/2*(1+{fx})+{k:.2f}*sin(n*0.7)"
    y = f"(in_h-{h})/2*(1+{fy})+{k:.2f}*sin(n*0.53+1)"
    vf = (
        f"scale=w='trunc({w}*{z}/2)*2':h='trunc({h}*{z}/2)*2':eval=frame:flags=bilinear,"
        f"crop={w}:{h}:x='{x}':y='{y}',edgedetect=low=0.1:high=0.3"
    )
    if rev:
        vf += ",reverse"
    return vf + ",format=yuv420p"


def control_video_cmd(image: str, out: str, path: str, w: int, h: int, frames: int, fps: int = 16) -> list[str]:
    """ffmpeg command (as an argv list) that writes the VACE control video for `path` from a keyframe image.
    The looped still is cut to `frames` frames BEFORE the filters (`-t`): `reverse` buffers the whole input, so an endless loop made
    the pull-back fail on the first real run (24 Nov 2026)."""
    return [
        "ffmpeg", "-y", "-loop", "1", "-framerate", str(fps), "-t", f"{frames / fps:.4f}", "-i", image,
        "-vf", vace_path_filter(path, w, h, frames),
        "-frames:v", str(frames), "-c:v", "libx264", "-crf", "12", out,
    ]  # fmt: skip


def last_frame_cmd(video: str, out_png: str) -> list[str]:
    """Last frame of a finished beat: the start picture of the next beat (last-frame chaining, preferred by the owner on 20 Nov)."""
    return ["ffmpeg", "-y", "-sseof", "-0.2", "-i", video, "-update", "1", "-frames:v", "1", "-q:v", "1", out_png]
