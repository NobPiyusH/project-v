"""The Dream Short page's step timeline and live log, worked out from "status" events. Port of DreamTimeline.

Work time counts only the RUNNING stretches: waiting for the owner or a cost OK is not work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from studio.agents.jfmt import jround

DREAM_STEPS = ["SCOUT", "WRITE", "PICTURES", "CLIPS", "MUSIC", "FINISH"]
MIN_SAMPLES = 2
LOG_SIZE = 20


@dataclass
class Event:
    step: str | None
    kind: str
    notes: str | None
    data: dict[str, Any] | None
    at: datetime

    def status(self) -> str | None:
        return None if not self.data or self.data.get("status") is None else str(self.data["status"])


@dataclass
class Cost:
    inr: float
    at: datetime


@dataclass
class StepTime:
    step: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    work_seconds: int
    cost_inr: float
    typical_seconds: int | None


@dataclass
class _Acc:
    status: str = "PENDING"
    start: datetime | None = None
    end: datetime | None = None
    work_millis: int = 0
    cost: float = 0.0


def median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2


def _step(e: Event) -> str | None:
    return e.step if e.step in DREAM_STEPS else None


def _close(a: _Acc, at: datetime, status: str) -> None:
    if a.end is None:
        a.end = at
        a.status = status


def _millis(a: datetime, b: datetime) -> int:
    return int((b - a).total_seconds() * 1000)


def _walk(events: list[Event], now: datetime | None) -> dict[str, _Acc]:
    acc = {s: _Acc() for s in DREAM_STEPS}
    st = [e for e in events if e.kind == "status" and _step(e) is not None and e.status() is not None]
    prev: str | None = None
    for i, e in enumerate(st):
        s = _step(e)
        status = e.status()
        a = acc[s]
        if prev is not None and prev != s:
            _close(acc[prev], e.at, "DONE")
        if status == "RUNNING" and a.end is not None:
            a = acc[s] = _Acc()  # run again (a redo from this step): the newest run counts
        if a.start is None:
            a.start = e.at
        if status == "COMPLETED":
            _close(a, e.at, "DONE")
        elif status == "CANCELLED":
            _close(a, e.at, "CANCELLED")
        else:
            a.status = status
        nxt = st[i + 1].at if i + 1 < len(st) else now
        if status == "RUNNING" and nxt is not None and nxt > e.at:
            a.work_millis += _millis(e.at, nxt)
        prev = s
    return acc


def steps(events: list[Event], costs: list[Cost], now: datetime, typical: dict[str, int]) -> list[StepTime]:
    acc = _walk(events, now)
    statuses = [e for e in events if e.kind == "status" and _step(e) is not None]
    for c in costs:
        at = None
        for e in statuses:
            if e.at > c.at:
                break
            at = _step(e)
        if at is not None:
            acc[at].cost += c.inr
    out = []
    for s in DREAM_STEPS:
        a = acc[s]
        out.append(StepTime(s, a.status, a.start, a.end, a.work_millis // 1000, jround(a.cost * 100) / 100.0, typical.get(s)))
    return out


def finished_work(events: list[Event]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s, a in _walk(events, None).items():
        if a.end is not None and a.status == "DONE" and a.work_millis > 0:
            out[s] = a.work_millis // 1000
    return out


def typical(past: list[dict[str, int]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in DREAM_STEPS:
        xs = [float(m[s]) for m in past if m.get(s) is not None]
        if len(xs) >= MIN_SAMPLES:
            out[s] = jround(median(xs))
    return out


@dataclass
class LogLine:
    at: datetime
    text: str


def _name(step: str) -> str:
    return step[0] + step[1:].lower()


def describe(e: Event) -> str | None:
    step = "" if e.step is None else _name(e.step)
    notes = "" if e.notes is None else " — " + (e.notes[:160] + "…" if len(e.notes) > 160 else e.notes)
    d = e.data
    k = e.kind
    if k == "status":
        st = e.status() or ""
        return {
            "RUNNING": f"{step} started{notes}",
            "WAITING": f"{step} is waiting for you{notes}",
            "WAITING_REVIEW": f"{step} is waiting for your review (goes on by itself after the window)",
            "WAITING_COST": f"{step} is waiting for your OK on the cost{notes}",
            "WAITING_BATCH": f"{step} is waiting its turn on the shared GPU{notes}",
            "FAILED_WAITING": None,  # the "failure" event says it
            "COMPLETED": "Short finished",
            "CANCELLED": f"Short cancelled (at {step})",
        }.get(st, f"{step}: {e.status()}")
    if k == "create":
        return f"Short created{notes}"
    if k == "rerun":
        return f"Redo from {step} started"
    if k == "approve":
        return f"You approved {step}"
    if k == "redo":
        return f"You asked to redo {step}{notes}"
    if k == "mode":
        return f"{step} mode set to {'?' if d is None else d.get('mode')}"
    if k == "retry":
        return "Retry requested"
    if k == "failure":
        return f"{step} failed{notes}"
    if k == "stop":
        return "Stop requested: clips stop after the current clip finishes"
    if k == "cancel":
        if d is not None and d.get("afterClip") is True:
            return "Cancel requested: it will apply after the current clip finishes"
        if d is not None and d.get("alreadyEnded") is True:
            return "Cancel requested: the Short had already ended"
        return "Cancel requested"
    if k == "cost":
        return (f"{step} cost ₹{'?' if d is None else d.get('actualInr')}"
                + ("" if d is None else f" (estimate ₹{d.get('estimateInr')})"))
    if k == "angle":
        return f"You picked Scout angle {'?' if d is None else d.get('angle')}"
    if k == "editing":
        return "Editing switches changed"
    return (f"{step}: " if step else "") + f"{k}{notes}"


def log(events: list[Event]) -> list[LogLine]:
    out: list[LogLine] = []
    for e in reversed(events):
        if len(out) >= LOG_SIZE:
            break
        text = describe(e)
        if text is not None:
            out.append(LogLine(e.at, text))
    return out


__all__ = ["DREAM_STEPS", "Cost", "Event", "LogLine", "StepTime", "describe", "finished_work", "log", "median", "steps", "typical"]
