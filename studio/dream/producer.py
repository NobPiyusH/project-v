"""AI Producer for Dream Shorts (autopilot, owner 19 Nov 2026). Port of DreamProducer. Same reviewer as the documentary pipeline (Gemini
reads, looks, watches, listens), with a rubric for music-only surreal Shorts. Its decisions go to the same producer_decision table (linked
to the Short), so the agreement score and learned rules are shared. Limits are the owner's: Rs 100 per Short, ask above Rs 50 per
decision, 3 redo rounds per step; on an autopilot Short also the daily cap."""

from __future__ import annotations

import json
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from studio.agents.jfmt import fixed
from studio.db import Db
from studio.dream import looks
from studio.dream.service import DreamService
from studio.ledger.budget_guard import GEMINI
from studio.media.types import ProducerAdvice, ProducerItem, ProducerReviewRequest, ProducerReviewResult, SpendDecision
from studio.media.wire import to_json
from studio.production import redo_stop_rule as stop_rule
from studio.production.producer import ProducerService, stop_rule_column

RUBRIC_BASE = (
    "You are the producer of a premium, privately owned YouTube Shorts channel (Dreamscapes): short surreal,\n"
    "dreamlike vertical videos with music only, no voice. Quality first, cost second. Judge each item strictly but\n"
    "fairly. score 0-10 (7+ = good enough to publish); ok = score >= 7. problem: the main issue in a few words, or\n"
    "\"\" if none. fix: a PRECISE instruction for a redo (a rewritten prompt), or \"\" if ok. verdict: approve (all\n"
    "ok), redo (specific items are not ok and a redo would fix them), ask (unsure, taste call, or needs the\n"
    "owner). confidence 0-1. redoNotes: the precise notes for the redo, naming the items. summary: 1-2 plain\n"
    "sentences for the owner.")

RUBRICS = {
    "WRITE": "Plan: one believable dream world shared by every shot, a scroll-stopping first shot, each shot a clear "
             "visual moment with a real camera move (human, not effects), feels calm and wondrous, not generic AI cliché.",
    "PICTURES": "Pictures: each matches its scene, strong vertical composition, one consistent world and palette, "
                "no text or letters, no artefacts, deformities or melted objects, looks real not like an effect.",
    "CLIPS": "Clips: WATCH each clip. It must really move like a real handheld/drone camera (no near-still or "
             "slideshow), match its scene and motion, no morphing, glitches, flicker or black frames.",
    "MUSIC": "Music: LISTEN. It fits the Short's mood (dreamy, calm, wondrous), starts strong, no quiet stretch, "
             "no abrupt end, no clipping.",
    "FINISH": "Finished Short: WATCH and LISTEN. Hook in the first second, cuts land on the music, every shot moves, "
              "one consistent look, no black or broken moments, music level right, a satisfying ending or loop.",
}


def _n(shot: dict[str, Any]) -> int:
    return int(shot["ord"]) + 1


def items(step: str, d: dict[str, Any], shots: list[dict[str, Any]]) -> list[ProducerItem]:
    """What the Producer looks at on each step; '@assetId' placeholders are resolved to the asset's storage URI later."""
    out: list[ProducerItem] = []
    if step == "WRITE":
        b = f"Title: {d.get('title') or ''}\n"
        for s in shots:
            b += f"{_n(s)}. {s.get('scene')}  [camera: {s.get('motion_prompt')}]\n"
        if shots:
            out.append(ProducerItem("plan", "Plan", "text", None, b, f"A Dream Short for: {d.get('idea')}"))
    elif step in ("PICTURES", "CLIPS"):
        col = "picture_uri" if step == "PICTURES" else "clip_uri"
        for s in shots:
            if s.get(col) is not None:
                out.append(ProducerItem(f"shot-{_n(s)}", f"Shot {_n(s)}", "image" if step == "PICTURES" else "video", s[col], None,
                                        f"Shot {_n(s)}: {s.get('scene')}" + (f". Motion: {s.get('motion_prompt')}" if step == "CLIPS" else "")))
    elif step in ("MUSIC", "FINISH"):
        asset = d.get("music_asset_id" if step == "MUSIC" else "final_asset_id")
        if isinstance(asset, uuid.UUID):
            out.append(ProducerItem("music" if step == "MUSIC" else "final", "Music" if step == "MUSIC" else "Finished Short",
                                    "audio" if step == "MUSIC" else "video", f"@{asset}", None, f"For: {d.get('idea')}"))
    return out


def fits(estimate: Decimal, ask_above: Decimal, spent: Decimal, limit: Decimal) -> bool:
    """Within the limits: approve. Pure rule so it can be tested without a database."""
    return estimate <= ask_above and spent + estimate <= limit


def _r0(v: Decimal) -> str:
    return str(v.quantize(Decimal(1), rounding=ROUND_HALF_UP))


class DreamProducer:
    def __init__(self, db: Db, dreams: DreamService, producer: ProducerService, pricing: Any, model: str = "gemini-3.6-flash"):
        self.db, self.dreams, self.producer, self.pricing, self.model = db, dreams, producer, pricing, model

    # ---- review

    def review_request(self, dream_id: str, step: str) -> ProducerReviewRequest | None:
        if step not in RUBRICS:
            return None
        did = uuid.UUID(dream_id)
        d = self.dreams.dream(did)
        if looks.unfiltered(d):
            return None  # the Producer judges "feel-good, no people": not the owner's own, unfiltered prompt
        shots = self.dreams.shots(did)
        its = self._resolve(items(step, d, shots))
        if not its:
            return None
        context = (f"Short: {d.get('title') or '(untitled)'}\nOwner's idea: {d.get('idea')}\n"
                   f"Look: {d.get('look')}, {len(shots)} shots, vertical 9:16, music only.")
        return ProducerReviewRequest(dream_id, "DREAM_" + step, self.model, RUBRIC_BASE + "\n" + RUBRICS[step], context, self._rules(step), its)

    def _resolve(self, its: list[ProducerItem]) -> list[ProducerItem]:
        out = []
        for i in its:
            if i.uri is not None and i.uri.startswith("@"):
                uri = self.db.rows("SELECT uri_hot FROM asset WHERE id = %(a)s", {"a": uuid.UUID(i.uri[1:])})[0]["uri_hot"]
                out.append(ProducerItem(i.id, i.label, i.kind, uri, i.text, i.expect))
            else:
                out.append(i)
        return out

    def _rules(self, step: str) -> list[str]:
        return [r["rule_text"] for r in self.db.rows(
            "SELECT rule_text FROM producer_rule WHERE enabled AND (stage IS NULL OR stage = %(s)s) "
            "ORDER BY evidence DESC, updated_at DESC LIMIT 20", {"s": "DREAM_" + step})]

    def save_review(self, dream_id: str, step: str, attempt: int, r: ProducerReviewResult) -> ProducerAdvice:
        did = uuid.UUID(dream_id)
        cost = Decimal(0) if r.usage is None else self.pricing.text_inr(r.usage)
        with self.db.transaction():
            if cost > 0:
                self.db.execute(
                    "INSERT INTO cost_entry (id, video_id, dream_short_id, provider_id, amount_inr, units, unit_type, estimated, note) "
                    "VALUES (%(id)s, NULL, %(d)s, %(p)s, %(amt)s, %(u)s, 'tokens', true, %(note)s)",
                    {"id": uuid.uuid4(), "d": did, "p": GEMINI, "amt": cost, "u": Decimal(r.usage.total_tokens),
                     "note": f"PRODUCER review of Dream Short {step} ({r.model})"})
            decision = uuid.uuid4()
            verdict = "ask" if r.verdict is None else r.verdict.lower()
            # Stop rule: the same issue twice in a row gets one firm prompt fix, then goes to the owner (not another re-roll).
            stop = stop_rule.check(self.db, "dream_short_id", did, "DREAM_" + step, verdict, r.items, r.summary, r.redo_notes)
            self.db.execute(
                "INSERT INTO producer_decision (id, dream_short_id, stage, attempt, verdict, confidence, summary, redo_notes, items_json, model, "
                "cost_inr, stop_rule, stop_cause) VALUES (%(id)s, %(d)s, %(s)s, %(a)s, %(verdict)s, %(c)s, %(sum)s, %(notes)s, "
                "CAST(%(items)s AS jsonb), %(m)s, %(cost)s, %(stop)s, %(cause)s)",
                {"id": decision, "d": did, "s": "DREAM_" + step, "a": attempt, "verdict": verdict,
                 "c": Decimal(str(r.confidence)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP), "sum": r.summary, "notes": stop.redo_notes,
                 "items": json.dumps(to_json(r.items or []), separators=(",", ":")), "m": r.model, "cost": cost,
                 "stop": stop_rule_column(stop), "cause": None if stop.cause is None else stop.cause.key})
            weak = [i.id for i in (r.items or []) if not i.ok]
            what = ("recommends APPROVE" if verdict == "approve"
                    else f"recommends REDO of {len(weak)} item{'' if len(weak) == 1 else 's'}" if verdict == "redo"
                    else "recommends you DECIDE (unsure)")
            text = (f"AI Producer {what} ({fixed(r.confidence * 100, 0)}% sure): {r.summary or ''}"
                    f"{'' if stop.note is None else ' ' + stop.note}"
                    f"{'' if stop.redo_notes is None or not stop.redo_notes.strip() else ' Suggested redo: ' + stop.redo_notes}")
            self.dreams.evidence.event(did, step, "producer", None, text, {"verdict": stop.verdict, "weak": weak})
        return ProducerAdvice(stop.verdict, r.confidence, text, stop.redo_notes, str(decision), weak)

    # ---- acting alone

    def decides(self, dream_id: str) -> bool:
        rows = self.db.rows("SELECT producer_decides FROM dream_short WHERE id = %(d)s", {"d": uuid.UUID(dream_id)})
        own = bool(rows[0]["producer_decides"]) if rows else False
        return own or self.producer.deciding()

    def spent(self, dream_id: uuid.UUID) -> Decimal:
        """What this Short has cost so far."""
        return Decimal(self.db.rows("SELECT COALESCE(sum(amount_inr), 0) AS s FROM cost_entry WHERE dream_short_id = %(d)s",
                                    {"d": dream_id})[0]["s"])

    def limit(self, dream_id: uuid.UUID) -> Decimal:
        """The most this Short may cost: the Producer's per-Short budget, and the daily cap when it is an autopilot Short."""
        budget = self.pricing.producer_budget_inr(True)
        rows = self.db.rows("SELECT s.daily_cap_inr FROM autopilot_setting s WHERE EXISTS "
                            "(SELECT 1 FROM autopilot_run r WHERE r.dream_short_id = %(d)s)", {"d": dream_id})
        daily = rows[0]["daily_cap_inr"] if rows else None
        return budget if daily is None else min(budget, Decimal(daily))

    def may_redo(self, dream_id: str, round_: int) -> bool:
        did = uuid.UUID(dream_id)
        return round_ <= self.pricing.producer_setting("roundsPerStage") and self.spent(did) < self.limit(did)

    def shot_ids(self, dream_id: str, item_ids: list[str] | None) -> list[str]:
        ords = [int(i[5:]) - 1 for i in (item_ids or []) if i.startswith("shot-")]
        if not ords:
            return []
        return [r["id"] for r in self.db.rows("SELECT id::text AS id FROM dream_shot WHERE dream_short_id = %(d)s AND ord = ANY(%(o)s) ORDER BY ord",
                                              {"d": uuid.UUID(dream_id), "o": ords})]

    def spend_decision(self, dream_id: str, estimate_inr: Decimal) -> SpendDecision:
        did = uuid.UUID(dream_id)
        with self.db.transaction():
            ask_above = self.pricing.producer_ask_above_inr()
            spent = self.spent(did)
            limit = self.limit(did)
            approve = self.decides(dream_id) and fits(estimate_inr, ask_above, spent, limit)
            text = (("AI Producer APPROVED this by itself" if approve else "AI Producer: ASKING YOU")
                    + f" (≈₹{_r0(estimate_inr)}; 'ask me above' ₹{_r0(ask_above)}; ₹{_r0(spent)} of this Short's ₹{_r0(limit)} limit used).")
            self.db.execute("INSERT INTO producer_decision (id, dream_short_id, stage, attempt, verdict, confidence, summary, acted, cost_inr) "
                            "VALUES (%(id)s, %(d)s, 'DREAM_CLIPS_SPEND', 0, %(v)s, 1, %(sum)s, %(acted)s, 0)",
                            {"id": uuid.uuid4(), "d": did, "v": "approve" if approve else "ask", "sum": text,
                             "acted": "approved_spend" if approve else "asked"})
        return SpendDecision(approve, text)

    def mark_acted(self, decision_id: str | None, acted: str) -> None:
        if decision_id is not None:
            self.producer.mark_acted(uuid.UUID(decision_id), acted)

    def record_owner_decision(self, dream_id: uuid.UUID, step: str, decision: str, notes: str | None) -> None:
        """The owner decided on a step the Producer reviewed: keep it with the latest open review (agreement score)."""
        self.db.execute(
            "UPDATE producer_decision SET owner_decision = %(dec)s, owner_notes = %(n)s, decided_at = now(), "
            "agreed = CASE WHEN %(dec)s = 'approve' THEN verdict = 'approve' ELSE verdict = 'redo' END "
            "WHERE id = (SELECT id FROM producer_decision WHERE dream_short_id = %(d)s AND stage = %(s)s AND owner_decision IS NULL "
            "ORDER BY created_at DESC LIMIT 1)", {"dec": decision, "n": notes, "d": dream_id, "s": "DREAM_" + step})

    def decisions(self, dream_id: uuid.UUID) -> list[dict[str, Any]]:
        return self.db.rows("SELECT id, stage, attempt, verdict, confidence, summary, redo_notes, acted, cost_inr, owner_decision, agreed, created_at "
                            "FROM producer_decision WHERE dream_short_id = %(d)s ORDER BY created_at DESC", {"d": dream_id})
