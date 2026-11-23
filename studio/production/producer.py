"""AI Producer, control side (docs/plans/ai-producer.md, owner 18 Nov 2026). Port of ProducerService.

In PRODUCER mode a multimodal model (on the media worker, where the files are) reviews a stage's output the way a producer would (reads the
script, LISTENS to every voice line, looks at the keyframes, WATCHES the clips and the master) and recommends approve / redo / ask. P1 is
advise-only; P2 lets a trusted Producer act inside the owner's limits. Every owner decision is compared with the recommendation (agreement
score) and kept as a lesson. Rules the Producer learns and the owner's recent notes go into every review.
"""

from __future__ import annotations

import json
import re
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel

from studio.agents.jfmt import fixed, jround
from studio.ledger.budget_guard import GEMINI
from studio.media.types import (
    NarrationRequest,
    ProducerAdvice,
    ProducerItem,
    ProducerReviewRequest,
    ProducerReviewResult,
    SpendDecision,
)
from studio.media.wire import to_json
from studio.production import redo_stop_rule as stop_rule
from studio.production.support import ProductionDeps, StageSupport

RUBRIC_BASE = """You are the producer of a premium, privately owned documentary studio. Quality first, cost second.
Judge each item strictly but fairly. score 0-10 (7+ = good enough to publish); ok = score >= 7.
problem: the main issue in a few words, or "" if none. fix: a PRECISE instruction for a redo (a rewritten
prompt or delivery note), or "" if ok. verdict: approve (all ok), redo (specific items are not ok and a redo
would fix them), ask (unsure, taste call, or the issue needs the owner). confidence 0-1. redoNotes: the
precise notes for the redo, naming the items. summary: 1-2 plain sentences for the owner."""

RUBRICS = {
    "SCRIPT": "Script: strong hook in the first line, true facts, one idea per line, fits the duration, covers what the owner asked for, ends well.",
    "NARRATION": "Narration audio: LISTEN to each line. Natural human delivery (not robotic), emotion and pace match the line and its delivery note, "
                 "every word clear and exactly as written, no mispronunciations, no instructions read aloud, no glitches, clicks or long silences.",
    "KEYFRAMES": "Keyframes: each image matches its shot description, strong composition for vertical video, no text or letters, no artefacts or "
                 "deformities, consistent look across shots.",
    "DRAFT_GEN": "Draft clips: WATCH each clip. It must really move (no near-still or slideshow), match the shot and its motion prompt, no glitches, "
                 "morphing, black frames or flicker.",
    "FINAL_GEN": "Final clips: WATCH each clip. Real, smooth motion matching the shot, no glitches, morphing, black frames or flicker; premium look.",
    "ASSEMBLY": "Finished video: WATCH and LISTEN. Hook in the first 2 seconds, good pacing, every shot moves, captions readable and in sync, voice clear "
                "over music, no black or broken moments, strong ending.",
}

_ASSET_COLUMNS = ("keyframe_asset_id", "draft_clip_asset_id", "final_clip_asset_id")


class LearnedRules(BaseModel):
    rules: list[str] | None = None


def reviews(stage: str) -> bool:
    """The stages the Producer reviews in P1."""
    return stage in RUBRICS


def _r0(v: Decimal) -> str:
    return str(Decimal(v).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def stop_rule_column(stop: stop_rule.Outcome) -> str | None:
    """The stop_rule column value for an outcome."""
    return {stop_rule.Action.PROMPT_FIX: "prompt_fix", stop_rule.Action.ASK: "ask", stop_rule.Action.NONE: None}[stop.action]


def with_style(base: NarrationRequest, notes: str | None) -> NarrationRequest:
    """The narration request for a Producer redo: its precise notes become the reading style (lines keep their delivery)."""
    if notes is None or not notes.strip():
        return base
    style = " ".join(re.split(r"\s+", notes.strip())[:40])
    return NarrationRequest(base.video_id, base.model, base.check_model, base.voice_name, style, base.language, base.lines, base.voice_provider)


class ProducerService(StageSupport):
    def __init__(self, d: ProductionDeps, model: str = "gemini-3.6-flash", llm: Any = None):
        super().__init__(d)
        self.model, self.llm = model, llm

    # ------------------------------------------------------------------------------------------- review request

    def review_request(self, video_id: uuid.UUID, stage: str) -> ProducerReviewRequest | None:
        """What to review for this stage, or None when the stage has nothing the Producer judges."""
        if not reviews(stage):
            return None
        items = {
            "SCRIPT": lambda: self.script_items(video_id),
            "NARRATION": lambda: self.narration_items(video_id),
            "KEYFRAMES": lambda: self.shot_items(video_id, "keyframe_asset_id", "image"),
            "DRAFT_GEN": lambda: self.shot_items(video_id, "draft_clip_asset_id", "video"),
            "FINAL_GEN": lambda: self.shot_items(video_id, "final_clip_asset_id", "video"),
            "ASSEMBLY": lambda: self.master_items(video_id),
        }[stage]()
        if not items:
            return None
        script = self.latest_script(video_id)
        context = (f"Video: {self._title(video_id)}\nOwner's prompt: {self.prompt(video_id)}"
                   + ("" if script is None else f"\nNarrator: {script.voice_name}, style: {script.voice_style}"))
        return ProducerReviewRequest(str(video_id), stage, self.model, RUBRIC_BASE + "\n" + RUBRICS[stage], context, self.rules(stage), items)

    def script_items(self, video_id: uuid.UUID) -> list[ProducerItem]:
        s = self.latest_script(video_id)
        if s is None:
            return []
        text = "".join(f"{ln.index + 1}. {ln.text}" + ("" if ln.delivery is None else f"  [delivery: {ln.delivery}]") + "\n" for ln in s.lines)
        return [ProducerItem("script", "Script", "text", None, text,
                             f"A script for: {self.prompt(video_id)} ({self.plan(video_id).durationSec} s)")]

    def narration_items(self, video_id: uuid.UUID) -> list[ProducerItem]:
        s = self.latest_script(video_id)
        out: list[ProducerItem] = []
        if s is None:
            return out
        for ln in s.lines:
            if ln.spoken and ln.audio_uri is not None:
                delivery = ln.delivery if ln.delivery is not None else ("natural narrator" if s.voice_style is None else s.voice_style)
                out.append(ProducerItem(f"line-{ln.index}", f"Line {ln.index + 1}", "audio", ln.audio_uri, None,
                                        f'Says exactly: "{ln.text}". Delivery: {delivery}'))
        return out

    def shot_items(self, video_id: uuid.UUID, asset_column: str, kind: str) -> list[ProducerItem]:
        if asset_column not in _ASSET_COLUMNS:
            raise ValueError(asset_column)
        out: list[ProducerItem] = []
        for r in self.db.rows(
                f"SELECT s.id, s.ord, s.description, s.prompt_json->>'keyframePrompt' AS kp, s.prompt_json->>'motionPrompt' AS mp, a.uri_hot "
                f"FROM shot s JOIN scene sc ON sc.id = s.scene_id JOIN asset a ON a.id = s.{asset_column} "
                "WHERE sc.video_id = %(v)s AND s.footage_asset_id IS NULL ORDER BY s.ord", {"v": video_id}):
            n = int(r["ord"]) + 1
            expect = (f"Shot {n}: {r['description']}. Prompt: {r['kp']}" if kind == "image" else f"Shot {n}: {r['description']}. Motion: {r['mp']}")
            out.append(ProducerItem(f"shot-{n}", f"Shot {n}", kind, r["uri_hot"], None, expect))
        return out

    def master_items(self, video_id: uuid.UUID) -> list[ProducerItem]:
        rows = self.db.rows("SELECT a.uri_hot FROM video v JOIN asset a ON a.id = v.master_asset_id WHERE v.id = %(v)s", {"v": video_id})
        return [ProducerItem("master", "Finished video", "video", rows[0]["uri_hot"], None, f"The finished video for: {self.prompt(video_id)}")] if rows else []

    def rules(self, stage: str) -> list[str]:
        """The owner's learned rules for this stage plus their most recent notes: the Producer follows the owner's taste."""
        out = [r["rule_text"] for r in self.db.rows(
            "SELECT rule_text FROM producer_rule WHERE enabled AND (stage IS NULL OR stage = %(s)s) ORDER BY evidence DESC, updated_at DESC LIMIT 20",
            {"s": stage})]
        out += [r["t"] for r in self.db.rows(
            "SELECT 'Owner feedback on ' || stage || ': ' || notes AS t FROM approval WHERE notes IS NOT NULL AND btrim(notes) <> '' "
            "AND actor = 'owner' AND (stage = %(s)s OR stage = 'ALL') ORDER BY ts DESC LIMIT 8", {"s": stage})]
        return out

    def _title(self, video_id: uuid.UUID) -> str:
        return self.db.rows("SELECT COALESCE(title, '') AS t FROM video WHERE id = %(v)s", {"v": video_id})[0]["t"]

    # ------------------------------------------------------------------------------------------- save + advice

    def save_review(self, video_id: uuid.UUID, stage: str, attempt: int, r: ProducerReviewResult) -> ProducerAdvice:
        cost = Decimal(0) if r.usage is None else self.d.pricing.text_inr(r.usage)
        with self.db.transaction():
            if cost > 0:
                self.d.ledger.record(video_id, GEMINI, cost, Decimal(r.usage.total_tokens), "tokens", True, f"PRODUCER review of {stage} ({r.model})")
            decision_id = uuid.uuid4()
            verdict = "ask" if r.verdict is None else r.verdict.lower()
            # Stop rule: the same issue twice in a row gets one firm prompt fix, then goes to the owner (not another re-roll).
            stop = stop_rule.check(self.db, "video_id", video_id, stage, verdict, r.items, r.summary, r.redo_notes)
            self.db.execute(
                "INSERT INTO producer_decision (id, video_id, stage, attempt, verdict, confidence, summary, redo_notes, items_json, model, cost_inr, "
                "stop_rule, stop_cause) VALUES (%(id)s, %(v)s, %(s)s, %(a)s, %(verdict)s, %(c)s, %(sum)s, %(notes)s, CAST(%(items)s AS jsonb), %(m)s, "
                "%(cost)s, %(stop)s, %(cause)s)",
                {"id": decision_id, "v": video_id, "s": stage, "a": attempt, "verdict": verdict,
                 "c": Decimal(str(r.confidence)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP), "sum": r.summary, "notes": stop.redo_notes,
                 "items": json.dumps(to_json(r.items or []), separators=(",", ":")), "m": r.model, "cost": cost,
                 "stop": stop_rule_column(stop), "cause": None if stop.cause is None else stop.cause.key})
        weak = sum(1 for i in (r.items or []) if not i.ok)
        what = {"approve": "recommends APPROVE", "redo": f"recommends REDO of {weak} item{'' if weak == 1 else 's'}"}.get(verdict, "recommends you DECIDE (unsure)")
        text = (f"AI Producer {what} ({fixed(r.confidence * 100, 0)}% sure): {'' if r.summary is None else r.summary}"
                f"{'' if stop.note is None else ' ' + stop.note}"
                f"{'' if stop.redo_notes is None or not stop.redo_notes.strip() else ' Suggested redo: ' + stop.redo_notes}")
        weak_items = [i.id for i in (r.items or []) if not i.ok]
        return ProducerAdvice(stop.verdict, r.confidence, text, stop.redo_notes, str(decision_id), weak_items)

    # ------------------------------------------------------------------------------------------- P2: acting

    def trust(self) -> str:
        """The owner's trust switch: auto (decide once agreement is high enough), advise, or decide."""
        rows = self.db.rows("SELECT value FROM producer_setting WHERE key = 'trust'")
        return rows[0]["value"] if rows else "auto"

    def set_trust(self, trust: str) -> None:
        if trust not in ("auto", "advise", "decide"):
            raise ValueError("trust must be auto, advise or decide")
        self.db.execute("INSERT INTO producer_setting (key, value) VALUES ('trust', %(t)s) ON CONFLICT (key) DO UPDATE SET value = %(t)s, updated_at = now()",
                        {"t": trust})

    def deciding(self) -> bool:
        """May the Producer act on its own right now? decide: yes; advise: no; auto: once it agrees with the owner enough."""
        t = self.trust()
        if t == "decide":
            return True
        if t == "advise":
            return False
        a = self.agreement()
        return a["decided"] >= self.d.pricing.producer_setting("trustAfterDecisions") and a["rate"] >= self.d.pricing.producer_setting("trustAgreementPct")

    def mark_acted(self, decision_id: uuid.UUID | str, acted: str) -> None:
        """Records what the Producer did by itself: approved | redo | asked."""
        self.db.execute("UPDATE producer_decision SET acted = %(a)s WHERE id = %(id)s", {"a": acted, "id": uuid.UUID(str(decision_id))})

    def _video_spent(self, video_id: uuid.UUID) -> Decimal:
        """Everything this video has cost so far (the Producer budget counts the whole video)."""
        return Decimal(self.db.rows("SELECT COALESCE(sum(amount_inr), 0) AS s FROM cost_entry WHERE video_id = %(v)s", {"v": video_id})[0]["s"])

    def may_redo(self, video_id: uuid.UUID, round_: int) -> bool:
        """A redo is allowed while the stage has rounds left and the video is inside the Producer budget."""
        if round_ > self.d.pricing.producer_setting("roundsPerStage"):
            return False
        return self._video_spent(video_id) < self.d.pricing.producer_budget_inr(self.plan(video_id).durationSec <= 180)

    def shot_ids_for(self, video_id: uuid.UUID, item_ids: list[str]) -> list[str]:
        """Shot ids for the weak items of a shot stage ("shot-N" = the shot at position N)."""
        ords = [int(i[5:]) - 1 for i in item_ids if i.startswith("shot-")]
        if not ords:
            return []
        return [r["id"] for r in self.db.rows(
            "SELECT s.id::text AS id FROM shot s JOIN scene sc ON sc.id = s.scene_id WHERE sc.video_id = %(v)s AND s.ord = ANY(%(ords)s) ORDER BY s.ord",
            {"v": video_id, "ords": ords})]

    def spend_decision(self, video_id: uuid.UUID, stage: str, estimate_inr: Decimal) -> SpendDecision:
        """P2: the Producer's own call on a money step, recorded so it shows in the report."""
        text = self.spend_advice(video_id, estimate_inr)
        approve = self.deciding() and "would APPROVE" in text
        self.db.execute(
            "INSERT INTO producer_decision (id, video_id, stage, attempt, verdict, confidence, summary, acted, cost_inr) "
            "VALUES (%(id)s, %(v)s, %(s)s, 0, %(verdict)s, 1, %(sum)s, %(acted)s, 0)",
            {"id": uuid.uuid4(), "v": video_id, "s": stage, "verdict": "approve" if approve else "ask", "sum": text,
             "acted": "approved_spend" if approve else "asked"})
        return SpendDecision(approve, text.replace("would APPROVE this", "APPROVED this by itself") if approve else text)

    def spend_advice(self, video_id: uuid.UUID, estimate_inr: Decimal) -> str:
        """P1 advice on a money step (no AI call): within the per-video Producer budget and below the "ask me above" line, the Producer would
        approve it; otherwise it would ask the owner. Advise-only: the owner still approves."""
        pricing = self.d.pricing
        budget = pricing.producer_budget_inr(self.plan(video_id).durationSec <= 180)
        ask_above = pricing.producer_ask_above_inr()
        spent = self._video_spent(video_id)
        estimate_inr = Decimal(estimate_inr)
        fits = estimate_inr <= ask_above and spent + estimate_inr <= budget
        if fits:
            return (f"AI Producer: would APPROVE this (≈₹{_r0(estimate_inr)}, under your ₹{_r0(ask_above)} 'ask me' line; "
                    f"₹{_r0(spent)} of the ₹{_r0(budget)} video budget used).")
        return f"AI Producer: would ASK YOU (≈₹{_r0(estimate_inr)}; 'ask me above' ₹{_r0(ask_above)}; ₹{_r0(spent)} of the ₹{_r0(budget)} video budget used)."

    # ------------------------------------------------------------------------------------------- P2: learning

    def learn(self, video_id: uuid.UUID, stage: str) -> list[str]:
        """Learns from the owner's notes: distils the owner's recent feedback on a stage (and where they disagreed with the Producer) into at most
        6 short rules, replacing the stage's earlier learned rules. Owner-written rules are kept."""
        if self.llm is None:
            return []
        notes = [r["t"] for r in self.db.rows(
            "SELECT decision || ': ' || notes AS t FROM approval WHERE stage = %(s)s AND actor = 'owner' AND notes IS NOT NULL AND btrim(notes) <> '' "
            "ORDER BY ts DESC LIMIT 15", {"s": stage})]
        disagreements = [r["t"] for r in self.db.rows(
            "SELECT 'Producer said ' || verdict || ' (' || COALESCE(summary, '') || '); owner chose ' || owner_decision "
            "|| COALESCE(' with notes: ' || owner_notes, '') AS t FROM producer_decision WHERE stage = %(s)s AND agreed = false "
            "ORDER BY decided_at DESC LIMIT 8", {"s": stage})]
        if not notes and not disagreements:
            return []
        owner = [r["rule_text"] for r in self.db.rows("SELECT rule_text FROM producer_rule WHERE stage = %(s)s AND source = 'owner' AND enabled", {"s": stage})]
        system = """You keep the taste rules of a video studio's AI producer. From the owner's feedback, write at most 6 short,
concrete rules (max 15 words each) that capture what the owner consistently wants or rejects for this
stage. Only rules the feedback supports; no duplicates of the owner's own rules. Return JSON {"rules": [...]}."""
        user = (f"Stage: {stage}\nOwner's own rules: [{', '.join(owner)}]\nOwner feedback (newest first):\n- " + "\n- ".join(notes)
                + "\nDisagreements with the Producer:\n- " + "\n- ".join(disagreements))
        out = self.llm.call(video_id, f"PRODUCER learn rules {stage}", system, user, LearnedRules)
        rules = [r for r in (out.rules or [] if out is not None else []) if r is not None and r.strip()][:6]
        with self.db.transaction():
            self.db.execute("DELETE FROM producer_rule WHERE stage = %(s)s AND source = 'learned'", {"s": stage})
            for r in rules:
                self.db.execute("INSERT INTO producer_rule (id, stage, rule_text, source, evidence) VALUES (%(id)s, %(s)s, %(t)s, 'learned', %(e)s)",
                                {"id": uuid.uuid4(), "s": stage, "t": r.strip(), "e": len(notes)})
        return rules

    def rules_list(self) -> list[dict[str, Any]]:
        return self.db.rows("SELECT id, stage, rule_text, source, enabled, evidence, updated_at FROM producer_rule ORDER BY stage, source DESC, evidence DESC")

    def add_rule(self, stage: str | None, text: str) -> None:
        self.db.execute("INSERT INTO producer_rule (id, stage, rule_text, source) VALUES (%(id)s, %(s)s, %(t)s, 'owner')",
                        {"id": uuid.uuid4(), "s": None if stage is None or not stage.strip() else stage, "t": text.strip()})

    def update_rule(self, rule_id: uuid.UUID, text: str | None, enabled: bool | None) -> None:
        self.db.execute(
            "UPDATE producer_rule SET rule_text = COALESCE(CAST(%(t)s AS text), rule_text), enabled = COALESCE(CAST(%(e)s AS boolean), enabled), "
            "source = CASE WHEN CAST(%(t)s AS text) IS NULL THEN source ELSE 'owner' END, updated_at = now() WHERE id = %(id)s",
            {"t": text, "e": enabled, "id": rule_id})

    def delete_rule(self, rule_id: uuid.UUID) -> None:
        self.db.execute("DELETE FROM producer_rule WHERE id = %(id)s", {"id": rule_id})

    def video_summary(self, video_id: uuid.UUID) -> dict[str, Any]:
        """End-of-video summary: what the Producer did by itself, and what it spent on that video."""
        m = self.db.rows(
            "SELECT count(*) FILTER (WHERE acted = 'approved') AS approved, count(*) FILTER (WHERE acted = 'redo') AS redone, "
            "count(*) FILTER (WHERE acted = 'asked') AS asked, count(*) FILTER (WHERE acted = 'approved_spend') AS spend_approved, "
            "COALESCE(sum(cost_inr), 0) AS review_cost FROM producer_decision WHERE video_id = %(v)s", {"v": video_id})[0]
        return {**m, "spentInr": self._video_spent(video_id), "budgetInr": self.d.pricing.producer_budget_inr(self.plan(video_id).durationSec <= 180),
                "trust": self.trust(), "deciding": self.deciding()}

    def record_owner_decision(self, video_id: uuid.UUID, stage: str, decision: str, notes: str | None) -> None:
        """The owner decided on a stage the Producer reviewed: keep the decision and notes with its latest open review (a lesson) and whether
        they agreed (approve = approve; reject = it had recommended a redo)."""
        self.db.execute(
            "UPDATE producer_decision SET owner_decision = %(d)s, owner_notes = %(n)s, decided_at = now(), "
            "agreed = CASE WHEN %(d)s = 'approve' THEN verdict = 'approve' ELSE verdict = 'redo' END "
            "WHERE id = (SELECT id FROM producer_decision WHERE video_id = %(v)s AND stage = %(s)s AND owner_decision IS NULL "
            "ORDER BY created_at DESC LIMIT 1)", {"d": decision, "n": notes, "v": video_id, "s": stage})

    # ------------------------------------------------------------------------------------------- read model

    def decisions(self, video_id: uuid.UUID) -> list[dict[str, Any]]:
        """Every Producer review of a video, newest first, with the owner's decision and agreement."""
        return self.db.rows(
            "SELECT id, stage, attempt, verdict, confidence, summary, redo_notes, items_json::text AS items, model, cost_inr, owner_decision, "
            "owner_notes, agreed, created_at, decided_at FROM producer_decision WHERE video_id = %(v)s ORDER BY created_at DESC", {"v": video_id})

    def agreement(self) -> dict[str, Any]:
        """How often the Producer's recommendation matched the owner's decision (all videos)."""
        m = self.db.rows("SELECT count(*) FILTER (WHERE agreed IS NOT NULL) AS decided, count(*) FILTER (WHERE agreed) AS agreed FROM producer_decision")[0]
        decided, agreed = int(m["decided"]), int(m["agreed"])
        return {"decided": decided, "agreed": agreed, "rate": 0 if decided == 0 else jround(agreed * 100.0 / decided)}
