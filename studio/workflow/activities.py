"""Control activities of the Python control plane (port of VideoActivitiesImpl and ProducerActivitiesImpl). All database and LLM I/O happens
here, never in workflow code. Every method takes and returns JSON-compatible values (see codec) and must be idempotent: Temporal may retry
any call. `register()` exposes each method as a Temporal activity named `control.<method>` (`producer.<method>` for the Producer's)."""

from __future__ import annotations

import functools
import json
import logging
import re
import uuid
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from studio.api import plan_preview
from studio.media.types import (
    AnimaticResult,
    AssemblyResult,
    ClipRequest,
    ClipResult,
    FootageFetchRequest,
    FootageFetchResult,
    FootageSearchResponse,
    ImageReviewResult,
    KeyframeRequest,
    KeyframeResult,
    MediaInspectResult,
    MusicResult,
    NarrationResult,
    ProducerReviewResult,
    ResearchRequest,
    ResearchResult,
    SmoothRequest,
    SmoothResult,
    StockRequest,
    StockResult,
)
from studio.media.wire import from_json, to_json
from studio.production.records import ImagePolicy
from studio.production.redo_policy import redo_is_paid, redo_what
from studio.video.clip_progress import ClipProgress
from studio.workflow.codec import inspection_report_json, stage_result_json
from studio.workflow.stage import Stage, StageMode
from studio.workflow.types import StageResult

log = logging.getLogger(__name__)


def _id(s: str) -> uuid.UUID:
    return uuid.UUID(s)


def _ids(shot_ids: list[str] | None) -> list[uuid.UUID] | None:
    return None if shot_ids is None else [uuid.UUID(s) for s in shot_ids]


def _dropped_note(dropped: list[str]) -> str:
    """What the media worker's result left out (optional fields, defaulted by the tolerant parser): named once each, without shot indexes."""
    names = sorted({re.sub(r"\d+\.", "", d) for d in dropped})
    log.warning("The media result lacked optional field(s) %s; defaults were used", ", ".join(names))
    return f" (the worker's result lacked {', '.join(names)}: defaults used)"


def _compact(o: Any) -> str:
    return json.dumps(o, separators=(",", ":"))


class ControlActivities:
    """Everything the workflows ask the control plane to do. Collaborators are passed in so tests can fake any of them."""

    def __init__(self, db: Any, planner: Any, *, researcher: Any, inspector: Any, creative: Any, research: Any, story: Any, music: Any, voice: Any,
                 shots: Any, stock: Any, animatic: Any, video: Any, footage: Any, assembly: Any, finish: Any, sharpen: Any,
                 clip_progress: ClipProgress | None = None, moon: Any = None, interpolate: Any = None):
        self.db, self.planner, self.researcher = db, planner, researcher
        self.inspector, self.creative = inspector, creative
        self.research_stage, self.story, self.music, self.voice = research, story, music, voice
        self.shots, self.stock_stage, self.animatic = shots, stock, animatic
        self.video, self.footage, self.assembly, self.finish, self.sharpen = video, footage, assembly, finish, sharpen
        self.clip_progress = clip_progress or ClipProgress()
        self.moon = moon
        self.interpolate = interpolate

    # ---- plan and placeholder stages

    def plan(self, video_id: str, prompt: str, owner_notes: str | None, attempt: int) -> dict[str, Any]:
        outcome = self.planner.plan(_id(video_id), prompt, owner_notes)
        artifact: dict[str, Any] = {"plan": outcome.plan.model_dump(), "stages": [s.name for s in outcome.stages], "source": outcome.source}
        if outcome.fallback_reason is not None:
            artifact["fallbackReason"] = outcome.fallback_reason
        artifact_json = _compact(artifact)
        # A video created from a plan file keeps that file under "imported" (the writer and director read it later).
        self.db.execute("UPDATE video SET plan_json = CAST(%(plan)s AS jsonb) || jsonb_strip_nulls(jsonb_build_object('imported', plan_json -> 'imported')), "
                        "title = %(title)s, updated_at = now() WHERE id = %(id)s",
                        {"plan": artifact_json, "title": outcome.plan.title, "id": _id(video_id)})
        # The PLAN cost is booked once, by Llm, when the model answers.
        p = outcome.plan
        summary = (f"{p.title}: {p.format}, {p.durationSec}s, {p.aspectRatio}, preset {p.stylePreset}, narration {p.narration}, music {p.music}, "
                   f"research {'yes' if p.needsResearch else 'no'} (planned by {outcome.source})")
        return stage_result_json(StageResult(summary, artifact_json, outcome.stages))

    def run_stage(self, video_id: str, stage: str, owner_notes: str | None, attempt: int) -> dict[str, Any]:
        """Placeholder for stages that have no real work yet (Publish and similar): the backbone (modes, approvals, restarts) is real."""
        st = Stage[stage]
        artifact: dict[str, Any] = {"placeholder": True, "stage": st.name, "willDo": st.description}
        if owner_notes is not None and owner_notes.strip():
            artifact["ownerNotesApplied"] = owner_notes
        summary = (f"Placeholder (real work arrives in M2+): {st.description}."
                   + ("" if owner_notes is None or not owner_notes.strip() else f' Notes received: "{owner_notes}".'))
        return stage_result_json(StageResult.of(summary, _compact(artifact)))

    # ---- research -> concept -> script -> music -> narration

    def research_request(self, video_id: str, owner_notes: str | None) -> dict[str, Any]:
        return to_json(self.research_stage.research_request(_id(video_id), owner_notes))

    def claude_research(self, video_id: str, request: dict[str, Any]) -> dict[str, Any]:
        return to_json(self.researcher.research(_id(video_id), from_json(ResearchRequest, request)))

    def save_research(self, video_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return stage_result_json(self.research_stage.save_research(_id(video_id), from_json(ResearchResult, result)))

    def concept(self, video_id: str, owner_notes: str | None) -> dict[str, Any]:
        return stage_result_json(self.story.concept(_id(video_id), owner_notes))

    def script(self, video_id: str, owner_notes: str | None) -> dict[str, Any]:
        return stage_result_json(self.story.script(_id(video_id), owner_notes))

    def music_request(self, video_id: str, owner_notes: str | None) -> dict[str, Any]:
        return to_json(self.music.music_request(_id(video_id), owner_notes))

    def save_music(self, video_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return stage_result_json(self.music.save_music(_id(video_id), from_json(MusicResult, result)))

    def narration_request(self, video_id: str) -> dict[str, Any]:
        return to_json(self.voice.narration_request(_id(video_id)))

    def save_narration(self, video_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return stage_result_json(self.voice.save_narration(_id(video_id), from_json(NarrationResult, result)))

    def last_attempt(self, video_id: str, stage: str) -> int:
        """Highest attempt recorded for a stage (0 if none). Used by re-runs so attempt numbers keep increasing."""
        return int(self.db.rows("SELECT COALESCE(max(attempt), 0) AS n FROM stage_run WHERE video_id = %(v)s AND stage = %(s)s",
                                {"v": _id(video_id), "s": stage})[0]["n"])

    # ---- shot plan, keyframes, per-shot redo

    def shot_plan(self, video_id: str, owner_notes: str | None) -> dict[str, Any]:
        return stage_result_json(self.shots.shot_plan(_id(video_id), owner_notes))

    def keyframe_request(self, video_id: str, owner_notes: str | None, one_pod: bool = False) -> dict[str, Any]:
        return to_json(self.shots.keyframe_request(_id(video_id), owner_notes, one_pod))

    def save_keyframes(self, video_id: str, request: dict[str, Any], result: dict[str, Any], keep_pod: bool = False) -> dict[str, Any]:
        """keep_pod (one-pod mode, the next stages are all AUTO): the pod stays for the clips stage once the keyframes are saved. Whatever else
        happens (a failed save, the pod gone, nothing to keep) it is released here, exactly as without the flag."""
        req = from_json(KeyframeRequest, request)
        kept = False
        try:
            saved = stage_result_json(self.shots.save_keyframes(_id(video_id), req, from_json(KeyframeResult, result)))
            if keep_pod and req.gpu_url is not None:
                kept = self.shots.keep_gpu(_id(video_id))
            return saved
        finally:
            if req.gpu_url is not None and not kept:
                self.shots.release_gpu(_id(video_id), "keyframes saved")

    # ---- one-pod mode (VIDEO_ONE_POD): the workflow asks, the control plane keeps or lets go

    def one_pod_enabled(self) -> bool:
        return bool(self.shots.one_pod_enabled())

    def touch_held_pod(self, video_id: str) -> bool:
        return bool(self.shots.touch_gpu(_id(video_id)))

    def release_held_pod(self, video_id: str, why: str) -> None:
        """Idempotent: ends this video's share of the pod (and the pod when nobody else uses it) before a human wait, after a failure or cancel,
        and when the clips stage is over."""
        self.shots.release_gpu(_id(video_id), why)

    def shot_keyframe_request(self, video_id: str, shot_ids: list[str], owner_notes: str | None, policy: str | None) -> dict[str, Any]:
        return to_json(self.shots.shot_keyframe_request(_id(video_id), _ids(shot_ids), owner_notes, ImagePolicy["AUTO" if policy is None else policy]))

    def mark_shots(self, video_id: str, shot_ids: list[str], status: str | None, error: str | None) -> None:
        self.shots.mark_shots(_id(video_id), _ids(shot_ids), status, error)

    # ---- per-line voice redo

    def line_narration_request(self, video_id: str, line_indexes: list[int], style_notes: str | None) -> dict[str, Any]:
        return to_json(self.voice.line_narration_request(_id(video_id), list(line_indexes), style_notes))

    def save_line_narration(self, video_id: str, result: dict[str, Any], style: str | None) -> dict[str, Any]:
        return stage_result_json(self.voice.save_line_narration(_id(video_id), from_json(NarrationResult, result), style))

    def mark_lines(self, video_id: str, line_indexes: list[int], status: str | None, why: str | None) -> None:
        self.voice.mark_lines(_id(video_id), list(line_indexes), status, why)

    def animatic_request(self, video_id: str) -> dict[str, Any]:
        return to_json(self.animatic.animatic_request(_id(video_id)))

    def save_animatic(self, video_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return stage_result_json(self.animatic.save_animatic(_id(video_id), from_json(AnimaticResult, result)))

    # ---- Inspector and Creative Director

    def inspection_request(self, video_id: str, stage: str) -> dict[str, Any] | None:
        r = self.inspector.media_request(_id(video_id), Stage[stage])
        return None if r is None else to_json(r)

    def save_inspection(self, video_id: str, stage: str, attempt: int, media: dict[str, Any] | None) -> dict[str, Any]:
        m = None if media is None else from_json(MediaInspectResult, media)
        return inspection_report_json(self.inspector.evaluate(_id(video_id), Stage[stage], attempt, m))

    def creative_shot_plan_review(self, video_id: str, attempt: int) -> list[dict[str, Any]]:
        return to_json(self.creative.review_shot_plan(_id(video_id), attempt))

    def image_review_request(self, video_id: str) -> dict[str, Any]:
        return to_json(self.creative.image_review_request(_id(video_id)))

    def save_image_review(self, video_id: str, attempt: int, result: dict[str, Any]) -> list[dict[str, Any]]:
        return to_json(self.creative.save_image_review(_id(video_id), attempt, from_json(ImageReviewResult, result)))

    def fix_keyframe_request(self, video_id: str, shot_ids: list[str], owner_notes: str | None) -> dict[str, Any]:
        """Targeted redo of weak images: applies the Creative Director's suggested prompts, then builds the request."""
        ids = _ids(shot_ids)
        self.creative.apply_suggested_prompts(_id(video_id), ids)
        return to_json(self.shots.shot_keyframe_request(_id(video_id), ids, owner_notes, ImagePolicy.AUTO))

    # ---- moving video per shot

    def clip_estimate(self, video_id: str, kind: str, shot_ids: list[str] | None) -> dict[str, Any]:
        return to_json(self.video.estimate(_id(video_id), kind, _ids(shot_ids)))

    def clip_request(self, video_id: str, kind: str, shot_ids: list[str] | None) -> dict[str, Any]:
        request = self.video.clip_request(_id(video_id), kind, _ids(shot_ids))
        self.clip_progress.start_batch(_id(video_id))
        self.video.mark_clips(_id(video_id), [_id(j.shot_id) for j in request.jobs], "RENDERING", None)
        return to_json(request)

    def save_clips(self, video_id: str, request: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        req = from_json(ClipRequest, request)
        dropped: list[str] = []
        try:
            saved = stage_result_json(self.video.save_clips(_id(video_id), req, from_json(ClipResult, result, dropped)))
            return {**saved, "summary": saved["summary"] + _dropped_note(dropped)} if dropped else saved
        finally:
            if req.provider == "pod":
                self.video.release_gpu(_id(video_id), "clip batch saved")

    def mark_clips(self, video_id: str, shot_ids: list[str], status: str | None, error: str | None) -> None:
        self.video.mark_clips(_id(video_id), _ids(shot_ids), status, error)

    # ---- stock lane and real footage

    def stock_requests(self, video_id: str) -> list[dict[str, Any]]:
        return to_json(self.stock_stage.requests(_id(video_id)))

    def save_stock(self, video_id: str, request: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return stage_result_json(self.stock_stage.save(_id(video_id), from_json(StockRequest, request), from_json(StockResult, result)))

    def footage_search_request(self, video_id: str, shot_ids: list[str] | None, query: str | None) -> dict[str, Any]:
        return to_json(self.footage.search_request(_id(video_id), _ids(shot_ids), query))

    def save_footage_candidates(self, video_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return stage_result_json(self.footage.save_candidates(_id(video_id), from_json(FootageSearchResponse, result)))

    def footage_fetch_request(self, video_id: str, shot_id: str, nasa_id: str, start_sec: float | None) -> dict[str, Any]:
        return to_json(self.footage.fetch_request(_id(video_id), _id(shot_id), nasa_id, start_sec))

    def save_footage(self, video_id: str, request: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return stage_result_json(self.footage.save_footage(_id(video_id), from_json(FootageFetchRequest, request),
                                                           from_json(FootageFetchResult, result)))

    # ---- assembly and finishing

    def assembly_request(self, video_id: str) -> dict[str, Any]:
        return to_json(self.assembly.assembly_request(_id(video_id)))

    def save_master(self, video_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return stage_result_json(self.assembly.save_master(_id(video_id), from_json(AssemblyResult, result)))

    def moon_request(self, video_id: str) -> dict[str, Any]:
        return self.moon.request(_id(video_id))

    def save_moon_layer(self, video_id: str, request: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return stage_result_json(self.moon.save(_id(video_id), request, result))

    def interpolate_request(self, video_id: str) -> dict[str, Any]:
        return self.interpolate.request(_id(video_id))

    def save_interpolated(self, video_id: str, request: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return stage_result_json(self.interpolate.save(_id(video_id), request, result))

    def smooth_estimate(self, video_id: str) -> dict[str, Any]:
        return to_json(self.finish.estimate(_id(video_id)))

    def smooth_request(self, video_id: str) -> dict[str, Any]:
        return to_json(self.finish.request(_id(video_id)))

    def save_smooth(self, video_id: str, request: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return stage_result_json(self.finish.save(_id(video_id), from_json(SmoothRequest, request), from_json(SmoothResult, result)))

    def sharp_estimate(self, video_id: str) -> dict[str, Any]:
        return to_json(self.sharpen.estimate(_id(video_id)))

    def sharp_request(self, video_id: str) -> dict[str, Any]:
        return to_json(self.sharpen.request(_id(video_id)))

    def save_sharp(self, video_id: str, request: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        return stage_result_json(self.sharpen.save(_id(video_id), from_json(SmoothRequest, request), from_json(SmoothResult, result)))

    # ---- paid automatic redos need approval; held stages and Resume

    def redo_check(self, video_id: str, stage: str, shot_ids: list[str] | None) -> dict[str, Any]:
        """Would the Inspector's automatic redo of this stage cost money, may it go ahead without asking (the video's `auto_redo_paid`), and about
        how much. The estimate is best effort: a failure to price it never blocks the question."""
        paid = redo_is_paid(stage)
        auto = bool(self.db.rows("SELECT auto_redo_paid FROM video WHERE id = %(v)s", {"v": _id(video_id)})[0]["auto_redo_paid"])
        inr: float | None = None
        if paid:
            try:
                if stage in ("DRAFT_GEN", "FINAL_GEN"):
                    inr = float(self.video.estimate(_id(video_id), "draft" if stage == "DRAFT_GEN" else "final", _ids(shot_ids)).inr)
                elif stage == "KEYFRAMES" and shot_ids:
                    inr = float(self.shots.d.pricing.image_inr("1K") * len(shot_ids))  # the dearest image model, per redone shot
            except Exception as e:  # noqa: BLE001
                log.warning("Could not price the redo of %s: %s", stage, e)
        return {"paid": paid, "autoPaid": auto, "inr": inr, "what": redo_what(stage)}

    def record_hold(self, video_id: str, stage: str, attempt: int, held_reason: str | None, can_resume: bool, has_stored_result: bool,
                    redo_estimate_inr: float | None) -> None:
        """What the dashboard shows next to a stage that waits for the owner: why, whether Resume is offered, and the redo's estimated cost."""
        self.db.execute(
            "UPDATE stage_run SET held_reason = %(r)s, can_resume = %(c)s, has_stored_result = %(h)s, redo_estimate_inr = %(i)s "
            "WHERE video_id = %(v)s AND stage = %(s)s AND attempt = %(a)s",
            {"r": held_reason, "c": can_resume, "h": has_stored_result, "i": redo_estimate_inr, "v": _id(video_id), "s": stage, "a": attempt})

    # ---- hold before run: a stage that can spend waits for the owner first

    def hold_check(self, video_id: str, stage: str, manual: bool) -> dict[str, Any]:
        """Must this stage wait for the owner BEFORE it runs, and what to show them: a paid stage in Manual mode, or on a video with
        `approve_paid_first`. A stage with nothing to spend (estimate 0, or not a paid stage) never holds. An estimate that can't be made holds
        (unknown cost is not free). The cap line shows the video's budget and, when this run would break it, why."""
        vid = _id(video_id)
        rows = self.db.rows("SELECT approve_paid_first FROM video WHERE id = %(v)s", {"v": vid})
        flag = bool(rows and rows[0]["approve_paid_first"])
        out: dict[str, Any] = {"hold": False, "flag": flag, "estimateInr": None, "provider": None, "what": stage, "text": "", "refusal": None}
        if stage not in plan_preview.HOLDABLE or not (flag or manual):
            return out
        inr, provider, what = self._spend_estimate(vid, stage)
        if inr is not None and inr <= 0:
            return {**out, "estimateInr": 0.0, "provider": provider, "what": what}
        budget, refusal, left = self.shots.d.budget, None, ""
        try:
            b = budget.video_budget(vid)
            if b["capInr"] is not None:
                left = f" Budget of this video: ₹{float(b['remainingInr']):.0f} left of ₹{float(b['capInr']):.0f}."
            if inr is not None:
                refusal = budget.video_refusal(vid, Decimal(str(inr)), what)
        except Exception as e:  # noqa: BLE001 - the question to the owner must never fail because the budget could not be read
            log.warning("Could not read the budget of %s for the hold before %s: %s", video_id, stage, e)
        cost = "about ₹%.0f" % inr if inr is not None else "cost not estimated"
        text = f"Waiting before it spends: {what} on {provider}, {cost}.{left}"
        if refusal is not None:
            text += f" Over the cap: {refusal}"
        text += " Approve to run it, or Reject to skip it (nothing is spent)."
        return {**out, "hold": True, "estimateInr": inr, "provider": provider, "what": what, "text": text, "refusal": refusal}

    def _spend_estimate(self, vid: uuid.UUID, stage: str) -> tuple[float | None, str, str]:
        """(about how many rupees one run costs, the provider, plain words). Live prices where the stage has them, else pricing.holdEstimates.
        Best effort: None = could not be priced."""
        d = self.shots.d
        flat = d.pricing.hold_estimate(stage)
        try:
            if stage == "MUSIC":
                return float(d.pricing.music_inr(1)), "Lyria (Gemini)", "one music track"
            if stage == "KEYFRAMES":
                n = int(self.db.rows("SELECT count(*) AS n FROM shot s JOIN scene sc ON sc.id = s.scene_id WHERE sc.video_id = %(v)s", {"v": vid})[0]["n"])
                review = flat[0] if flat is not None else 0.0
                return float(d.pricing.image_inr("1K") * n) + review, "GPU pod or Gemini images, plus the AI picture review", f"{n} pictures and their AI review"
            if stage in ("DRAFT_GEN", "FINAL_GEN"):
                e = self.video.estimate(vid, "draft" if stage == "DRAFT_GEN" else "final", None)
                return (float(e.inr), "GPU pod or Gemini Omni", f"{e.clips} clips") if e.clips else (0.0, "GPU pod", "the clips")
            if stage == "UPSCALE":
                a, b = self.finish.estimate(vid), self.sharpen.estimate(vid)
                return float(a.inr + b.inr), "GPU pod", f"smoothing {a.clips} and sharpening {b.clips} clips"
        except Exception as e:  # noqa: BLE001
            log.warning("Could not price %s for the hold before it runs: %s", stage, e)
            return None, "a paid service", stage
        if flat is not None:
            return flat
        return None, "a paid service", stage

    def record_hold_before_run(self, video_id: str, stage: str, attempt: int, reason: str, estimate_inr: float | None) -> None:
        """The dashboard's 'Waiting before it spends' line for this attempt (cleared when the stage starts running, see record_stage)."""
        self.db.execute(
            "UPDATE stage_run SET held_before_run = true, held_reason = %(r)s, estimate_inr = %(i)s "
            "WHERE video_id = %(v)s AND stage = %(s)s AND attempt = %(a)s",
            {"r": reason, "i": estimate_inr, "v": _id(video_id), "s": stage, "a": attempt})

    # ---- the database mirror of the workflow

    def record_stage(self, video_id: str, stage: str, attempt: int, mode: str, status: str, summary: str | None, artifact_json: str | None,
                     owner_notes: str | None) -> None:
        """Upsert the stage_run row and the video's status / current stage."""
        vid = _id(video_id)
        done = status not in ("RUNNING", "WAITING_APPROVAL")
        with self.db.transaction():
            self.db.execute(
                """
                INSERT INTO stage_run (id, video_id, stage, attempt, mode, status, summary, artifact_json, owner_notes, finished_at)
                VALUES (%(rid)s, %(vid)s, %(stage)s, %(attempt)s, %(mode)s, %(status)s, %(summary)s, CAST(%(artifact)s AS jsonb), %(notes)s,
                        CASE WHEN CAST(%(done)s AS boolean) THEN now() END)
                ON CONFLICT (video_id, stage, attempt) DO UPDATE SET
                    mode = EXCLUDED.mode, status = EXCLUDED.status,
                    summary = COALESCE(EXCLUDED.summary, stage_run.summary),
                    artifact_json = COALESCE(EXCLUDED.artifact_json, stage_run.artifact_json),
                    owner_notes = COALESCE(EXCLUDED.owner_notes, stage_run.owner_notes),
                    finished_at = EXCLUDED.finished_at,
                    review_deadline = CASE WHEN EXCLUDED.status = 'WAITING_APPROVAL' THEN stage_run.review_deadline END,
                    held_before_run = CASE WHEN EXCLUDED.status = 'RUNNING' THEN false ELSE stage_run.held_before_run END,
                    held_reason = CASE WHEN EXCLUDED.status = 'RUNNING' THEN NULL ELSE stage_run.held_reason END,
                    estimate_inr = CASE WHEN EXCLUDED.status = 'RUNNING' THEN NULL ELSE stage_run.estimate_inr END
                """,
                {"rid": uuid.uuid4(), "vid": vid, "stage": stage, "attempt": attempt, "mode": mode, "status": status, "summary": summary,
                 "artifact": artifact_json, "notes": owner_notes, "done": done})
            if status == "SKIPPED":
                return
            video_status = "WAITING" if status == "WAITING_APPROVAL" else "RUNNING"
            self.db.execute("UPDATE video SET status = %(status)s, current_stage = %(stage)s, updated_at = now() "
                            "WHERE id = %(id)s AND status NOT IN ('COMPLETED','CANCELLED')", {"status": video_status, "stage": stage, "id": vid})

    def set_review_deadline(self, video_id: str, stage: str, attempt: int, deadline_iso: str | None) -> None:
        """When a REVIEW-mode stage will continue on its own (ISO-8601 instant), or None once it is held/decided."""
        self.db.execute("UPDATE stage_run SET review_deadline = CAST(%(d)s AS timestamptz) WHERE video_id = %(v)s AND stage = %(s)s AND attempt = %(a)s",
                        {"d": deadline_iso, "v": _id(video_id), "s": stage, "a": attempt})

    def set_video_status(self, video_id: str, status: str) -> None:
        self.db.execute("UPDATE video SET status = CAST(%(status)s AS text), current_stage = CASE WHEN CAST(%(status)s AS text) = 'COMPLETED' "
                        "THEN NULL ELSE current_stage END, updated_at = now() WHERE id = %(id)s", {"status": status, "id": _id(video_id)})


class ProducerActivities:
    """AI Producer control activities: what to review, and saving the review as advice."""

    def __init__(self, producer: Any, voice: Any):
        self.producer, self.voice = producer, voice

    def review_request(self, video_id: str, stage: str) -> dict[str, Any] | None:
        r = self.producer.review_request(_id(video_id), stage)
        return None if r is None else to_json(r)

    def save_review(self, video_id: str, stage: str, attempt: int, result: dict[str, Any]) -> dict[str, Any]:
        return to_json(self.producer.save_review(_id(video_id), stage, attempt, from_json(ProducerReviewResult, result)))

    def spend_advice(self, video_id: str, estimate_inr: float) -> str:
        """Advice on a money step (no AI call): would the Producer approve it within the owner's limits?"""
        return self.producer.spend_advice(_id(video_id), Decimal(str(estimate_inr)))

    def deciding(self) -> bool:
        return self.producer.deciding()

    def mark_acted(self, decision_id: str | None, acted: str) -> None:
        if decision_id is not None:
            self.producer.mark_acted(_id(decision_id), acted)

    def may_redo(self, video_id: str, round_: int) -> bool:
        return self.producer.may_redo(_id(video_id), round_)

    def shot_ids_for(self, video_id: str, item_ids: list[str]) -> list[str]:
        return self.producer.shot_ids_for(_id(video_id), list(item_ids))

    def narration_redo_request(self, video_id: str, notes: str | None) -> dict[str, Any]:
        from studio.production.producer import with_style

        return to_json(with_style(self.voice.narration_request(_id(video_id)), notes))

    def spend_decision(self, video_id: str, stage: str, estimate_inr: float) -> dict[str, Any]:
        return to_json(self.producer.spend_decision(_id(video_id), stage, Decimal(str(estimate_inr))))


def activity_functions(control: ControlActivities, producer: ProducerActivities) -> list[Callable[..., Any]]:
    """Every public method as a Temporal activity: `control.<method>` and `producer.<method>`. Imported lazily (temporalio is a worker dependency)."""
    from temporalio import activity

    out: list[Callable[..., Any]] = []
    for prefix, obj in (("control", control), ("producer", producer)):
        for name in dir(type(obj)):
            if name.startswith("_"):
                continue
            fn = getattr(obj, name)
            if not callable(fn):
                continue

            @functools.wraps(fn)
            def wrapper(*args: Any, __fn: Callable[..., Any] = fn) -> Any:
                return __fn(*args)

            out.append(activity.defn(name=f"{prefix}.{name}")(wrapper))
    return out


def activity_names(control: type = ControlActivities, producer: type = ProducerActivities) -> list[str]:
    return [f"{p}.{n}" for p, c in (("control", control), ("producer", producer)) for n in dir(c) if not n.startswith("_") and callable(getattr(c, n))]


__all__ = ["ControlActivities", "ProducerActivities", "StageMode", "activity_functions", "activity_names"]
