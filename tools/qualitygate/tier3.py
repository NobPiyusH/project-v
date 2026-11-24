"""Tier 3: VLM judge. Pure and offline: prompt builder, reply parser, decision. The model call is an injected callable.

The gate never imports a model SDK. A backend is any `Callable[[JudgePrompt], str]` returning the model's raw text reply; it is passed in
by the caller and is OFF by default (`judge=None`). Tests use a fake. Decisions map defects onto tools/film4k/repair.py:
a short, small defect box becomes a `vace_inpaint` Region, anything big/long/unboxed becomes a whole-shot `reroll`.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tools.film4k.repair import Region, choose_mode

from .config import RUBRIC, THRESHOLDS
from .ffprobe import extract_frame_cmd
from .types import Shot


@dataclass(frozen=True)
class FrameRef:
    t: float  # seconds into the VIDEO
    path: str


@dataclass(frozen=True)
class JudgePrompt:
    shot_id: str
    text: str
    frames: tuple[FrameRef, ...]


Judge = Callable[[JudgePrompt], str]


class JudgeReplyError(ValueError):
    """The model's reply could not be turned into a verdict."""


class JudgeUnavailable(RuntimeError):
    """The backend could not judge (spend cap reached, network or HTTP error). The shot fails to the owner; nothing is auto-accepted."""


def sample_times(shot: Shot, n: int) -> list[float]:
    """Midpoints of n equal slices of the shot, so no sample lands on a cut."""
    step = shot.duration / n
    return [round(shot.start + step * (i + 0.5), 3) for i in range(n)]


def plan_times(shot: Shot, n: int, focus: tuple[float, ...] = (), min_gap: float = 0.12, extra: int = 3) -> list[float]:
    """Frame times: n points spread from just after the first to just before the last frame (so the first and last frame give a
    before/after pair for drift), plus `focus` moments the free checks flagged. Close duplicates are dropped; at most n + extra frames."""
    pad = min(0.1, shot.duration / 4)
    lo, hi = shot.start + pad, shot.end - pad
    base = [lo + (hi - lo) * i / max(n - 1, 1) for i in range(n)]
    times = list(base)
    for t in focus:
        if lo <= t <= hi and all(abs(t - x) >= min_gap for x in times) and len(times) < n + extra:
            times.append(t)
    return [round(t, 3) for t in sorted(times)]


def build_prompt(shot: Shot, frames: list[FrameRef], intent: str = "", rubric: dict[str, tuple[float, str]] = RUBRIC,
                 hints: tuple[str, ...] = ()) -> JudgePrompt:
    crit = "\n".join(f"- {k} (weight {w:.2f}): {desc}" for k, (w, desc) in rubric.items())
    listing = "\n".join(f"Frame {i + 1}: t={f.t - shot.start:.2f}s" for i, f in enumerate(frames))
    keys = ", ".join(f'"{k}": <0-10>' for k in rubric)
    flagged = ("Automatic checks flagged these moments, look closely there (they may be false alarms): " + "; ".join(hints) + "\n") if hints else ""
    text = (
        f"You are a strict video QA reviewer. The {len(frames)} attached frames come, in time order, from ONE {shot.duration:.1f}s shot of an "
        "AI-generated film. Frame 1 and the last frame are the start and end of the shot.\n"
        + (f"Intended content: {intent}\n" if intent else "")
        + f"{listing}\n\n"
        "Do these checks in order, comparing frames with each other (not judging each frame alone):\n"
        "1. COLOUR OF PARTS: for each main subject (for example a creature's wings, body, tail), note its colour in every frame. "
        "A part whose hue changes in some frames and returns later (for example a dark wing turning blue) is a MAJOR defect: give the time span "
        "and a box around that part.\n"
        "2. SHAPE: do limbs, wings, faces keep the same shape and count? Melting, extra or missing parts are MAJOR.\n"
        "3. REAL MOTION: compare the first and last frame. Moving characters or a camera move with parallax is real motion. If only the whole image "
        "slides or zooms, or nothing changes except tiny shifts, report a MAJOR defect 'near-static' with box null.\n"
        "4. Do NOT report normal film grain, soft focus, or small natural wobble as defects. Only list defects you can see in at least two frames "
        "or that are large.\n"
        + flagged
        + f"\nThen score each criterion from 0 (unusable) to 10 (flawless):\n{crit}\n\n"
        "For each defect give times in seconds relative to the shot start, a box (x, y, w, h as 0..1 fractions of the frame) "
        'when the defect is local, and severity "minor" or "major".\n'
        "Reply with ONLY this JSON, nothing else:\n"
        f'{{"scores": {{{keys}}}, "defects": [{{"kind": "...", "severity": "major", "t0": 0.0, "t1": 1.0, '
        '"box": [x, y, w, h] or null, "note": "..."}]}'
    )
    return JudgePrompt(shot.id, text, tuple(frames))


@dataclass(frozen=True)
class Defect:
    kind: str
    severity: str  # "minor" | "major"
    t0: float  # seconds relative to the shot start
    t1: float
    box: tuple[float, float, float, float] | None
    note: str = ""


@dataclass(frozen=True)
class Verdict:
    scores: dict[str, float]
    weighted: float
    defects: tuple[Defect, ...] = ()


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_verdict(reply: str, rubric: dict[str, tuple[float, str]] = RUBRIC) -> Verdict:
    """Parse the judge's JSON (code fences and chatter around it are tolerated). Raises JudgeReplyError on anything unusable."""
    m = _FENCE.search(reply)
    body = m.group(1) if m else reply
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise JudgeReplyError("no JSON object in reply")
    try:
        data = json.loads(body[start : end + 1])
    except json.JSONDecodeError as e:
        raise JudgeReplyError(f"invalid JSON: {e}") from e
    raw = data.get("scores")
    if not isinstance(raw, dict):
        raise JudgeReplyError("missing 'scores' object")
    scores: dict[str, float] = {}
    for key in rubric:
        try:
            scores[key] = min(10.0, max(0.0, float(raw[key])))
        except (KeyError, TypeError, ValueError) as e:
            raise JudgeReplyError(f"missing or non-numeric score '{key}'") from e
    total_w = sum(w for w, _ in rubric.values())
    weighted = sum(rubric[k][0] * v for k, v in scores.items()) / total_w
    defects = []
    for d in data.get("defects") or []:
        try:
            box = d.get("box")
            box_t = tuple(float(v) for v in box) if box else None
            if box_t is not None and len(box_t) != 4:
                box_t = None
            t0, t1 = float(d.get("t0", 0.0)), float(d.get("t1", 0.0))
            sev = "major" if str(d.get("severity", "minor")).lower() == "major" else "minor"
            defects.append(Defect(str(d.get("kind", "unspecified")), sev, t0, t1, box_t, str(d.get("note", ""))))
        except (TypeError, ValueError, AttributeError):
            continue  # a malformed defect entry is dropped; the scores still decide
    return Verdict(scores, round(weighted, 3), tuple(defects))


@dataclass(frozen=True)
class RepairPlan:
    mode: str  # "reroll" | "vace_inpaint"
    region: Region | None  # None for a whole-shot reroll
    reason: str

    def to_dict(self) -> dict:
        r = self.region
        return {"mode": self.mode, "reason": self.reason,
                "region": None if r is None else {"x": r.x, "y": r.y, "w": r.w, "h": r.h, "t0": r.t0, "t1": r.t1}}  # fmt: skip


@dataclass(frozen=True)
class Decision:
    action: str  # "accept" | "repair" | "fail"
    reason: str
    repairs: tuple[RepairPlan, ...] = ()
    weighted: float | None = None

    def to_dict(self) -> dict:
        return {"action": self.action, "reason": self.reason, "weighted": self.weighted,
                "repairs": [r.to_dict() for r in self.repairs]}  # fmt: skip


def _plan_for(defect: Defect, shot: Shot) -> RepairPlan:
    """Map one defect to a Region (clip-relative seconds) when it is boxed and valid, otherwise to a whole-shot reroll."""
    if defect.box is None:
        return RepairPlan("reroll", None, f"{defect.kind}: whole-shot defect")
    t0 = max(0.0, defect.t0)
    t1 = min(shot.duration, max(defect.t1, t0 + 0.2))
    x, y, w, h = defect.box
    try:
        region = Region(x, y, w, h, t0, t1)
        mode = choose_mode(region)  # validates the box and window
    except ValueError:
        return RepairPlan("reroll", None, f"{defect.kind}: box/window unusable")
    return RepairPlan(mode, None if mode == "reroll" else region, f"{defect.kind} at {t0:.1f}-{t1:.1f}s")


def decide(verdict: Verdict, shot: Shot, repairs_done: int = 0, cfg: dict[str, float] = THRESHOLDS) -> Decision:
    """accept / repair / fail. A repair plan counts as one repair; at most `max_repairs_per_shot` are ever spent on a shot.
    `fail` means the owner decides (Retry / Continue without it); a failed shot never kills the video."""
    major = [d for d in verdict.defects if d.severity == "major"]
    if verdict.weighted >= cfg["accept_score"] and not major:
        return Decision("accept", f"score {verdict.weighted:.1f} >= {cfg['accept_score']}", weighted=verdict.weighted)
    budget = int(cfg["max_repairs_per_shot"]) - repairs_done
    if budget <= 0:
        return Decision("fail", f"score {verdict.weighted:.1f}, repair budget spent", weighted=verdict.weighted)
    if verdict.weighted < cfg["reroll_below"] or not verdict.defects:
        why = f"score {verdict.weighted:.1f}" + ("" if verdict.defects else ", no localised defect")
        return Decision("repair", why, (RepairPlan("reroll", None, why),), verdict.weighted)
    ranked = sorted(verdict.defects, key=lambda d: (d.severity != "major", -(d.t1 - d.t0)))
    plans = [_plan_for(d, shot) for d in ranked]
    if any(p.mode == "reroll" for p in plans):  # a whole-shot redo makes local patches pointless
        first = next(p for p in plans if p.mode == "reroll")
        return Decision("repair", first.reason, (first,), verdict.weighted)
    return Decision("repair", f"{len(plans[:budget])} targeted repair(s)", tuple(plans[:budget]), verdict.weighted)


FrameExtractor = Callable[[str, float, str], None]


def _ffmpeg_extract(video: str, t: float, out: str) -> None:
    import subprocess

    subprocess.run(extract_frame_cmd(video, t, out), check=True, capture_output=True)


@dataclass
class ShotJudgement:
    shot: str
    verdict: Verdict | None
    decision: Decision
    frames: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"shot": self.shot, "scores": self.verdict.scores if self.verdict else None, "decision": self.decision.to_dict(),
                "defects": [d.__dict__ for d in self.verdict.defects] if self.verdict else []}  # fmt: skip


def judge_shot(video: str, shot: Shot, judge: Judge, repairs_done: int = 0, intent: str = "", cfg: dict[str, float] = THRESHOLDS,
               focus: tuple[float, ...] = (), hints: tuple[str, ...] = (),
               extract: FrameExtractor = _ffmpeg_extract, workdir: str | None = None) -> ShotJudgement:  # fmt: skip
    """Sample frames, ask the injected judge, parse, decide. An unusable reply fails the shot (owner decides); it never auto-accepts."""
    work = Path(workdir or tempfile.mkdtemp(prefix="qgate_"))
    refs = []
    for i, t in enumerate(plan_times(shot, int(cfg["frames_per_shot"]), focus)):
        path = str(work / f"{shot.id}_{i:02d}.jpg")
        extract(video, t, path)
        refs.append(FrameRef(t, path))
    prompt = build_prompt(shot, refs, intent, hints=hints)
    try:
        verdict = parse_verdict(judge(prompt))
    except JudgeReplyError as e:
        return ShotJudgement(shot.id, None, Decision("fail", f"judge reply unusable: {e}"), [r.path for r in refs])
    except JudgeUnavailable as e:
        return ShotJudgement(shot.id, None, Decision("fail", f"judge unavailable: {e}"), [r.path for r in refs])
    return ShotJudgement(shot.id, verdict, decide(verdict, shot, repairs_done, cfg), [r.path for r in refs])
