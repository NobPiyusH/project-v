"""SVI scenes (off by default): pure rules shared by writer, pictures, clips, estimate and finish. Port of DreamSvi."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MAX_SCENES = 3
MAX_BEATS = 5  # 20 s: content drifts after that
BEAT_FRAMES = 81
NEW_FRAMES = 68  # a later beat adds 81 - 1 (anchor) - 12 (overlap) frames
FPS = 16
SETUP_SECONDS = 180.0
LENGTHS = [10, 15, 20]


def on(d: dict[str, Any]) -> bool:
    return d.get("svi_scenes") is not None and int(d["svi_scenes"]) > 0


def scenes(d: dict[str, Any]) -> int:
    return max(1, min(MAX_SCENES, int(d["svi_scenes"])))


def seconds(d: dict[str, Any]) -> int:
    return int(d["svi_scene_seconds"])


def clean_seconds(s: int | None) -> int:
    return s if s in LENGTHS else 20


def frames_of(beats: int) -> int:
    return 0 if beats <= 0 else BEAT_FRAMES + (beats - 1) * NEW_FRAMES


def beats_for(secs: int) -> int:
    want = secs * FPS + 1
    n = 1
    while frames_of(n) < want:
        n += 1
    return min(MAX_BEATS, n)


def clean_between(b: str | None) -> str:
    return "dissolve" if b is not None and b.lower() == "dissolve" else "cut"


def structure(n_scenes: int, secs: int, beats: int) -> str:
    story = " that tell one small story (a beginning, a turn and an end)" if n_scenes > 1 else ""
    cut = "Between scenes there is a cut to a new view of the same world (another place or moment)." if n_scenes > 1 else ""
    return (f"Invent {n_scenes} scene{'' if n_scenes == 1 else 's'} in the same world{story}. Each scene is ONE continuous, unbroken "
            f"camera shot of {secs} seconds told in {beats} beats of about 5 seconds: the camera never cuts inside a scene and keeps "
            "moving smoothly from one beat into the next, each beat continuing exactly where the one before left off. For each "
            "scene give \"scene\": what the camera sees at the START of the scene (one or two concrete sentences; it becomes the "
            f"scene's only picture) and \"beats\": exactly {beats} motion lines, one per beat, each saying how the camera keeps "
            f"moving and what slowly changes in view. {cut}")


def json_fields(n_scenes: int, beats: int) -> str:
    return ("\"style\": ONE line of visual style (lens, colour, light, texture) shared by every scene, \"scenes\": [{\"scene\": "
            f"the opening view, \"beats\": [{beats} motion lines]}}] with exactly {n_scenes} scenes")


@dataclass
class Beat:
    scene: int
    beat: int
    scene_text: str
    motion: str


def flatten(planned: list[Any] | None, n_scenes: int, beats: int) -> list[Beat]:
    """The writer's scenes as beats, cut or padded to the Short's shape (a missing beat repeats the scene's last motion)."""
    out: list[Beat] = []
    if planned is None:
        return out
    for p in planned[:min(n_scenes, len(planned))]:
        if p is None or p.scene is None or not p.scene.strip():
            continue
        lines = [b.strip() for b in (p.beats or []) if b is not None and b.strip()]
        last = lines[-1] if lines else "the camera keeps gliding slowly forward"
        scene_no = 1 if not out else out[-1].scene + 1
        for b in range(beats):
            out.append(Beat(scene_no, b + 1, p.scene.strip(), lines[b] if b < len(lines) else last))
    return out


def unfiltered(prompt: str | None) -> list[Beat]:
    """A blank line separates scenes and each line is a beat, the words used exactly as written (at most 3 scenes of 5 beats)."""
    out: list[Beat] = []
    scene = 0
    beat = 0
    gap = True
    for raw in re.split(r"\r\n|\r|\n|\u0085| | ", prompt or ""):
        line = raw.strip()
        if not line:
            gap = True
            continue
        if gap:
            scene += 1
            beat = 0
            gap = False
        if scene > MAX_SCENES:
            break
        beat += 1
        if beat <= MAX_BEATS:
            out.append(Beat(scene, beat, line, line))
    return out


def beats_per_scene(shots: list[dict[str, Any]]) -> list[int]:
    out: list[int] = []
    scene = -1
    for s in shots:
        n = int(s["scene_no"])
        if n != scene:
            out.append(0)
            scene = n
        out[-1] += 1
    return out


def scene_of(shot: dict[str, Any]) -> int:
    return 0 if shot.get("scene_no") is None else int(shot["scene_no"])


def beat_of(shot: dict[str, Any]) -> int:
    return 0 if shot.get("beat_no") is None else int(shot["beat_no"])
