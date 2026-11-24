"""Post camera move for SVI scenes (pure functions; ffmpeg filter strings).

Why: Wan + SVI animates the subject but barely moves the camera, whatever the prompt says (24 Nov 2026: scenes 2, 5 and 8 were
nearly static). A slow push-in with a small sideways or tilt drift and a faint handheld shake, applied to the 4K plate before
RIFE, gives a real camera feel for free.
NOT a zoom-OUT: `scale=eval=frame` with a shrinking size segfaulted ffmpeg (scene 6). Every scene pushes in; variety comes
from the sideways drift (DX) and the tilt (DY).
"""

from __future__ import annotations

BEAT_S = 81 / 16  # one SVI beat: 81 frames at 16 fps
FADE_S = 0.5  # cross-fade between beats (the beats do not join exactly: SSIM 0.43-0.76 at the seams)
FRAMES = 227  # 3 beats - 2 fades, at 16 fps
ZOOM = 0.16
TILT_UP = frozenset({4, 6, 9, 12, 15})
DRIFT_RIGHT = frozenset({1, 5, 8, 11, 14})
DRIFT_LEFT = frozenset({2, 7, 10, 13})


def move(scene_id: int) -> dict:
    return {
        "zoom": ZOOM,
        "dx": 60 if scene_id in DRIFT_RIGHT else (-60 if scene_id in DRIFT_LEFT else 0),
        "dy": 160 if scene_id in TILT_UP else 0,
    }


def camera_filter(scene_id: int, w: int = 2160, h: int = 3840, frames: int = FRAMES) -> str:
    m = move(scene_id)
    last = frames - 1
    z = f"(1+{m['zoom']}*n/{last})"
    return (
        f"scale=w='trunc({w}*{z}/2)*2':h='trunc({h}*{z}/2)*2':eval=frame:flags=bilinear,"
        f"crop={w}:{h}:x='(in_w-{w})/2+{m['dx']}*(n/{last}-0.5)+14*sin(n*0.37)':"
        f"y='(in_h-{h})/2+{m['dy']}*(0.5-n/{last})+14*sin(n*0.29+1)',format=yuv420p"
    )


def join_filter(w: int = 2160, h: int = 3840) -> str:
    """Three beats -> one scene: scale to the same size and cross-fade (offsets 4.5625 s and 9.125 s)."""
    parts = [f"[{i}:v]scale={w}:{h}:flags=lanczos,setsar=1[{'abc'[i]}]" for i in range(3)]
    o1, o2 = BEAT_S - FADE_S, 2 * (BEAT_S - FADE_S)
    return (
        ";".join(parts) + f";[a][b]xfade=transition=fade:duration={FADE_S}:offset={o1:.4f}[ab];"
        f"[ab][c]xfade=transition=fade:duration={FADE_S}:offset={o2:.4f}"
    )


def rife_target(frames_in: int, speed: float = 1.25, fps_mult: int = 3) -> int:
    """Output frame count for one RIFE pass that raises 16 -> 48 fps and plays `speed` times faster at the same time."""
    return int(round(frames_in * fps_mult / speed))
