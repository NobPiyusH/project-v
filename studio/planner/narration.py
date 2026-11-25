"""Narration length check at plan time (25 Nov 2026).

Why: the first live Moon Short planned 10 s, but its two narration lines ran about 12 s, so the master came out 14 s with a shot slowed to
fill its slot. Estimating the spoken length of every line before anything is generated lets the owner shorten the line while it is free.

Pure apart from reading config/narration.yaml once. Estimate = words / words-per-second (per language) + small pauses for punctuation.
A line's slot is the time from where it starts to the end of its beat (or to the next line's start / the video's end when it has no beat).
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from studio.config import REPO_ROOT

CONFIG_FILE = REPO_ROOT / "config" / "narration.yaml"
_DEFAULTS: dict[str, Any] = {"words_per_sec": {"default": 2.5}, "pause_sec": {"comma": 0.3, "sentence": 0.4}, "warn_over": 0.10, "fail_over": 0.25}
_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)
_SENTENCE_END = re.compile(r"[.!?…]+(?=\s|$)")
_COMMA = re.compile(r"[,;:—–]|\s-\s")


@dataclass(frozen=True)
class NarrationConfig:
    words_per_sec: dict[str, float]
    comma_pause: float
    sentence_pause: float
    warn_over: float
    fail_over: float

    def rate(self, language: str | None) -> float:
        lang = (language or "en").lower().split("-")[0]
        return float(self.words_per_sec.get(lang, self.words_per_sec.get("default", 2.5)))


@lru_cache(maxsize=4)
def _load(path: str) -> NarrationConfig:
    raw: dict[str, Any] = {}
    p = Path(path)
    if p.is_file():
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    pause = {**_DEFAULTS["pause_sec"], **(raw.get("pause_sec") or {})}
    return NarrationConfig({**_DEFAULTS["words_per_sec"], **(raw.get("words_per_sec") or {})}, float(pause["comma"]), float(pause["sentence"]),
                           float(raw.get("warn_over", _DEFAULTS["warn_over"])), float(raw.get("fail_over", _DEFAULTS["fail_over"])))


def config(path: Path | None = None) -> NarrationConfig:
    return _load(str(path or CONFIG_FILE))


def count_words(text: str) -> int:
    return len(_WORD.findall(text))


def estimate_sec(text: str, language: str | None = "en", cfg: NarrationConfig | None = None) -> float:
    """Spoken seconds for one line: words / rate + a pause at every comma and sentence end (the last one too: voice files keep a short tail)."""
    c = cfg or config()
    t = text.strip()
    words = count_words(t)
    if words == 0:
        return 0.0
    ends = len(_SENTENCE_END.findall(t))
    commas = len(_COMMA.findall(t))
    return round(words / c.rate(language) + commas * c.comma_pause + ends * c.sentence_pause, 2)


@dataclass
class LineCheck:
    line: int  # 1-based; 0 = the whole narration (rule-based plans)
    words: int
    estimated_sec: float
    slot_sec: float | None
    over_pct: float | None  # how much longer than the slot (negative: shorter)
    level: str  # ok | warn | fail | unchecked
    message: str | None = None


@dataclass
class NarrationCheck:
    lines: list[LineCheck] = field(default_factory=list)
    estimated_total_sec: float = 0.0
    planned_sec: float | None = None
    total_over_pct: float | None = None

    @property
    def problems(self) -> list[str]:
        return [ln.message for ln in self.lines if ln.level == "fail" and ln.message]

    @property
    def warnings(self) -> list[str]:
        return [ln.message for ln in self.lines if ln.level == "warn" and ln.message]

    def as_dict(self) -> dict[str, Any]:
        return {"estimatedTotalSec": self.estimated_total_sec, "plannedSec": self.planned_sec, "totalOverPct": self.total_over_pct,
                "warnings": self.warnings, "problems": self.problems, "lines": [asdict(ln) for ln in self.lines]}


def _sec(x: float) -> str:
    return str(int(round(x))) if abs(x - round(x)) < 0.05 else f"{x:.1f}"


def _message(subject: str, est: float, slot: float, rate: float, what: str) -> str:
    cut = max(1, math.ceil((est - slot) * rate))
    return f"{subject} needs about {_sec(est)} s but the {what} is {_sec(slot)} s: shorten it by about {cut} word{'' if cut == 1 else 's'}"


def check_lines(texts: list[str], slots: list[float | None], language: str | None = "en", planned_sec: float | None = None,
                cfg: NarrationConfig | None = None, what: str = "beat") -> NarrationCheck:
    """texts[i] against slots[i] (seconds available to that line; None = not known, the line is only estimated)."""
    c = cfg or config()
    rate = c.rate(language)
    out = NarrationCheck(planned_sec=planned_sec)
    for i, (text, slot) in enumerate(zip(texts, slots, strict=True), 1):
        est = estimate_sec(text, language, c)
        words = count_words(text)
        out.estimated_total_sec = round(out.estimated_total_sec + est, 2)
        if slot is None or slot <= 0:
            out.lines.append(LineCheck(i, words, est, None, None, "unchecked"))
            continue
        over = (est - slot) / slot
        level = "fail" if over > c.fail_over else "warn" if over > c.warn_over else "ok"
        out.lines.append(LineCheck(i, words, est, round(slot, 2), round(over * 100, 1), level,
                                   None if level == "ok" else _message(f"line {i}", est, slot, rate, what)))
    if planned_sec and planned_sec > 0:
        out.total_over_pct = round((out.estimated_total_sec - planned_sec) / planned_sec * 100, 1)
    return out


def check_plan(lines: list[Any], beats: list[Any], language: str | None = "en", cfg: NarrationConfig | None = None) -> NarrationCheck:
    """An imported plan's lines (ImportedLine: text, startSec, beat) against its beats (ImportedBeat: startSec, durationSec)."""
    total_end = max((b.startSec + b.durationSec for b in beats), default=0.0)
    starts: list[float | None] = []
    for ln in lines:
        if ln.startSec is not None:
            starts.append(float(ln.startSec))
        elif ln.beat is not None and 1 <= ln.beat <= len(beats):
            starts.append(float(beats[ln.beat - 1].startSec))
        else:
            starts.append(None)
    slots: list[float | None] = []
    for i, ln in enumerate(lines):
        st = starts[i]
        if st is None:
            slots.append(None)
        elif ln.beat is not None and 1 <= ln.beat <= len(beats):
            b = beats[ln.beat - 1]
            slots.append(round(b.startSec + b.durationSec - st, 3))
        else:
            nxt = next((s for s in starts[i + 1:] if s is not None and s > st), total_end)
            slots.append(round(nxt - st, 3))
    return check_lines([ln.text for ln in lines], slots, language, round(total_end, 3), cfg)


def check_spoken_total(texts: list[str], planned_sec: float, language: str | None = "en", cfg: NarrationConfig | None = None) -> NarrationCheck:
    """Rule-based plans have no beats: all the lines together are compared with the planned duration (one slot, line 0)."""
    c = cfg or config()
    est = round(sum(estimate_sec(t, language, c) for t in texts), 2)
    out = NarrationCheck(estimated_total_sec=est, planned_sec=planned_sec)
    if planned_sec > 0:
        over = (est - planned_sec) / planned_sec
        out.total_over_pct = round(over * 100, 1)
        level = "fail" if over > c.fail_over else "warn" if over > c.warn_over else "ok"
        msg = None if level == "ok" else _message("the narration", est, planned_sec, c.rate(language), "video")
        out.lines.append(LineCheck(0, sum(count_words(t) for t in texts), est, planned_sec, round(over * 100, 1), level, msg))
    return out
