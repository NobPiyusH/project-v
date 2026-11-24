"""Character and location bank, per-beat continuity and claims (pure: no I/O, no network).

Why: the same creature drifted between shots (a dragon's wing turned blue, the tone shifted) because every beat re-described it in
its own words. Each character and location is written down ONCE here with a fixed description; every beat only names ids, and the
prompt builder pastes the bank text verbatim, so every shot gets the identical words. Adopted from MovieAgent / ViMax / SAFE
(reports/AI video studio landscape.md).

Everything is optional and defaults to empty, so plans and stored JSON without these fields still load. camelCase names are the JSON
contract with the LLM, as in studio.planner.plan.
"""

from __future__ import annotations

from pydantic import BaseModel

TOLERANCE_SEC = 0.05  # beat timings are rounded to milliseconds upstream; anything beyond this is a real gap or overlap


class BankEntry(BaseModel):
    """A character or a location, stored once. description is pasted verbatim into every beat that uses it."""

    id: str = ""
    description: str = ""
    referenceImage: str | None = None  # noqa: N815  optional path to a reference picture, for models that accept one


class Bank(BaseModel):
    characters: list[BankEntry] | None = None
    locations: list[BankEntry] | None = None

    def character(self, id_: str) -> BankEntry | None:
        return next((c for c in self.characters or [] if c.id == id_), None)

    def location(self, id_: str) -> BankEntry | None:
        return next((c for c in self.locations or [] if c.id == id_), None)

    def empty(self) -> bool:
        return not self.characters and not self.locations


class BeatPlan(BaseModel):
    """One beat: what it shows (prompt), which bank entries it uses, when it runs, and what must stay as in the previous beat."""

    prompt: str | None = None
    characterIds: list[str] | None = None  # noqa: N815
    locationIds: list[str] | None = None  # noqa: N815
    startSec: float | None = None  # noqa: N815  seconds from the start of its scene
    durationSec: float | None = None  # noqa: N815
    continuity: str | None = None  # what must stay the same as the previous beat
    claims: list[str] | None = None  # factual claims made in this beat's narration, for a later verifier (nothing checks them yet)


class ScenePlan(BaseModel):
    lengthSec: float | None = None  # noqa: N815
    beats: list[BeatPlan] | None = None


class BankedPlan(BaseModel):
    bank: Bank | None = None
    scenes: list[ScenePlan] | None = None


def clean_ids(ids: list[str] | None) -> list[str]:
    seen: list[str] = []
    for i in ids or []:
        if i is not None and i.strip() and i.strip() not in seen:
            seen.append(i.strip())
    return seen


def bank_problems(bank: Bank | None) -> list[str]:
    out: list[str] = []
    if bank is None:
        return out
    for kind, entries in (("character", bank.characters or []), ("location", bank.locations or [])):
        seen: set[str] = set()
        for e in entries:
            if not e.id.strip():
                out.append(f"a {kind} has no id")
            elif e.id in seen:
                out.append(f"{kind} id '{e.id}' is used twice")
            seen.add(e.id)
            if e.id.strip() and not e.description.strip():
                out.append(f"{kind} '{e.id}' has no description")
    return out


def unknown_ids(bank: Bank | None, character_ids: list[str] | None, location_ids: list[str] | None) -> list[str]:
    """Messages for every id that is not in the bank (a missing bank knows nothing)."""
    b = bank or Bank()
    out = [f"unknown character '{i}'" for i in clean_ids(character_ids) if b.character(i) is None]
    return out + [f"unknown location '{i}'" for i in clean_ids(location_ids) if b.location(i) is None]


def validate(plan: BankedPlan) -> list[str]:
    """Every reason this plan must be rejected: bad bank, beats naming ids that are not in it, beats whose timings leave a gap, overlap
    or do not add up to the scene length. Empty list = valid. Timings are only checked on beats that carry them."""
    problems = bank_problems(plan.bank)
    for si, scene in enumerate(plan.scenes or [], 1):
        beats = scene.beats or []
        for bi, b in enumerate(beats, 1):
            problems += [f"scene {si} beat {bi}: {m}" for m in unknown_ids(plan.bank, b.characterIds, b.locationIds)]
        timed = [b for b in beats if b.durationSec is not None]
        if not timed:
            continue
        if len(timed) != len(beats):
            problems.append(f"scene {si}: some beats have a duration and some do not")
            continue
        if any((b.durationSec or 0.0) <= 0 for b in beats):
            problems.append(f"scene {si}: a beat has a duration of zero or less")
            continue
        clock = 0.0
        for bi, b in enumerate(beats, 1):
            if b.startSec is not None and abs(b.startSec - clock) > TOLERANCE_SEC:
                problems.append(f"scene {si} beat {bi}: starts at {b.startSec:g}s but the previous beat ends at {clock:g}s")
            clock += b.durationSec or 0.0
        if scene.lengthSec is not None and abs(clock - scene.lengthSec) > TOLERANCE_SEC:
            problems.append(f"scene {si}: beats add up to {clock:g}s but the scene is {scene.lengthSec:g}s")
    return problems


class PlanRejected(ValueError):
    pass


def ensure_valid(plan: BankedPlan) -> None:
    problems = validate(plan)
    if problems:
        raise PlanRejected("; ".join(problems))


def inject(bank: Bank | None, character_ids: list[str] | None, location_ids: list[str] | None) -> str:
    """The bank text for these ids, verbatim, in the order the beat lists them. Unknown ids raise PlanRejected."""
    problems = unknown_ids(bank, character_ids, location_ids)
    if problems:
        raise PlanRejected("; ".join(problems))
    b = bank or Bank()
    parts = [f"{c.description.strip()}" for i in clean_ids(character_ids) if (c := b.character(i)) is not None]
    parts += [f"{c.description.strip()}" for i in clean_ids(location_ids) if (c := b.location(i)) is not None]
    return " ".join(p if p.endswith((".", "!", "?")) else p + "." for p in parts)


def beat_prompt(bank: Bank | None, beat: BeatPlan, kind: str = "image") -> str:
    """The image or video prompt for one beat. The beat's own words come first, then the bank descriptions word for word (so every shot
    that uses a character carries exactly the same description), and for video the continuity note. With no ids and no note the beat's
    prompt comes back unchanged."""
    base = (beat.prompt or "").strip()
    bank_text = inject(bank, beat.characterIds, beat.locationIds)
    extras = [bank_text] if bank_text else []
    if kind == "video" and beat.continuity and beat.continuity.strip():
        extras.append("Keep the same as the previous beat: " + beat.continuity.strip())
    if not extras:
        return base
    parts = ([base] if base else []) + extras
    return " ".join(p if p.endswith((".", "!", "?")) else p + "." for p in parts)


def known_ids(bank: Bank | None, character_ids: list[str] | None, location_ids: list[str] | None) -> tuple[list[str], list[str]]:
    """The ids that are in the bank, for callers that drop a bad id and carry on instead of rejecting the plan."""
    b = bank or Bank()
    return ([i for i in clean_ids(character_ids) if b.character(i) is not None], [i for i in clean_ids(location_ids) if b.location(i) is not None])


def references(bank: Bank | None, character_ids: list[str] | None, location_ids: list[str] | None) -> list[str]:
    """Reference image paths of the bank entries a beat uses (known ids only), without repeats."""
    b = bank or Bank()
    entries = [b.character(i) for i in clean_ids(character_ids)] + [b.location(i) for i in clean_ids(location_ids)]
    out: list[str] = []
    for e in entries:
        if e is not None and e.referenceImage and e.referenceImage not in out:
            out.append(e.referenceImage)
    return out
