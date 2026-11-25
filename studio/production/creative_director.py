"""The Creative Director: checks that the pictures tell the story, at three levels (each shot against the meaning of its line, each
paragraph/act as a sequence, the whole video as an arc). Port of CreativeDirector.

Runs twice before any video money is spent:
- after the SHOT_PLAN (text only): weak shot prompts are rewritten on the spot (free); a paragraph or whole-video problem sends the
  shot plan back once with the reasons;
- after the KEYFRAMES (a vision model looks at every image with its line, paragraph and neighbours): weak images get a better prompt
  and only those shots are redone; still weak after that = the owner decides.
Its findings flow through the Inspector, so FAIL handling, storage and the dashboard work the same way.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from studio.agents.creative_director import RUBRIC
from studio.agents.jfmt import fixed
from studio.agents.types import CreativeReview, ShotCritique
from studio.ledger.budget_guard import GEMINI
from studio.media.types import Finding, ImageReviewRequest, ImageReviewResult, ReviewLine, ReviewShot
from studio.planner.imported import load as load_imported
from studio.production.records import StoredLine, StoredScript
from studio.production.support import ProductionDeps, StageSupport
from studio.workflow.stage import Stage

FAIL_AT = 2  # scores at or below this are a FAIL; one above is a WARN
WARN_AT = 3


def _blank(s: str | None) -> bool:
    return s is None or not s.strip()


def _text(first: str | None, fallback: str | None) -> str:
    return (fallback if not _blank(fallback) else "") if _blank(first) else first


def _worst(c: ShotCritique) -> int:
    return min(c.meaning, c.accuracy, c.continuity)


def _needs_fix(c: ShotCritique) -> bool:
    return _worst(c) <= WARN_AT or c.writing


def _id(shot: dict[str, Any]) -> str:
    return str(shot["id"])


def _n(shot: dict[str, Any]) -> int:
    return int(shot["ord"]) + 1


def by_number(shots: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {_n(s): s for s in shots}


def _int_list(xs: list[int]) -> str:
    return "[" + ", ".join(str(x) for x in xs) + "]"  # Java List.toString


def findings(review: CreativeReview, by_n: dict[int, dict[str, Any]], image_stage: bool, rewritten: set[int]) -> list[Finding]:
    """image_stage: True for the image review: shot problems become targeted FAILs; paragraph problems target the shots named to fix
    them (a whole-video problem cannot be fixed by redoing images, so it warns). rewritten: shot numbers whose prompts were already
    improved (shot-plan review), reported as INFO."""
    out: list[Finding] = []
    v = review.video
    if v is not None:
        worst = min(v.arc, v.hook, v.ending, v.variety)
        scores = f"arc {v.arc}/5, hook {v.hook}/5, ending {v.ending}/5, variety {v.variety}/5"
        if worst <= WARN_AT:
            sev = "FAIL" if worst <= FAIL_AT and not image_stage else "WARN"
            out.append(Finding(sev, "CD_VIDEO", f"{scores}: {_text(v.problem, v.summary)}", "whole video"))
    for a in review.acts or []:
        worst = min(a.flow, a.keyMoment, a.continuity, a.mood)
        if worst > WARN_AT:
            continue
        ref = f"paragraph “{'?' if _blank(a.act) else a.act}”"
        msg = f"flow {a.flow}/5, key moment {a.keyMoment}/5, continuity {a.continuity}/5, mood {a.mood}/5: {_text(a.problem, a.summary)}"
        fix = [n for n in (a.shotsToFix or []) if n in by_n]
        if worst > FAIL_AT:
            out.append(Finding("WARN", "CD_PARAGRAPH", msg, ref))
        elif image_stage and fix:
            out.extend(Finding("FAIL", "CD_PARAGRAPH", msg, f"shot {n}", _id(by_n[n])) for n in fix)
        elif not image_stage and fix and all(n in rewritten for n in fix):
            out.append(Finding("INFO", "CD_PARAGRAPH_FIXED", f"{msg} (fixed by rewriting shots {_int_list(fix)})", ref))
        else:
            out.append(Finding("WARN" if image_stage else "FAIL", "CD_PARAGRAPH", msg, ref))
    for c in review.shots or []:
        shot = by_n.get(c.shot)
        if shot is None:
            continue
        worst = _worst(c)
        literal = c.literal and c.meaning <= WARN_AT
        must = literal or c.writing  # garbled generated lettering is never acceptable output, whatever the other scores
        if worst > WARN_AT and not must:
            continue
        ref = f"shot {c.shot}"
        msg = (f"meaning {c.meaning}/5, accuracy {c.accuracy}/5, continuity {c.continuity}/5"
               + (", shows one word instead of the idea" if c.literal else "")
               + (", has generated lettering (it comes out garbled; on-screen words belong in the edit)" if c.writing else "")
               + f": {_text(c.problem, 'weak')}")
        has_fix = not _blank(c.fixPrompt)
        if c.shot in rewritten:
            out.append(Finding("INFO", "CD_PROMPT_IMPROVED", msg + " Prompt rewritten.", ref, _id(shot)))
        elif worst <= FAIL_AT or must:
            sev = "FAIL" if image_stage and has_fix else "WARN" if image_stage else "FAIL"
            out.append(Finding(sev, "CD_SHOT", msg, ref, _id(shot) if image_stage and has_fix else None))
        else:
            out.append(Finding("WARN", "CD_SHOT", msg, ref))
    return out


class CreativeDirector(StageSupport):
    def __init__(self, d: ProductionDeps, agent, vision_model: str):
        super().__init__(d)
        self._agent, self._vision_model = agent, vision_model

    # ---- shot plan (text)

    def review_shot_plan(self, video_id: uuid.UUID, attempt: int) -> list[Finding]:
        """Reviews the shot plan, rewrites weak shot prompts in place, stores the report card, returns findings."""
        shots = self.shots(video_id)
        if not shots or load_imported(self.d.db, video_id) is not None:
            return []  # a video made from a plan file: the plan is the owner's, the review would spend and rewrite its prompts
        review = self._agent.review_shot_plan(video_id, self.context(video_id, shots))
        self._store(video_id, Stage.SHOT_PLAN, attempt, self.d.text_model.split(",")[0].strip(), review)
        by_n = by_number(shots)
        rewritten: set[int] = set()
        for c in review.shots or []:
            shot = by_n.get(c.shot)
            if shot is not None and _needs_fix(c) and not _blank(c.fixPrompt) and self._rewrite_prompt(shot, c.fixPrompt, False):
                rewritten.add(c.shot)
        return findings(review, by_n, False, rewritten)

    # ---- images (vision)

    def image_review_request(self, video_id: uuid.UUID) -> ImageReviewRequest:
        """What the media worker's vision review needs: every image with its line, paragraph and timing."""
        self.d.budget.require(Decimal(5), "CREATIVE_REVIEW images", video_id)  # ~25 small images + text: well under ₹5 on Gemini Flash
        plan = self.plan(video_id)
        preset = self.d.presets.by_id_or_default(plan.stylePreset)
        concept = self.latest_concept(video_id)
        script = self.latest_script(video_id)
        lines = self._lines(script)
        starts = (script.line_starts if script and script.line_starts else {})
        review_lines = [ReviewLine(line.index, line.act, line.text, starts.get(str(line.index), 0.0)) for line in lines.values()]
        review_shots = []
        for s in self.shots(video_id):
            if s.get("keyframe_uri") is None:
                continue
            p = self._prompt(s)
            li = int(p["lineIndex"]) if isinstance(p.get("lineIndex"), (int, float)) and not isinstance(p.get("lineIndex"), bool) else None
            start = p.get("startSec")
            dur = s.get("duration_target_sec")
            review_shots.append(ReviewShot(
                _n(s), _id(s), li, lines[li].act if li is not None and li in lines else None,
                float(start) if isinstance(start, (int, float)) else 0.0, float(dur) if isinstance(dur, (int, float)) else 0.0,
                s.get("description"), p.get("keyframePrompt"), s.get("priority"), s["keyframe_uri"]))
        return ImageReviewRequest(str(video_id), self._vision_model, RUBRIC, plan.title, plan.aspectRatio, preset.visual_style,
                                  None if concept is None else concept.logline, None if concept is None else concept.bigIdea,
                                  None if concept is None else concept.emotionalArc, None if script is None else script.music_brief,
                                  review_lines, review_shots)

    def save_image_review(self, video_id: uuid.UUID, attempt: int, r: ImageReviewResult | None) -> list[Finding]:
        """Records the review's cost, stores the report card, keeps suggested prompts on weak shots, returns findings."""
        if r is None or r.review is None:
            return [Finding("WARN", "CREATIVE_REVIEW_UNAVAILABLE", "The Creative Director could not review the images this time.", "images")]
        with self.db.transaction():
            if r.usage is not None and r.usage.total_tokens > 0:
                self.d.ledger.record(video_id, GEMINI, self.d.pricing.text_inr(r.usage), Decimal(r.usage.total_tokens), "tokens", True,
                                     f"CREATIVE_REVIEW images ({r.model}, {r.images_sent} images)")
            self._store(video_id, Stage.KEYFRAMES, attempt, r.model, r.review)
            by_n = by_number(self.shots(video_id))
            for c in r.review.shots or []:
                shot = by_n.get(c.shot)
                if shot is not None and _needs_fix(c) and not _blank(c.fixPrompt):
                    self._rewrite_prompt(shot, c.fixPrompt, True)  # kept as a suggestion until the shot is redone
        return findings(r.review, by_n, True, set())

    def apply_suggested_prompts(self, video_id: uuid.UUID, shot_ids: list[uuid.UUID]) -> None:
        """Before a targeted redo: the suggested prompts become the shots' prompts (owner-edited prompts are kept)."""
        ids = {str(i) for i in shot_ids}
        with self.db.transaction():
            for s in self.shots(video_id):
                if _id(s) not in ids:
                    continue
                p = self._prompt(s)
                suggested = p.pop("suggestedPrompt", None)
                if isinstance(suggested, str) and suggested.strip() and p.get("editedByOwner") is not True:
                    p.setdefault("originalPrompt", p.get("keyframePrompt"))
                    p["keyframePrompt"] = suggested
                    p["revisedBy"] = "creative_director"
                    self._save_prompt(s, p)

    def reports(self, video_id: uuid.UUID) -> list[dict[str, Any]]:
        """Latest report card per reviewed stage, for the dashboard."""
        rows = self.db.rows("SELECT DISTINCT ON (stage) stage, attempt, model, review_json::text AS review, created_at FROM creative_review "
                            "WHERE video_id = %(v)s ORDER BY stage, attempt DESC", {"v": video_id})
        return [{**r, "review": json.loads(r["review"])} for r in rows]

    # ---- context and helpers

    def context(self, video_id: uuid.UUID, shots: list[dict[str, Any]]) -> str:
        """Everything a reviewer needs to judge meaning in context: concept, paragraphs, timed lines and every shot."""
        plan = self.plan(video_id)
        preset = self.d.presets.by_id_or_default(plan.stylePreset)
        concept = self.latest_concept(video_id)
        script = self.latest_script(video_id)
        lines = self._lines(script)
        starts = (script.line_starts if script and script.line_starts else {})
        b = [f"Video: {plan.title} ({plan.format}, {plan.durationSec}s, {plan.aspectRatio}, style: {preset.name} - {preset.visual_style})\n"]
        if concept is not None:
            b.append(f"Logline: {concept.logline}\nBig idea: {concept.bigIdea}\nEmotional arc: {concept.emotionalArc}\n")
            for act in concept.acts or []:
                b.append(f"Act “{act.title}”: {act.purpose}\n")
        if script is not None and not _blank(script.music_brief):
            b.append(f"Music: {script.music_brief}\n")
        b.append("\nNarration by paragraph (line | starts at | text):\n")
        act = None
        for line in lines.values():
            if act != line.act:
                act = line.act
                b.append(f"## {'(no act)' if act is None else act}\n")
            b.append(f"{line.index} | {fixed(starts.get(str(line.index), 0.0), 1)}s | {'' if line.spoken else '[visual beat] '}{line.text}\n")
        b.append("\nShots in order (shot | line | starts | seconds | priority | type, move | what we see | image prompt):\n")
        for s in shots:
            p = self._prompt(s)
            li = "-" if p.get("lineIndex") is None else p["lineIndex"]
            b.append(f"{_n(s)} | {li} | {_java(p.get('startSec'))} | {_java(s.get('duration_target_sec'))} | {_java(s.get('priority'))} | "
                     f"{_java(s.get('shot_type'))}, {_java(s.get('camera_move'))} | {_java(s.get('description'))} | {_java(p.get('keyframePrompt'))}\n")
        return "".join(b)

    def _store(self, video_id: uuid.UUID, stage: Stage, attempt: int, model: str | None, review: CreativeReview) -> None:
        self.db.execute(
            """
            INSERT INTO creative_review (video_id, stage, attempt, model, review_json)
            VALUES (%(v)s, %(s)s, %(a)s, %(m)s, CAST(%(r)s AS jsonb))
            ON CONFLICT (video_id, stage, attempt) DO UPDATE SET model = EXCLUDED.model, review_json = EXCLUDED.review_json, created_at = now()
            """,
            {"v": video_id, "s": stage.name, "a": attempt, "m": model, "r": json.dumps(review.model_dump())},
        )

    def _rewrite_prompt(self, shot: dict[str, Any], fix: str, suggestion_only: bool) -> bool:
        """Writes a better prompt onto a shot (or keeps it as a suggestion). Never overrides the owner's own prompt."""
        p = self._prompt(shot)
        if p.get("editedByOwner") is True:
            return False
        if suggestion_only:
            p["suggestedPrompt"] = fix.strip()
        else:
            p.setdefault("originalPrompt", p.get("keyframePrompt"))
            p["keyframePrompt"] = fix.strip()
            p["revisedBy"] = "creative_director"
        self._save_prompt(shot, p)
        return True

    def _save_prompt(self, shot: dict[str, Any], p: dict[str, Any]) -> None:
        self.db.execute("UPDATE shot SET prompt_json = CAST(%(p)s AS jsonb), updated_at = now() WHERE id = %(s)s",
                        {"p": json.dumps(p, separators=(",", ":")), "s": shot["id"]})

    @staticmethod
    def _prompt(shot: dict[str, Any]) -> dict[str, Any]:
        return json.loads(shot["prompt_json"])

    @staticmethod
    def _lines(script: StoredScript | None) -> dict[int, StoredLine]:
        return {} if script is None else {line.index: line for line in script.lines}


def _java(o: Any) -> str:
    """String.valueOf / StringBuilder.append for the values shown in the review context (null prints as 'null')."""
    return "null" if o is None else str(o)
