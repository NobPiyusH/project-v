"""Camera presets for the studio's pod video route (25 Nov 2026). The presets and their TESTED set live in tools/film4k/camera_presets.py
(Wan 2.2 Fun Camera, judged by eye on real pods); this module only loads them for the control plane and decides which preset each shot gets.

- A shot whose camera move IS a preset name (an imported plan, or a Director that was told the names) uses it. An untested preset is refused
  unless allow_untested is set (Fun Camera preset names outside TESTED were judged bad or not trusted at 5 s: pan_down, fast_push, roll_*).
- Any other shot (free-text camera move such as "slow push-in") gets a preset from `plan_camera`: varied, tested ones only, never the same twice in a row.
- A chain is at most CHAIN_BEATS beats long (owner-visible rule from the 25 Nov motion block: from the third chained beat the scene drifts
  blue and soft); the third beat restarts from its own fresh keyframe.
"""

from __future__ import annotations

import importlib
import re
import sys
from typing import Any

from studio.config import REPO_ROOT

CHAIN_BEATS = 2
WIDTH, HEIGHT, FRAMES, FPS = 720, 1280, 81, 16  # the tested Fun Camera size (docs/film4k.md, motion block)
_mod: Any = None


def presets_module() -> Any:
    """tools/film4k/camera_presets.py (a flat module: its sibling camera_graphs.py imports it by that name)."""
    global _mod
    if _mod is None:
        path = str(REPO_ROOT / "tools" / "film4k")
        if path not in sys.path:
            sys.path.insert(0, path)
        _mod = importlib.import_module("camera_presets")
    return _mod


def preset_name(move: str | None) -> str | None:
    """"Zoom In" / "zoom-in" / "zoom_in" -> "zoom_in" when that is a Fun Camera preset, else None."""
    if not move:
        return None
    name = re.sub(r"[\s-]+", "_", move.strip().lower())
    m = presets_module()
    p = m.PRESETS.get(name)
    return name if p is not None and p.engine == "fun_camera" else None


def check(names: list[str], allow_untested: bool = False) -> None:
    """Raises ValueError for an unknown, non-Fun-Camera or (without allow_untested) untested preset."""
    m = presets_module()
    for n in names:
        p = m.preset(n)  # KeyError text lists the choices
        if p.engine != "fun_camera":
            raise ValueError(f"camera preset {n!r} is not a Fun Camera preset")
        if n not in m.TESTED and not allow_untested:
            raise ValueError(f"camera preset {n!r} is not tested (tested: {', '.join(sorted(m.TESTED))}); set allowUntested to use it")


def assign(moves: list[str | None], allow_untested: bool = False, strict: bool = False, seed: int = 0) -> list[str]:
    """One preset per shot, in order (see the module doc). A move naming an untested preset is refused (ValueError) when strict (an
    imported plan named it on purpose); otherwise it is replaced by a tested preset, because a model's free text ("pan down") is a wish."""
    m = presets_module()
    filler = m.plan_camera(1, len(moves), None, seed, allow_untested=False)
    explicit: list[str | None] = []
    for move in moves:
        n = preset_name(move)
        if n is not None and n not in m.TESTED and not allow_untested:
            if strict:
                check([n], False)
            n = None
        explicit.append(n)
    pool = [n for n in m.DEFAULT_CYCLE if n in m.TESTED]
    out: list[str] = []
    for i, n in enumerate(explicit):
        if n is None:
            nxt = explicit[i + 1] if i + 1 < len(explicit) else None
            n = filler[i] if filler[i] != (out[-1] if out else None) and filler[i] != nxt else next(
                (c for c in pool if c != (out[-1] if out else None) and c != nxt), filler[i])
        out.append(n)
    return out
