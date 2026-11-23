"""Check 2: text collisions and frame edges on diagram shots.

Two independent angles, both pure:
 * spec geometry: the Director's diagram spec (Manim units, frame 14.22 x 8) says where every label sits. Free text is
   not shrunk to fit, so its estimated box is tested against other text, against shapes it does not belong to, and
   against the frame edge. Widths are estimates (average glyph width), so overlaps are WARN, clear overlaps FAIL.
 * rendered frame: ink (bright pixels) touching the outer strip of a grayscale frame means something is cut off.
The picture-level check (does a human-readable label look wrong?) is the vision check run on diagram frames.
"""
from __future__ import annotations

import numpy as np

from . import FAIL, WARN, CheckResult, Finding

FRAME_W, FRAME_H = 14.22, 8.0
GLYPH_W, LINE_H = 0.0072, 0.012   # Manim units per font-size point: average glyph width and line height (estimates)
EDGE_MARGIN = 0.15


def text_box(e: dict) -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) of a free text element, centred on its x, y."""
    size = float(e.get("size", 26))
    w = len(str(e.get("text") or "")) * size * GLYPH_W
    h = size * LINE_H
    return e["x"] - w / 2, e["y"] - h / 2, e["x"] + w / 2, e["y"] + h / 2


def shape_box(e: dict) -> tuple[float, float, float, float] | None:
    t = e.get("type")
    if t in ("box", "layer"):
        return e["x"] - e["w"] / 2, e["y"] - e["h"] / 2, e["x"] + e["w"] / 2, e["y"] + e["h"] / 2
    if t == "circle":
        return e["x"] - e["r"], e["y"] - e["r"], e["x"] + e["r"], e["y"] + e["r"]
    if t == "rgb_mix":
        r = float(e.get("r", 0.5)) * 2.2   # three overlapping circles around the centre (measured on a rendered frame)
        return e["x"] - r, e["y"] - r, e["x"] + r, e["y"] + r
    return None


def overlap(a, b) -> float:
    """Overlapping area as a fraction of the smaller box (0 = apart, 1 = one inside the other)."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (w * h) / small if small > 0 else 0.0


def check_spec(spec: dict, ref: str) -> list[Finding]:
    out: list[Finding] = []
    els = spec.get("elements") or []
    texts = [e for e in els if e.get("type") == "text" and e.get("text")]
    shapes = [(e, shape_box(e)) for e in els if shape_box(e)]
    for s, (x0, y0, x1, y1) in shapes:
        if x0 < -FRAME_W / 2 or x1 > FRAME_W / 2 or y0 < -FRAME_H / 2 or y1 > FRAME_H / 2:
            out.append(Finding("diagram", FAIL, "DIAGRAM_SHAPE_CUT_OFF", f"Shape {s.get('id', '?')} extends past the frame edge.", ref))
    for e in texts:
        x0, y0, x1, y1 = text_box(e)
        if x0 < -FRAME_W / 2 + EDGE_MARGIN or x1 > FRAME_W / 2 - EDGE_MARGIN or y0 < -FRAME_H / 2 + EDGE_MARGIN or y1 > FRAME_H / 2 - EDGE_MARGIN:
            out.append(Finding("diagram", FAIL, "DIAGRAM_LABEL_CUT_OFF", f"Label “{e['text']}” reaches the frame edge.", ref))
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            o = overlap(text_box(a), text_box(b))
            if o > 0.05:
                sev = FAIL if o > 0.25 else WARN
                out.append(Finding("diagram", sev, "DIAGRAM_TEXT_OVERLAP", f"Labels “{a['text']}” and “{b['text']}” overlap ({o:.0%}).", ref))
    for a in texts:
        for s, box in shapes:
            o = overlap(text_box(a), box)
            if 0.15 < o < 0.95:   # fully inside a box is a deliberate label on that box
                out.append(Finding("diagram", WARN, "DIAGRAM_TEXT_ON_SHAPE", f"Label “{a['text']}” sits on shape {s.get('id', '?')} ({o:.0%}).", ref))
    return out


def in_caption_band(spec: dict, band_top_frac: float = 0.82) -> list[str]:
    """Texts the diagram puts in the bottom band where captions are drawn (fraction from the top of the frame)."""
    hits = []
    for e in spec.get("elements") or []:
        if e.get("type") == "text" and e.get("text"):
            y0, y1 = text_box(e)[1], text_box(e)[3]
            if (FRAME_H / 2 - y0) / FRAME_H >= band_top_frac or (FRAME_H / 2 - y1) / FRAME_H >= band_top_frac:
                hits.append(str(e["text"]))
    return hits


def edge_ink(gray: np.ndarray, strip_frac: float = 0.006, thresh: int = 90, min_frac: float = 0.002) -> dict:
    """Bright pixels in the outermost strip of a grayscale frame (0..255). Diagram backgrounds are dark."""
    h, w = gray.shape
    sy, sx = max(2, int(h * strip_frac)), max(2, int(w * strip_frac))
    sides = {"top": gray[:sy, :], "bottom": gray[-sy:, :], "left": gray[:, :sx], "right": gray[:, -sx:]}
    frac = {k: float((v > thresh).mean()) for k, v in sides.items()}
    return {"sides": [k for k, f in frac.items() if f > min_frac], "frac": frac}


def check_frame(gray: np.ndarray, ref: str) -> list[Finding]:
    r = edge_ink(gray)
    if r["sides"]:
        return [Finding("diagram", WARN, "DIAGRAM_INK_AT_EDGE", f"Bright content touches the {'/'.join(r['sides'])} edge of the frame.", ref)]
    return []


def run(shots: list[dict], specs: dict[int, dict], frames: dict[int, np.ndarray]) -> CheckResult:
    """shots: manifest shots; specs/frames keyed by shot n (only diagram shots)."""
    res = CheckResult("diagram")
    checked = 0
    for s in shots:
        if s.get("lane") != "diagram":
            continue
        ref = f"shot {s['n']}"
        if s["n"] in specs:
            res.findings += check_spec(specs[s["n"]], ref)
            checked += 1
        if s["n"] in frames:
            res.findings += check_frame(frames[s["n"]], ref)
            checked += 1
    if not checked:
        res.skipped = "no diagram shots with a spec or frame"
    res.stats = {"diagram_shots_checked": checked}
    return res
