"""The Scout step of a Dream Short: Gemini watches similar Shorts that already exist, a text model turns what it saw into cliches and
angles, and the writer is later told what not to copy. Remembers watched Shorts in scout_watch so a repeat idea does not pay to watch
them again. Port of DreamScout; runs inside DreamService's transactions."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from studio.dream import looks
from studio.dream.llm_types import DreamWorld, ScoutAngle, ScoutBrief, Similar
from studio.dream.settings import DreamSettings
from studio.dream.wire_types import DreamScoutRequest, DreamScoutResult, ScoutVideo
from studio.ledger.budget_guard import GEMINI
from studio.media.wire import from_json, to_json

SCOUT_VIDEOS = 6  # Shorts watched per idea
SCOUT_ESTIMATE_INR = Decimal("6")  # measured Rs 4.56 and Rs 5.96 (19 Nov)

_YT_ID = re.compile(r"(?:youtube\.com/(?:shorts/|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]{11})")


def video_key(url: str | None) -> str | None:
    m = _YT_ID.search(url or "")
    return m.group(1) if m else None


@dataclass
class Scouted:
    """What the Scout found and the angle the owner picked, as the writer will read it."""

    brief: ScoutBrief
    videos: list[ScoutVideo]
    angle: int

    def chosen(self) -> ScoutAngle | None:
        """The angle the owner picked (1-based; out of range falls back to the nearest), or None when there are none."""
        a = self.brief.angles
        return None if not a else a[min(max(self.angle, 1), len(a)) - 1]

    def for_writer(self) -> str:
        s = "\n\nWhat already exists (make this Short clearly different):"
        if self.brief.cliches:
            s += "\nAvoid these clichés: " + "; ".join(self.brief.cliches)
        if self.brief.angles:
            a = self.brief.angles[min(max(self.angle, 1), len(self.brief.angles)) - 1]
            # The owner's chosen angle shapes the WHOLE Short; where it conflicts with the look's usual structure, the angle wins.
            s += ("\nThe owner chose this angle; build EVERY shot around it, as consecutive moments of it (it overrides "
                  f"the look's usual shot structure where they conflict): {a.title} - {a.idea}")
        seen = [v.shows for v in self.videos if v.shows is not None][:8]
        if seen:
            s += "\nDo not copy any of these existing Shorts: " + " | ".join(seen)
        return s


class DreamScout:
    def __init__(self, svc: Any, db: Any, llm: Any, pricing: Any, budget: Any, router: Any, evidence: Any, settings: DreamSettings):
        self.svc, self.db, self.llm, self.pricing, self.budget, self.router, self.evidence = svc, db, llm, pricing, budget, router, evidence
        self.listen_model = settings.listen_model
        self.brief_model = settings.brief_model

    def scouted(self, d: dict[str, Any]) -> Scouted | None:
        raw = d.get("scout_json")
        if raw is None:
            return None
        m = raw if isinstance(raw, dict) else json.loads(raw)
        if m.get("brief") is None:
            return None
        brief = ScoutBrief.model_validate(m["brief"])
        videos = [from_json(ScoutVideo, v) for v in m.get("videos") or []]
        return Scouted(brief, videos, int(d["scout_angle"]))

    def scout_wanted(self, dream_id: uuid.UUID) -> bool:
        return self.svc.dream(dream_id).get("scout") is True

    def scout_request(self, dream_id: uuid.UUID, notes: str | None) -> DreamScoutRequest:
        d = self.svc.dream(dream_id)
        self.budget.require(SCOUT_ESTIMATE_INR, "Dream Short Scout (watch similar Shorts)")
        query = (d.get("idea") or "") + ("" if not notes or not notes.strip() else " " + notes.strip())
        return DreamScoutRequest(self.svc.storage_id(dream_id), query.strip(), self.listen_model, SCOUT_VIDEOS,
                                 looks.look(d.get("look")).brief, self.watched_shorts())

    def watched_shorts(self) -> dict[str, ScoutVideo]:
        """Shorts the Scout already watched (last 90 days, newest 200), so it doesn't pay to watch them again."""
        rows = self.db.rows("SELECT video_key, watched::text AS w FROM scout_watch WHERE watched_at > now() - interval '90 days' "
                            "ORDER BY watched_at DESC LIMIT 200")
        return {r["video_key"]: from_json(ScoutVideo, json.loads(r["w"])) for r in rows}

    def remember_watched(self, videos: list[ScoutVideo] | None, model: str | None) -> None:
        for v in videos or []:
            key = video_key(v.url)
            watched = v.shows is not None or v.error == "not about this idea"
            if key is None or not watched:
                continue  # a failed watch (private, removed, timeout) is not remembered: next time it is tried again
            self.db.execute("INSERT INTO scout_watch (video_key, url, watched, model) VALUES (%(k)s, %(u)s, CAST(%(w)s AS jsonb), %(m)s) "
                            "ON CONFLICT (video_key) DO NOTHING", {"k": key, "u": v.url, "w": json.dumps(to_json(v)), "m": model})

    def save_scout(self, dream_id: uuid.UUID, req: DreamScoutRequest | None, r: DreamScoutResult) -> str:
        paid = 0.0
        for u in r.usage or []:
            inr = self.pricing.text_inr(u)
            paid += float(inr)
            self.svc.cost(dream_id, GEMINI, inr, u.total_tokens, "token", False, "Dream Short Scout: Gemini watched a Short")
        if r.searches > 0 and r.source == "google":
            inr = self.pricing.grounding_inr(r.searches)
            paid += float(inr)
            self.svc.cost(dream_id, GEMINI, inr, r.searches, "query", False, "Dream Short Scout: Google Search")
        decision = self.router.record_external(
            None, "DREAM_SCOUT", "video.watch", GEMINI,
            f"found {len(r.videos or [])} similar Shorts via {r.source}; Gemini watches them from their links (only model that takes video)",
            [{"provider": GEMINI}])
        self.svc.tag("routing_decision", decision, dream_id)
        self.remember_watched(r.videos, None if req is None else req.model)
        seen = [v for v in (r.videos or []) if v.error is None and v.shows is not None]
        if not seen:
            return self._no_angles(dream_id, paid, "found no similar Shorts" + ("" if r.error is None else f" ({r.error})"))
        d = self.svc.dream(dream_id)
        since = datetime.now(UTC)
        system = ("You advise a private studio that makes short, wordless, music-led vertical videos (YouTube Shorts) that\n"
                  "feel warm, nostalgic and real. Below are Shorts that already exist for the owner's idea, as a viewer saw\n"
                  "them. Find what most of them do (the cliches to avoid), what none of them do (the gaps), and propose 3\n"
                  "concrete angles that would make the owner's Short clearly different yet still feel-good and believable\n"
                  "(a new object or place, a new camera idea, a new time of light, a twist at the end...). Never suggest\n"
                  "copying any of them. Return JSON: {\"cliches\": [short phrases], \"gaps\": [short phrases],\n"
                  "\"angles\": [{\"title\": 2-5 words, \"idea\": one or two concrete sentences, \"why\": why it stands out}]}.\n")
        user = f"Owner's idea: {d.get('idea')}\nLook: {looks.look(d.get('look')).brief}\n"
        for n, v in enumerate(seen, 1):
            user += (f"\nShort {n}" + ("" if v.views is None else f" ({v.views} views)") + f": {v.shows} | look: {v.look} | camera: {v.camera}"
                     f" | shots: {v.shots} | music: {v.music} | hook: {v.hook} | why it works: {v.works}")
        brief = self.llm.call(None, "dream.scout", system, user, ScoutBrief, self.brief_model)
        self.svc.link_llm_costs(dream_id, "dream.scout", since)
        if brief is None or not brief.angles:
            return self._no_angles(dream_id, paid + self.svc.llm_costs_since(dream_id, "dream.scout", since), "found Shorts but no useful angle")
        self.db.execute("UPDATE dream_short SET scout_json = CAST(%(j)s AS jsonb), scout_angle = 1, updated_at = now() WHERE id = %(d)s",
                        {"j": json.dumps({"videos": [to_json(v) for v in seen], "brief": brief.model_dump(), "source": r.source}), "d": dream_id})
        self.evidence.cost_check(dream_id, "SCOUT", float(SCOUT_ESTIMATE_INR), paid + self.svc.llm_costs_since(dream_id, "dream.scout", since))
        top = max((v.views for v in seen if v.views is not None), default=0)
        first = "-" if not brief.cliches else brief.cliches[0]
        return (f"Watched {len(seen)} similar Shorts" + (f" (top one {top:,} views)" if top > 0 else "") +
                f". Most do: {first}. Angle 1: {brief.angles[0].title} (pick another before approving).")

    def _no_angles(self, dream_id: uuid.UUID, paid: float, why: str) -> str:
        """Nothing similar exists (owner, 19 Nov 2026): carry on from the idea and the look alone instead of stopping."""
        self.evidence.cost_check(dream_id, "SCOUT", float(SCOUT_ESTIMATE_INR), paid)
        self.evidence.event(dream_id, "SCOUT", "scout", None, f"Scout {why}; carrying on from the idea alone", None)
        return f"Scout {why}, so the writer works from your idea and the look alone (no angle)."

    def too_similar(self, dream_id: uuid.UUID, w: DreamWorld, scouted: Scouted) -> str | None:
        """Our shots too close to an existing Short? Returns rewrite notes, or None. Cheap Gemini text call."""
        theirs = [v.shows for v in scouted.videos if v.shows is not None]
        if not theirs:
            return None
        user = "Our shots:" + "".join(f"\n{i + 1}. {s.scene}" for i, s in enumerate(w.shots or []))
        user += "\n\nExisting Shorts:" + "".join(f"\n{chr(ord('A') + i)}. {t}" for i, t in enumerate(theirs))
        s = self.llm.call(None, "dream.similar",
                          "Compare our planned shots with Shorts that already exist. A shot is too similar only when it is nearly the\n"
                          "same scene (same object in the same kind of place, seen the same way). Sharing a mood or a colour is fine.\n"
                          "Return JSON {\"similar\": [{\"shot\": our shot number, \"like\": the letter it copies, \"why\": a few words}]};\n"
                          "an empty list when nothing is too close.\n", user, Similar)
        if s is None or not s.similar:
            return None
        notes = "; ".join(f"shot {x.shot} is too close to an existing Short ({x.why}): make it a different scene" for x in s.similar)
        self.evidence.event(dream_id, "WRITE", "similar", None, notes, None)
        return notes

