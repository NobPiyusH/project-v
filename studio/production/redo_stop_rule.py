"""The AI Producer's stop rule (optimisation review item 4, owner approved 19 Nov 2026). Port of RedoStopRule.

Re-rolling the same prompt with a new seed does not fix a prompt problem: the autopilot Short paid four GPU rounds for "visible human
figures". So when a redo review names the same issue as the review before it on that step: a known recurring cause (people, text,
morphing, a still image) gets ONE firm prompt / negative-prompt fix; after that, or for any other repeated issue, the Producer asks
the owner instead of re-rolling. The round and rupee limits still apply on top. Workflow code is untouched: the rule only changes the
advice the (recorded) save-review activity returns, so running workflows replay unchanged.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from enum import Enum

from studio.db import Db
from studio.media.types import ProducerItemVerdict


@dataclass(frozen=True)
class Cause:
    """A cause that keeps coming back because of the prompt, and its fix. negative = extra Wan negative-prompt terms."""

    key: str
    label: str
    match: re.Pattern[str]
    prompt_fix: str
    negative: str


CAUSES: list[Cause] = [
    Cause("people", "visible people",
          re.compile(r"\b(human|humans|person|persons|people|figure|figures|silhouette|silhouettes|man|men|"
                     r"woman|women|child|children|crowd|face|faces|body|bodies|hand|hands)\b"),
          "The scene is completely empty of people: no humans, no figures, no silhouettes, no faces, no hands, no reflections of people.",
          "people, person, human figure, silhouette, face, hands, crowd, man, woman, child"),
    Cause("text", "text or letters", re.compile(r"\b(text|letters?|words?|caption|captions|watermark|logo|signage|writing)\b"),
          "No text anywhere: no letters, words, signs, numbers, logos or watermarks.",
          "text, letters, words, signage, numbers, logo, watermark"),
    Cause("morph", "morphing or deformed objects", re.compile(r"\b(morph\w*|warp\w*|melt\w*|deform\w*|distort\w*)\b"),
          "Every object keeps a solid, stable, physically real shape for the whole shot.",
          "morphing, melting, warping, deformed, distorted, shape-shifting"),
    Cause("still", "too little motion", re.compile(r"\b(static|still|slideshow|frozen|near-still|barely moves?|no motion|no movement)\b"),
          "The camera moves clearly and continuously through the whole shot, like a real handheld or drone move.",
          "static, still image, frozen, slideshow, no camera movement"),
]


class Action(Enum):
    NONE = "NONE"
    PROMPT_FIX = "PROMPT_FIX"
    ASK = "ASK"


@dataclass
class Outcome:
    """What the rule decided. verdict and redo_notes are what the Producer's advice should carry."""

    action: Action
    cause: Cause | None
    verdict: str
    redo_notes: str | None
    note: str | None


def cause(text: str) -> Cause | None:
    return next((c for c in CAUSES if c.match.search(text)), None)


def cause_by_key(key: str) -> Cause | None:
    return next((c for c in CAUSES if c.key == key), None)


def issues(items: list[ProducerItemVerdict] | None, summary: str | None) -> list[str]:
    """The issues a review names (in order, no repeats): a cause key when one matches, otherwise the item's problem as normalised words."""
    out: dict[str, None] = {}
    for i in items or []:
        if i.ok:
            continue
        problem = (i.problem or "").lower().strip()
        c = cause(problem)
        if c is not None:
            out[c.key] = None
        elif problem.strip():
            out["~" + re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", problem)).strip()] = None
    if not out:
        c = cause((summary or "").lower())
        if c is not None:
            out[c.key] = None
    return list(out)


def _words(s: str) -> set[str]:
    return {w for w in s[1:].split(" ") if len(w) > 2}


def _similar(a: str, b: str) -> bool:
    x, y = _words(a), _words(b)
    if not x or not y:
        return False
    return len(x & y) * 2 >= len(x | y)


def repeated(now: list[str], before: list[str]) -> list[str]:
    """The issues both reviews name: shared cause keys, or free-text problems sharing at least half their words."""
    out: dict[str, None] = {}
    for a in now:
        for b in before:
            if a == b or (a.startswith("~") and b.startswith("~") and _similar(a, b)):
                out[a] = None
    return list(out)


def decide(verdict: str, items: list[ProducerItemVerdict] | None, summary: str | None, redo_notes: str | None,
           previous_items: list[ProducerItemVerdict] | None, previous_summary: str | None, has_previous: bool, fix_tried: bool) -> Outcome:
    """Pure rule. previous = the review before this one on the same step (none = no previous); fix_tried = a prompt fix was already
    tried on this step."""
    if verdict != "redo" or not has_previous:
        return Outcome(Action.NONE, None, verdict, redo_notes, None)
    same = repeated(issues(items, summary), issues(previous_items, previous_summary))
    if not same:
        return Outcome(Action.NONE, None, verdict, redo_notes, None)
    known = next((c for c in (cause_by_key(k) for k in same) if c is not None), None)
    if known is not None and not fix_tried:
        notes = f"RECURRING ISSUE ({known.label}), firm fix: {known.prompt_fix}" + ("" if not redo_notes or not redo_notes.strip() else " " + redo_notes.strip())
        return Outcome(Action.PROMPT_FIX, known, "redo", notes,
                       f"Same issue twice in a row ({known.label}): trying one firm prompt fix before asking you.")
    what = known.label if known is not None else same[0][1:]
    return Outcome(Action.ASK, known, "ask", redo_notes,
                   f"Same issue twice in a row ({what})" + (" even after a prompt fix" if fix_tried else "")
                   + ": stopped re-rolling, please decide.")


def check(db: Db, owner_column: str, owner_id: uuid.UUID, stage: str, verdict: str, items: list[ProducerItemVerdict] | None,
          summary: str | None, redo_notes: str | None) -> Outcome:
    """The rule against the database: reads the previous review of this step for this video or Short. owner_column is video_id or
    dream_short_id (a constant, never user input)."""
    if verdict != "redo":
        return Outcome(Action.NONE, None, verdict, redo_notes, None)
    if owner_column not in ("video_id", "dream_short_id"):
        raise ValueError(owner_column)
    prev_rows = db.rows(f"SELECT items_json::text AS items, summary FROM producer_decision WHERE {owner_column} = %(o)s AND stage = %(s)s "
                        "ORDER BY created_at DESC LIMIT 1", {"o": owner_id, "s": stage})
    prev = prev_rows[0] if prev_rows else None
    fix_tried = db.rows(f"SELECT count(*) AS n FROM producer_decision WHERE {owner_column} = %(o)s AND stage = %(s)s AND stop_rule = 'prompt_fix'",
                        {"o": owner_id, "s": stage})[0]["n"] > 0
    prev_items: list[ProducerItemVerdict] = []
    if prev is not None and prev.get("items") is not None:
        prev_items = [ProducerItemVerdict(x.get("id"), int(x.get("score", 0)), bool(x.get("ok", False)), x.get("problem"), x.get("fix"))
                      for x in json.loads(prev["items"]) or []]
    return decide(verdict, items, summary, redo_notes, prev_items, None if prev is None else prev["summary"], prev is not None, fix_tried)
