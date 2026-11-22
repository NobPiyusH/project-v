"""Scene spec for one diagram shot: schema, validation, palette and the text-card fallback.

A spec is plain JSON the Director's LLM can write from a narration line:

{"title": "...", "duration": 8, "elements": [ ... ], "steps": [ ... ]}

Coordinates are Manim scene units: x -7.1..7.1 (left..right), y -4..4 (bottom..top), 16:9 frame.
Elements are visible from the start unless a "show" step targets them (then they start hidden; set "visible": true
to override). Steps run on a timeline in seconds: {"at": 1.0, "dur": 1.0, "do": "show", "target": "gun"}.
"""

from __future__ import annotations

PALETTE = {
    "bg": "#0B1020", "panel": "#151C32", "text": "#E8ECF8", "muted": "#8A94B8",
    "cyan": "#4CC9F0", "blue": "#4895EF", "violet": "#7B6CF6", "orange": "#F9A03F",
    "red": "#EF476F", "green": "#06D6A0", "yellow": "#FFD166", "white": "#FFFFFF",
    "pure_red": "#FF2A2A", "pure_green": "#2AFF4A", "pure_blue": "#2A4AFF",
}
MIN_DURATION, MAX_DURATION = 5.0, 12.0
MAX_ELEMENTS, MAX_STEPS = 60, 80
X_MAX, Y_MAX = 7.1, 4.0

EASINGS = ("linear", "smooth", "ease_in", "ease_out", "ease_in_out", "bounce", "there_and_back")
ACTIONS = ("show", "hide", "move", "color", "scale", "pulse", "draw", "flow", "mix")

# element type -> (required numeric fields, required text fields)
ELEMENT_FIELDS = {
    "box": (("x", "y", "w", "h"), ()),
    "layer": (("x", "y", "w", "h"), ()),
    "circle": (("x", "y", "r"), ()),
    "line": (("x1", "y1", "x2", "y2"), ()),
    "arrow": (("x1", "y1", "x2", "y2"), ()),
    "beam": (("x1", "y1", "x2", "y2"), ()),
    "text": (("x", "y"), ("text",)),
    "particles": (("x", "y", "count"), ()),
    "dots_grid": (("x", "y", "cols", "rows", "dx", "dy"), ()),
    "rgb_mix": (("x", "y", "r"), ()),
    "callout": (("x", "y", "tx", "ty"), ("text",)),
}
# which element types an action works on (others work on any element)
ACTION_TYPES = {
    "flow": ("beam", "line", "arrow"),
    "mix": ("rgb_mix",),
    "draw": ("box", "layer", "circle", "line", "arrow", "beam"),
}


def color_hex(c, default="text") -> str:
    """A palette name or a #RRGGBB string -> #RRGGBB."""
    if c is None:
        return PALETTE[default]
    if isinstance(c, str) and c in PALETTE:
        return PALETTE[c]
    return c


def _is_color(c) -> bool:
    if isinstance(c, str) and c in PALETTE:
        return True
    return isinstance(c, str) and len(c) == 7 and c[0] == "#" and all(ch in "0123456789abcdefABCDEF" for ch in c[1:])


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_spec(spec) -> list[str]:
    """All problems found (empty list = valid). Messages are written for the LLM that repairs the spec."""
    errs: list[str] = []
    if not isinstance(spec, dict):
        return ["spec must be a JSON object"]
    d = spec.get("duration")
    if not _num(d) or not MIN_DURATION <= d <= MAX_DURATION:
        errs.append(f"duration must be a number between {MIN_DURATION:g} and {MAX_DURATION:g} seconds")
        d = MAX_DURATION
    if not isinstance(spec.get("title", ""), str):
        errs.append("title must be a string")
    els = spec.get("elements")
    if not isinstance(els, list) or not els:
        return [*errs, "elements must be a non-empty list"]
    if len(els) > MAX_ELEMENTS:
        errs.append(f"at most {MAX_ELEMENTS} elements")
    ids: dict[str, str] = {}
    for i, e in enumerate(els):
        where = f"elements[{i}]"
        if not isinstance(e, dict):
            errs.append(f"{where} must be an object")
            continue
        eid, typ = e.get("id"), e.get("type")
        if not isinstance(eid, str) or not eid:
            errs.append(f"{where}.id must be a non-empty string")
        elif eid in ids:
            errs.append(f"{where}.id '{eid}' is duplicated")
        else:
            ids[eid] = typ
        if typ not in ELEMENT_FIELDS:
            errs.append(f"{where}.type must be one of {sorted(ELEMENT_FIELDS)}")
            continue
        nums, texts = ELEMENT_FIELDS[typ]
        for f in nums:
            if not _num(e.get(f)):
                errs.append(f"{where} ({typ} '{eid}') needs numeric '{f}'")
            elif f in ("x", "x1", "x2", "tx") and abs(e[f]) > X_MAX + 0.01:
                errs.append(f"{where}.{f}={e[f]} is outside the frame (|x| <= {X_MAX})")
            elif f in ("y", "y1", "y2", "ty") and abs(e[f]) > Y_MAX + 0.01:
                errs.append(f"{where}.{f}={e[f]} is outside the frame (|y| <= {Y_MAX})")
        for f in texts:
            if not isinstance(e.get(f), str) or not e.get(f).strip():
                errs.append(f"{where} ({typ} '{eid}') needs text '{f}'")
        if "color" in e and not _is_color(e["color"]):
            errs.append(f"{where}.color must be a palette name {sorted(PALETTE)} or #RRGGBB")
        if "text" in e and not isinstance(e["text"], str):
            errs.append(f"{where}.text must be a string")
        if typ == "particles" and _num(e.get("count")) and not 1 <= e["count"] <= 80:
            errs.append(f"{where}.count must be 1..80")
        if typ == "dots_grid" and _num(e.get("cols")) and _num(e.get("rows")) and e["cols"] * e["rows"] > 400:
            errs.append(f"{where}: cols*rows must be <= 400")
    steps = spec.get("steps", [])
    if not isinstance(steps, list):
        return [*errs, "steps must be a list"]
    if len(steps) > MAX_STEPS:
        errs.append(f"at most {MAX_STEPS} steps")
    for i, s in enumerate(steps):
        where = f"steps[{i}]"
        if not isinstance(s, dict):
            errs.append(f"{where} must be an object")
            continue
        act = s.get("do")
        if act not in ACTIONS:
            errs.append(f"{where}.do '{act}' is not allowed; use one of {list(ACTIONS)}")
            continue
        if not _num(s.get("at")) or s["at"] < 0:
            errs.append(f"{where}.at must be a number >= 0 (seconds)")
        if not _num(s.get("dur")) or s["dur"] <= 0:
            errs.append(f"{where}.dur must be a number > 0 (seconds)")
        elif _num(s.get("at")) and s["at"] + s["dur"] > d + 0.01:
            errs.append(f"{where} ends at {s['at'] + s['dur']:.1f}s, after the shot's duration {d:g}s")
        tgt = s.get("target")
        if tgt not in ids:
            errs.append(f"{where}.target '{tgt}' is not an element id")
        elif act in ACTION_TYPES and ids[tgt] not in ACTION_TYPES[act]:
            errs.append(f"{where}: '{act}' works only on {list(ACTION_TYPES[act])}, not {ids[tgt]}")
        if s.get("ease") is not None and s["ease"] not in EASINGS:
            errs.append(f"{where}.ease must be one of {list(EASINGS)}")
        if act == "move" and not any(_num(s.get(k)) for k in ("dx", "dy", "x", "y")):
            errs.append(f"{where}: move needs dx/dy or x/y")
        if act == "color" and not _is_color(s.get("to")):
            errs.append(f"{where}: color needs 'to' (palette name or #RRGGBB)")
        if act == "scale" and not _num(s.get("to")):
            errs.append(f"{where}: scale needs numeric 'to'")
    return errs


def fallback_spec(title: str, lines: list[str] | None = None, duration: float = 6.0) -> dict:
    """A clear animated text card (title, staggered lines, sweeping underline): used when a spec can't be made or
    rendered. It moves (never a panned still) and always validates."""
    duration = max(MIN_DURATION, min(MAX_DURATION, float(duration)))
    title = (title or "").strip()[:80] or "Diagram"
    lines = [x.strip()[:90] for x in (lines or []) if x and x.strip()][:4]
    rule_y = 1.45 if lines else -0.4
    els = [{"id": "title", "type": "text", "x": 0, "y": 2.2 if lines else 0.4, "text": title, "size": 54, "color": "white"},
           {"id": "rule", "type": "line", "x1": -4, "y1": rule_y, "x2": 4, "y2": rule_y, "color": "cyan", "width": 6}]
    steps = [{"at": 0.2, "dur": 0.8, "do": "show", "target": "title", "from": "up"},
             {"at": 0.8, "dur": 1.0, "do": "draw", "target": "rule", "ease": "ease_in_out"}]
    for i, ln in enumerate(lines):
        eid = f"l{i}"
        els.append({"id": eid, "type": "text", "x": 0, "y": 0.5 - i * 0.95, "text": ln, "size": 34, "color": "text"})
        steps.append({"at": min(1.6 + i * 0.9, duration - 0.9), "dur": 0.7, "do": "show", "target": eid, "from": "up"})
    steps.append({"at": max(0.0, duration - 1.6), "dur": 1.2, "do": "pulse", "target": "rule"})
    return {"version": 1, "title": title, "duration": duration, "fallback": True, "elements": els, "steps": steps}

# ------------------------------------------------------------------------------------------------- ambient motion

AMBIENT_PULSE = ("box", "layer", "circle", "dots_grid", "particles", "rgb_mix", "callout")
AMBIENT_FLOW = ("beam", "line", "arrow")
TITLE_SIZE, LABEL_MIN_SIZE, X_LABEL, X_FRAME = 40, 28, 5.3, 6.9  # one title size for every diagram; floating labels at least 28
PULSE_PERIOD = 2.0        # seconds per gentle breathing cycle
MAX_TOTAL_STEPS = 240


def enrich_motion(spec: dict) -> dict:
    """A copy of a valid spec in which nothing sits still: after an element has appeared it breathes (a small pulse
    every PULSE_PERIOD seconds, staggered), and lines/arrows/beams carry travelling dots for the rest of the shot.
    Text is made larger and something is visible at t=0 (no black opening). Deterministic; the Director's own steps are
    kept as they are (22 Nov 2026: most diagram shots were frozen for most of their length)."""
    import copy

    out = copy.deepcopy(spec)
    d = float(out["duration"])
    # The finished video burns the narration captions into the bottom of the frame: a diagram's own bottom caption
    # (y <= -3.0) would be printed over them (owner saw it, 23 Nov 2026), so it is dropped.
    gone = {e["id"] for e in out["elements"] if e["type"] == "text" and float(e.get("y", 0)) <= -3.0}
    if gone and len(gone) < len(out["elements"]):
        out["elements"] = [e for e in out["elements"] if e["id"] not in gone]
        out["steps"] = [s for s in out.get("steps", []) if s.get("target") not in gone]
    els, steps = out["elements"], out.setdefault("steps", [])
    for e in els:  # mojibake from an upstream encoding bug ("90Â°"): repaired at the last moment
        for key in ("text",):
            if isinstance(e.get(key), str):
                e[key] = e[key].replace("Â°", "°")
    tops = sorted((e for e in els if e["type"] == "text" and float(e.get("y", 0)) >= 2.9), key=lambda e: -float(e["y"]))
    for k, e in enumerate(tops):  # one title (the highest); any other text up there becomes a label below it
        if k == 0:
            e["size"] = TITLE_SIZE
        else:
            e["y"] = 2.3
    for e in els:  # every title is the same size; floating labels get a modest floor (bigger ones overlapped each other)
        if e.get("text") and e["type"] == "text" and e not in tops[:1]:
            e["size"] = max(int(e.get("size", 26)), LABEL_MIN_SIZE)
            half = len(e["text"]) * 0.09  # about half the printed width in scene units at size 28
            e["x"] = max(-(X_FRAME - half), min(X_FRAME - half, float(e["x"])))  # keep labels inside the frame
    labels = sorted((e for e in els if e["type"] == "text" and e not in tops[:1]), key=lambda e: (round(float(e["y"]), 1), float(e["x"])))
    for a, b in zip(labels, labels[1:]):  # labels on the same line must not touch: push the right one along
        if abs(float(a["y"]) - float(b["y"])) < 0.4:
            need = (len(a["text"]) + len(b["text"])) * 0.1 + 0.3
            if float(b["x"]) - float(a["x"]) < need:
                b["x"] = min(X_LABEL, float(a["x"]) + need)
                if float(b["x"]) - float(a["x"]) < need:
                    a["x"] = float(b["x"]) - need
    appears = {s["target"] for s in steps if s.get("do") in ("show", "draw")}
    if els and all(e["id"] in appears and not e.get("visible") for e in els):
        first = next((e for e in els if e["type"] == "text"), els[0])
        first["visible"] = True
    ready: dict[str, float] = {}
    for s in steps:
        if s.get("do") in ("show", "draw"):
            ready[s["target"]] = max(ready.get(s["target"], 0.0), float(s["at"]) + float(s["dur"]))
    flowing = {s["target"] for s in steps if s.get("do") == "flow"}
    extra: list[dict] = []
    for i, e in enumerate(els):
        t0 = min(ready.get(e["id"], 0.0) + 0.2, d - 1.0)
        typ = e["type"]
        if typ in AMBIENT_PULSE:
            t = t0 + (i * 0.37) % 1.0
            while d - t >= 0.8:
                extra.append({"at": round(t, 3), "dur": round(min(PULSE_PERIOD, d - t), 3), "do": "pulse",
                              "target": e["id"], "amount": 0.04})
                t += PULSE_PERIOD
        elif typ in AMBIENT_FLOW and e["id"] not in flowing and d - t0 >= 0.8:
            extra.append({"at": round(t0, 3), "dur": round(d - t0, 3), "do": "flow", "target": e["id"], "count": 3,
                          "loops": max(1, round((d - t0) / 2.2)), "r": 0.07, "color": e.get("color", "cyan")})
    steps.extend(extra[: max(0, MAX_TOTAL_STEPS - len(steps))])
    return out
