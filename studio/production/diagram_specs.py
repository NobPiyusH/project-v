"""The Director's diagram shots: turns a narration line into a scene spec (JSON, drawn by workers/diagram/render.py). Port of DiagramSpecs.

One LLM call through the studio's text client, validated here; an invalid spec gets ONE repair call that shows the model its errors;
still invalid (or the model fails) = the clear animated text-card spec, never a panned still. The Python worker validates again before
drawing (defence in depth, it is the authority on the schema). Off unless VIDEO_DIAGRAM_LANE=true.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from typing import Any

from pydantic import BaseModel

log = logging.getLogger(__name__)

TYPES = {"box", "layer", "circle", "line", "arrow", "beam", "text", "particles", "dots_grid", "rgb_mix", "callout"}
ACTIONS = {"show", "hide", "move", "color", "scale", "pulse", "draw", "flow", "mix"}
NUMS: dict[str, list[str]] = {
    "box": ["x", "y", "w", "h"], "layer": ["x", "y", "w", "h"], "circle": ["x", "y", "r"], "line": ["x1", "y1", "x2", "y2"],
    "arrow": ["x1", "y1", "x2", "y2"], "beam": ["x1", "y1", "x2", "y2"], "text": ["x", "y"], "particles": ["x", "y", "count"],
    "dots_grid": ["x", "y", "cols", "rows", "dx", "dy"], "rgb_mix": ["x", "y", "r"],
}

SYSTEM = """You draw one animated explainer diagram (16:9, dark background, clean labelled style) as a JSON scene spec.
Return ONLY this JSON object: {"title": str, "duration": 5..12, "elements": [...], "steps": [...]}.
Coordinates: x -7..7 (left to right), y -4..4 (bottom to top). Keep y between -3.3 and 3.2 for parts (a title
sits at y=3.5, a caption at y=-3.7). Do not overlap labels. Text must be short (under 40 characters per line).
Colours: a name (cyan blue violet orange red green yellow white text muted pure_red pure_green pure_blue) or #RRGGBB.
Element types (every element has a unique "id" and optional "color"):
  box {x,y,w,h,text?,size?} | layer {x,y,w,h,skew?,text?} (a slanted plate, for stacks) | circle {x,y,r,text?}
  line/arrow/beam {x1,y1,x2,y2,width?,dashed?} | text {x,y,text,size?} | particles {x,y,count,spread?}
  dots_grid {x,y,cols,rows,dx,dy,r?} | rgb_mix {x,y,r} (red/green/blue circles that overlap) | callout {x,y,text,tx,ty}
Steps run on a timeline in seconds, each {"at","dur","do","target", ...}; every step must end by the duration:
  show (optional "from": up|down|left|right) | hide | draw (outline grows; box/layer/circle/line/arrow/beam)
  move {dx,dy or x,y} | color {to} | scale {to} | pulse {amount?} | flow {count?,loops?,color?,reverse?} (dots travel
  along a line/arrow/beam) | mix (rgb_mix only). Optional "ease": linear smooth ease_in ease_out ease_in_out bounce.
Elements start visible unless a show/draw step targets them. Reveal the parts in the order the narration
explains them, then animate what happens (a beam flows, a layer changes colour). Be technically accurate: only
draw parts and flows that really exist. Use at most 25 elements and 30 steps."""


class SceneSpec(BaseModel):
    """What the model returns. Elements and steps stay loose maps: the schema is in the prompt and in validate()."""

    title: str | None = None
    duration: float | None = None
    elements: list[dict[str, Any]] | None = None
    steps: list[dict[str, Any]] | None = None


def _set_str(s: set[str]) -> str:
    return "[" + ", ".join(sorted(s)) + "]"  # Java TreeSet.toString


def _num(o: Any) -> bool:
    return isinstance(o, (int, float)) and not isinstance(o, bool)


def validate(spec: dict[str, Any]) -> list[str]:
    """Structural check (same rules as workers/diagram/spec.py validate_spec, the part a model gets wrong most)."""
    errs: list[str] = []
    d = spec.get("duration")
    dur = float(d) if _num(d) else -1.0
    if dur < 5 or dur > 12:
        errs.append("duration must be a number between 5 and 12")
        dur = 12.0
    els = spec.get("elements")
    if not isinstance(els, list) or not els:
        errs.append("elements must be a non-empty list")
        return errs
    if len(els) > 60:
        errs.append("at most 60 elements")
    ids: dict[str, str] = {}
    for i, e in enumerate(els):
        if not isinstance(e, dict):
            errs.append(f"elements[{i}] must be an object")
            continue
        id_, typ = e.get("id"), e.get("type")
        if not isinstance(id_, str) or not id_.strip():
            errs.append(f"elements[{i}].id must be a non-empty string")
        elif id_ in ids:
            errs.append(f"elements[{i}].id '{id_}' is duplicated")
        else:
            ids[id_] = str(typ)
        if not isinstance(typ, str) or typ not in TYPES:
            errs.append(f"elements[{i}].type must be one of {_set_str(TYPES)}")
            continue
        need = NUMS.get(typ, ["x", "y", "tx", "ty"] if typ == "callout" else [])
        for f in need:
            v = e.get(f)
            if not _num(v):
                errs.append(f"elements[{i}] ({typ}) needs numeric '{f}'")
            elif f.startswith("x") or f == "tx":
                if abs(v) > 7.11:
                    errs.append(f"elements[{i}].{f}={v} is outside the frame (|x| <= 7.1)")
            elif (f.startswith("y") or f == "ty") and abs(v) > 4.01:
                errs.append(f"elements[{i}].{f}={v} is outside the frame (|y| <= 4)")
        if typ in ("text", "callout") and not (isinstance(e.get("text"), str) and e["text"].strip()):
            errs.append(f"elements[{i}] ({typ}) needs text")
    st = spec.get("steps")
    if st is not None and not isinstance(st, list):
        errs.append("steps must be a list")
    elif isinstance(st, list):
        if len(st) > 80:
            errs.append("at most 80 steps")
        for i, s in enumerate(st):
            if not isinstance(s, dict):
                errs.append(f"steps[{i}] must be an object")
                continue
            act = s.get("do")
            if not isinstance(act, str) or act not in ACTIONS:
                errs.append(f"steps[{i}].do '{act}' is not allowed; use one of {_set_str(ACTIONS)}")
                continue
            at, du = s.get("at"), s.get("dur")
            if not _num(at) or at < 0 or not _num(du) or du <= 0:
                errs.append(f"steps[{i}] needs at >= 0 and dur > 0 (seconds)")
            elif at + du > dur + 0.01:
                errs.append(f"steps[{i}] ends after the duration {dur}s")
            target = str(s.get("target"))
            tt = ids.get(target)
            if tt is None:
                errs.append(f"steps[{i}].target '{target}' is not an element id")
            elif act == "flow" and tt not in ("beam", "line", "arrow"):
                errs.append(f"steps[{i}]: flow works only on beam/line/arrow")
            elif act == "mix" and tt != "rgb_mix":
                errs.append(f"steps[{i}]: mix works only on rgb_mix")
    return errs


def fallback(line: str | None, description: str | None, dur: float, why: str | None) -> dict[str, Any]:
    """Title + lines for the worker's animated text card (the worker builds the card; an invalid spec is its trigger)."""
    title = "Diagram" if not description or not description.strip() else description.strip()
    one = (line or "").strip()
    return {
        "title": title[:57] + "..." if len(title) > 60 else title,
        "duration": dur, "fallback": True, "fallbackReason": why if why is not None else "no spec",
        "lines": [] if not one else [one[:87] + "..." if len(one) > 90 else one],
    }


class DiagramSpecs:
    def __init__(self, llm, enabled: bool = False):
        self._llm, self._enabled = llm, enabled

    def enabled(self) -> bool:
        return self._enabled

    def spec_for(self, video_id: uuid.UUID, video_title: str, narration_line: str, description: str, seconds: float) -> dict[str, Any]:
        """The spec for one diagram shot as a map ready for prompt_json. fallback=True in the result means a text card will be drawn;
        the reason is in fallbackReason."""
        dur = max(5.0, min(12.0, float(math.ceil(seconds))))
        user = (f"Video: {video_title}\nNarration line this shot explains: {narration_line}\n"
                f"What the shot must show: {description}\nDuration: {dur} seconds.")
        last_err: str | None = None
        spec: dict[str, Any] | None = None
        errs: list[str] = []
        for attempt in range(2):  # the call, then ONE repair
            try:
                prompt = user if attempt == 0 else (f"{user}\n\nYour previous spec was invalid:\n{json.dumps(spec, separators=(',', ':'))}\n"
                                                    "Problems:\n- " + "\n- ".join(errs) + "\nReturn the corrected JSON object only.")
                s = self._llm.call(video_id, "DIAGRAM_SPEC" if attempt == 0 else "DIAGRAM_SPEC_REPAIR", SYSTEM, prompt, SceneSpec)
                spec = s.model_dump()
                spec["duration"] = dur
                spec.setdefault("version", 1)
                errs = validate(spec)
                if not errs:
                    return spec
                last_err = "; ".join(errs[:3])
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
                log.warning("Diagram spec call %d failed: %s", attempt + 1, last_err)
                break  # a failing model call is not repaired by asking again with the same text
        return fallback(narration_line, description, dur, last_err)
