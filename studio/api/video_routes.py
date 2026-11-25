"""HTTP routes of the video pipeline: the rest of ApiController (videos, stages, shots, script lines, clips, footage, assets, budget, stages list,
system) and ProducerController. Same paths and JSON shapes as the Java API.

Mounted only when STUDIO_VIDEO_ROUTES=true (default off: Java serves these routes). Like ApiController's own handlers, an IllegalState-style
refusal (RuntimeError) is a 409 with {"error"} and a missing row (LookupError) is a 404 with {"error"}; any other unexpected exception is a 500."""

from __future__ import annotations

import functools
import inspect
import json
import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from studio.api import plan_preview
from studio.api.video_wiring import VideoContext
from studio.ledger.cost_view import CostView
from studio.media.wire import to_json
from studio.planner.imported import read_plan_file, title_of
from studio.video.service import REDO_WITH, CreateVideo
from studio.workflow.stage import Stage, StageMode


def _stage(name: str) -> Stage:
    try:
        return Stage[name]
    except KeyError as e:
        raise ValueError(f"No enum constant Stage.{name}") from e


def register_video_routes(app: FastAPI, v: VideoContext, JavaJson: type[Response]) -> None:  # noqa: N803
    modal_billing: dict[str, Any] = {}

    def guarded(fn):
        """ApiController's @ExceptionHandlers: refusal -> 409, missing row -> 404, both with {"error": message}."""
        def refusal(e: BaseException) -> Response:
            return JavaJson({"error": str(e)}, status_code=409 if isinstance(e, RuntimeError) else 404)

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except (RuntimeError, LookupError) as e:
                    return refusal(e)
            return awrapper

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):  # sync: FastAPI runs it in its thread pool, so database work never blocks the event loop
            try:
                return fn(*args, **kwargs)
            except (RuntimeError, LookupError) as e:
                return refusal(e)
        return wrapper

    async def body(request: Request, required: bool = True) -> dict[str, Any] | None:
        raw = await request.body()
        if not raw.strip():
            if required:
                raise HTTPException(status_code=400, detail="Required request body is missing")
            return None
        try:
            return json.loads(raw)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="Malformed JSON request") from e

    def ok() -> Response:
        return Response(status_code=200)

    def ids(xs: list[str] | None) -> list[uuid.UUID] | None:
        return None if xs is None else [uuid.UUID(x) for x in xs]

    # ---- videos

    @app.post("/api/videos")
    @guarded
    async def create(request: Request):
        b = await body(request)
        plan_doc = b.get("plan")
        if b.get("planFile") is not None:  # a plan file in docs/shorts, e.g. "moon-10x-closer-plan.json"
            try:
                plan_doc = read_plan_file(str(b["planFile"]))
            except (ValueError, OSError) as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
        if plan_doc is not None and not isinstance(plan_doc, dict):
            raise HTTPException(status_code=400, detail="plan must be a JSON object")
        prompt = b.get("prompt")
        if prompt is None and plan_doc is not None:
            prompt = title_of(plan_doc)  # the plan carries the idea; the prompt is only its label
        if prompt is None or not str(prompt).strip() or len(prompt) > 4000:
            raise HTTPException(status_code=400, detail="prompt must not be blank and at most 4000 characters")
        budget = b.get("budgetInr")
        if budget is not None and (isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget < 0):
            raise HTTPException(status_code=400, detail="budgetInr must be a number of rupees, 0 or more (0 = no cap); leave it out for the default")
        modes = None if b.get("modes") is None else {_stage(k): StageMode.parse(m) for k, m in b["modes"].items()}
        try:
            vid = v.videos.create(CreateVideo(prompt, None if b.get("defaultMode") is None else StageMode.parse(b["defaultMode"]), modes,
                                              b.get("reviewMinutes"), budget, plan_doc, bool(b.get("autoRedoPaid", False))))
        except ValueError as e:  # an invalid plan file
            raise HTTPException(status_code=400, detail=f"plan rejected: {e}") from e
        return JavaJson({"id": vid}, status_code=201)

    @app.post("/api/plans/validate")
    @guarded
    async def validate_plan(request: Request):
        """Dry run for the dashboard's "Load plan file": the same loader as POST /api/videos, nothing stored or started. Always 200 with
        {"ok": false, "error"} for a bad plan, so the page can show the reason; 400 only when the request itself is malformed."""
        b = await body(request)
        doc = b.get("plan")
        if b.get("planFile") is not None:
            try:
                doc = read_plan_file(str(b["planFile"]))
            except (ValueError, OSError) as e:
                return JavaJson({"ok": False, "error": str(e)})
        if not isinstance(doc, dict):
            return JavaJson({"ok": False, "error": "Give a plan: a JSON object (or a planFile name)"})
        return JavaJson(plan_preview.summary(v, doc))

    @app.get("/api/videos")
    @guarded
    def list_videos():
        return JavaJson(v.videos.list())

    @app.get("/api/videos/{vid}")
    @guarded
    def detail(vid: uuid.UUID):
        d = dict(v.videos.detail(vid))
        d["costs"] = v.ledger.for_video(vid)
        d["budget"] = v.budget.video_budget(vid)  # cap, spent and what is left of this video's own spend cap
        d["inspections"] = v.inspector.reports(vid)
        d["creativeReviews"] = v.creative.reports(vid)
        return JavaJson(d)

    @app.get("/api/videos/{vid}/cost")
    @guarded
    def cost(vid: uuid.UUID):
        """Read-only: cost per stage, provider and pod, total vs cap, cost per second of the finished video."""
        return JavaJson(CostView(v.db, v.budget).for_video(vid))

    @app.put("/api/videos/{vid}/budget")
    @guarded
    async def set_budget(vid: uuid.UUID, request: Request):
        """Raise (or lower) one video's spend cap, e.g. to let a stage held by the cap go on: {"budgetInr": 300}; null = the default."""
        b = await body(request)
        n = b.get("budgetInr")
        if n is not None and (isinstance(n, bool) or not isinstance(n, (int, float)) or n < 0):
            raise HTTPException(status_code=400, detail="budgetInr must be a number of rupees, 0 or more (0 = no cap), or null for the default")
        if not v.budget.set_video_budget(vid, None if n is None else Decimal(str(n))):
            raise LookupError(f"video {vid}: not found")
        return JavaJson(v.budget.video_budget(vid))

    @app.post("/api/videos/{vid}/stages/{stage}/approve")
    @guarded
    async def approve(vid: uuid.UUID, stage: str, request: Request):
        b = await body(request, required=False)
        v.videos.approve(vid, _stage(stage), None if b is None else b.get("notes"))
        return ok()

    @app.post("/api/videos/{vid}/stages/{stage}/reject")
    @guarded
    async def reject(vid: uuid.UUID, stage: str, request: Request):
        b = await body(request, required=False)
        v.videos.reject(vid, _stage(stage), None if b is None else b.get("notes"))
        return ok()

    @app.post("/api/videos/{vid}/stages/{stage}/rerun")
    @guarded
    def rerun(vid: uuid.UUID, stage: str):
        return JavaJson({"stages": [s.name for s in v.videos.rerun(vid, _stage(stage))]})

    @app.post("/api/videos/{vid}/stages/{stage}/resume")
    @guarded
    def resume_stage(vid: uuid.UUID, stage: str):
        v.videos.resume_stage(vid, _stage(stage))
        return ok()

    @app.put("/api/videos/{vid}/auto-redo-paid")
    @guarded
    async def set_auto_redo_paid(vid: uuid.UUID, request: Request):
        v.videos.set_auto_redo_paid(vid, bool((await body(request)).get("enabled")))
        return ok()

    @app.put("/api/videos/{vid}/modes/{stage}")
    @guarded
    async def set_mode(vid: uuid.UUID, stage: str, request: Request):
        v.videos.set_mode(vid, _stage(stage), StageMode.parse((await body(request)).get("mode")))
        return ok()

    @app.put("/api/videos/{vid}/modes")
    @guarded
    async def set_all_modes(vid: uuid.UUID, request: Request):
        v.videos.set_all_modes(vid, StageMode.parse((await body(request)).get("mode")))
        return ok()

    @app.post("/api/videos/{vid}/shots/redo")
    @guarded
    async def redo_shots(vid: uuid.UUID, request: Request):
        b = await body(request)
        return JavaJson({"workflowId": v.videos.redo_shots(vid, ids(b.get("shotIds")), b.get("notes"), b.get("policy"))})

    @app.put("/api/shots/{shot_id}/prompt")
    @guarded
    async def edit_shot_prompt(shot_id: uuid.UUID, request: Request):
        p = (await body(request)).get("keyframePrompt")
        if p is None or not str(p).strip() or len(p) > 4000:
            raise HTTPException(status_code=400, detail="keyframePrompt must not be blank and at most 4000 characters")
        v.shots.edit_shot_prompt(shot_id, p)
        return ok()

    @app.get("/api/shots/{shot_id}/versions")
    @guarded
    def shot_versions(shot_id: uuid.UUID):
        return JavaJson(v.shots.shot_versions(shot_id))

    @app.put("/api/shots/{shot_id}/keyframe")
    @guarded
    async def select_keyframe(shot_id: uuid.UUID, request: Request):
        v.shots.select_keyframe(shot_id, uuid.UUID((await body(request))["assetId"]))
        return ok()

    @app.put("/api/videos/{vid}/script/lines/{index}")
    @guarded
    async def edit_line(vid: uuid.UUID, index: int, request: Request):
        b = await body(request)
        t = b.get("text")
        if t is None or not str(t).strip() or len(t) > 2000:
            raise HTTPException(status_code=400, detail="text must not be blank and at most 2000 characters")
        return JavaJson(to_json(v.voice.edit_line(vid, index, t, b.get("spoken"))))

    # ---- M2b clips

    @app.get("/api/videos/{vid}/clips/estimate")
    @guarded
    def clip_estimate(vid: uuid.UUID, kind: str, shotIds: Annotated[list[uuid.UUID] | None, Query()] = None):  # noqa: N803
        return JavaJson(to_json(v.video.estimate(vid, kind, shotIds or None)))

    @app.post("/api/videos/{vid}/clips/redo")
    @guarded
    async def redo_clips(vid: uuid.UUID, request: Request):
        b = await body(request)
        return JavaJson({"workflowId": v.videos.redo_clips(vid, ids(b.get("shotIds")), b.get("kind"))})

    @app.post("/api/videos/{vid}/clips/stop")
    @guarded
    def stop_clips(vid: uuid.UUID):
        v.clip_progress.request_stop(vid)
        return ok()

    @app.get("/api/videos/{vid}/clips/progress")
    @guarded
    def clip_progress(vid: uuid.UUID):
        p = v.clip_progress.get(vid)
        return JavaJson({"clips": [], "finished": True} if p is None else {"clips": p.clips, "finished": p.finished, "at": p.at})

    @app.put("/api/shots/{shot_id}/final-wanted")
    @guarded
    def final_wanted(shot_id: uuid.UUID, wanted: bool):
        v.video.set_final_wanted(shot_id, wanted)
        return ok()

    @app.get("/api/shots/{shot_id}/clips")
    @guarded
    def clip_versions(shot_id: uuid.UUID):
        return JavaJson(v.video.clip_versions(shot_id))

    @app.put("/api/shots/{shot_id}/clips/{asset_id}")
    @guarded
    def select_clip(shot_id: uuid.UUID, asset_id: uuid.UUID):
        v.video.select_clip(shot_id, asset_id)
        return ok()

    # Called by the local media worker (same machine): progress of a running batch, and whether to stop.
    @app.post("/api/internal/clips/progress")
    @guarded
    async def report_clip_progress(request: Request):
        b = await body(request)
        v.clip_progress.report(uuid.UUID(b["videoId"]), b.get("clips"), b.get("finished") is True)
        return ok()

    @app.get("/api/internal/clips/{vid}/stop")
    @guarded
    def clip_stop(vid: uuid.UUID):
        return JavaJson({"stop": v.clip_progress.stop_requested(vid)})

    # ---- free real footage, image review, script lines

    @app.post("/api/videos/{vid}/shots/{shot_id}/footage")
    @guarded
    async def footage(vid: uuid.UUID, shot_id: uuid.UUID, request: Request):
        b = await body(request)
        pick = b.get("nasaId") is not None and str(b["nasaId"]).strip() != ""
        if not pick and (b.get("query") is None or not str(b["query"]).strip()):
            raise RuntimeError("Give search words or pick a clip")
        return JavaJson({"workflowId": v.videos.footage(vid, shot_id, b.get("query"), b["nasaId"] if pick else None, b.get("startSec"))})

    @app.delete("/api/shots/{shot_id}/footage")
    @guarded
    def clear_footage(shot_id: uuid.UUID):
        v.footage.clear_footage(shot_id)
        return ok()

    @app.post("/api/videos/{vid}/creative-review")
    @guarded
    def creative_review(vid: uuid.UUID):
        return JavaJson({"workflowId": v.videos.review_images(vid)})

    @app.post("/api/videos/{vid}/script/lines/redo")
    @guarded
    async def redo_lines(vid: uuid.UUID, request: Request):
        b = await body(request)
        return JavaJson({"workflowId": v.videos.redo_lines(vid, b.get("lines"), b.get("style"))})

    @app.put("/api/videos/{vid}/script/lines/{index}/voice")
    @guarded
    async def select_line_voice(vid: uuid.UUID, index: int, request: Request):
        v.voice.select_line_voice(vid, index, uuid.UUID((await body(request))["assetId"]))
        return ok()

    @app.put("/api/videos/{vid}/review-minutes")
    @guarded
    async def set_review_minutes(vid: uuid.UUID, request: Request):
        v.videos.set_review_minutes(vid, int((await body(request))["minutes"]))
        return ok()

    @app.post("/api/videos/{vid}/pause")
    @guarded
    def pause(vid: uuid.UUID):
        v.videos.pause(vid)
        return ok()

    @app.post("/api/videos/{vid}/resume")
    @guarded
    def resume(vid: uuid.UUID):
        v.videos.resume(vid)
        return ok()

    @app.get("/api/videos/{vid}/production")
    @guarded
    def production(vid: uuid.UUID):
        return JavaJson(v.read_model.production(vid))

    # ---- assets, budget, stages, system

    @app.get("/api/assets/{asset_id}/content")
    @guarded
    def asset_content(asset_id: uuid.UUID):
        """Redirects to a short-lived URL on the local S3 store (supports range requests for video seeking)."""
        return RedirectResponse(v.media_urls.presign(v.assets.uri_of(asset_id), 30 * 60), status_code=302)

    @app.get("/api/assets/{asset_id}")
    @guarded
    def asset(asset_id: uuid.UUID):
        return JavaJson(v.assets.describe(asset_id))

    @app.get("/api/budget")
    @guarded
    def budget():
        p, b = v.pricing, v.budget
        out: dict[str, Any] = {"geminiCapInr": p.gemini_monthly_cap_inr(), "geminiSpentInr": b.gemini_spent_this_month(),
                               "geminiRemainingInr": b.gemini_remaining(), "gpuCapInr": p.gpu_monthly_cap_inr(), "gpuSpentInr": b.gpu_spent_this_month(),
                               "claudeCapInr": p.claude_monthly_cap_inr(), "claudeSpentInr": b.claude_spent_this_month()}
        if modal_billing:  # Modal's own numbers, refreshed by the media worker every 10 min
            m = dict(modal_billing)
            free = p.modal_monthly_free_usd()
            used = m.get("creditsUsedUsd")
            used = float(used) if isinstance(used, (int, float)) else 0.0
            m.update({"freeCreditUsd": free, "creditLeftUsd": max(0.0, free - used), "fxInrPerUsd": p.fx()})
            out["modal"] = m
        return JavaJson(out)

    @app.post("/api/internal/modal-billing")
    @guarded
    async def report_modal_billing(request: Request):
        b = await body(request)
        modal_billing.clear()
        modal_billing.update(b)
        return ok()

    @app.get("/api/stages")
    @guarded
    def stages():
        return JavaJson([{"id": s.name, "label": s.label, "description": s.description, "redoWith": [x.name for x in REDO_WITH.get(s, [])]} for s in Stage])

    @app.get("/api/stages/paid")
    @guarded
    def paid_stages():
        """Stage id -> what paid service it calls (a separate route: /api/stages keeps the Java shape)."""
        return JavaJson(plan_preview.PAID_STAGES)

    @app.get("/api/system")
    @guarded
    def system():
        pl = v.planner
        return JavaJson({"planner": f"gemini:{pl.model_name()}" if pl.llm_configured() else "rules (no GEMINI_API_KEY)"})

    # ---- AI Producer report and rules (ProducerController)

    @app.get("/api/videos/{vid}/producer")
    def producer_report(vid: uuid.UUID):
        pr = v.producer
        return JavaJson({"decisions": pr.decisions(vid), "agreement": pr.agreement(), "summary": pr.video_summary(vid)})

    @app.get("/api/producer/settings")
    def producer_settings():
        pr = v.producer
        return JavaJson({"trust": pr.trust(), "deciding": pr.deciding(), "agreement": pr.agreement()})

    @app.put("/api/producer/settings")
    async def producer_set_trust(request: Request):
        v.producer.set_trust((await body(request)).get("trust"))
        return ok()

    @app.get("/api/producer/rules")
    def producer_rules():
        return JavaJson(v.producer.rules_list())

    @app.post("/api/producer/rules")
    async def producer_add_rule(request: Request):
        b = await body(request)
        v.producer.add_rule(b.get("stage"), b.get("text"))
        return ok()

    @app.put("/api/producer/rules/{rule_id}")
    async def producer_update_rule(rule_id: uuid.UUID, request: Request):
        b = await body(request)
        v.producer.update_rule(rule_id, b.get("text"), b.get("enabled"))
        return ok()

    @app.delete("/api/producer/rules/{rule_id}")
    def producer_delete_rule(rule_id: uuid.UUID):
        v.producer.delete_rule(rule_id)
        return ok()

