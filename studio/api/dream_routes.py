"""HTTP routes of the Dream Shorts screen, the Dream GPU controls and the Autopilot screen. Port of DreamController, DreamGpuController and
AutopilotController: same paths, same JSON field names and shapes as the Java API (so the dashboard needs no change).

Mounted only when STUDIO_DREAM_ROUTES=true (default off: Java serves these routes). Errors follow Java: an unexpected exception is a 500; the GPU
pick is the one place with its own 400 (unknown option / bad mode) and 409 (known option, blocked now)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from studio.api.wiring import DreamContext
from studio.autopilot.service import Settings as AutopilotSettings
from studio.dream import idea_packs, looks
from studio.dream.branches import PathPick
from studio.dream.dream_studio import NewDream
from studio.dream.gpu import PickRefusedError
from studio.dream.wire_types import DREAM_STEPS
from studio.gpu.broker.broker import pick_key
from studio.media.wire import from_json, to_json
from studio.workflow.stage import StageMode


def pg_json(v: Any) -> Any:
    """How Java's JdbcClient serialises a jsonb column it did not convert to text: Jackson writes the PGobject itself."""
    return None if v is None else {"null": False, "type": "jsonb", "value": json.dumps(v)}


def instant(t: datetime | None) -> str | None:
    """java.time.Instant.toString(): UTC, 0/3/6/9 fraction digits (here 0, 3 or 6)."""
    if t is None:
        return None
    u = t.astimezone(UTC)
    frac = f"{u.microsecond:06d}"
    frac = "" if u.microsecond == 0 else frac[:3] if frac.endswith("000") else frac
    return u.strftime("%Y-%m-%dT%H:%M:%S") + ("." + frac if frac else "") + "Z"


def _step(step: str) -> str:
    s = step.upper()
    if s not in DREAM_STEPS:
        raise ValueError(f"No enum constant DreamStep.{s}")
    return s


def detail_json(d: dict[str, Any]) -> dict[str, Any]:
    """DreamStudio.detail turned into the Java JSON: jsonb columns Java left as PGobjects, timeline and log in camelCase with Instant times."""
    out = dict(d)
    out["modes_json"] = pg_json(out.get("modes_json")) if not isinstance(out.get("modes_json"), str) else out["modes_json"]
    out["timeline"] = [{"step": t.step, "status": t.status, "startedAt": instant(t.started_at), "finishedAt": instant(t.finished_at),
                        "workSeconds": t.work_seconds, "costInr": t.cost_inr, "typicalSeconds": t.typical_seconds} for t in d["timeline"]]
    out["log"] = [{"at": instant(line.at), "text": line.text} for line in d["log"]]
    return out


def register_dream_routes(app: FastAPI, ctx: DreamContext, JavaJson: type[Response]) -> None:  # noqa: N803
    studio, lessons, gpu, autopilot = ctx.studio, ctx.lessons, ctx.gpu, ctx.autopilot

    def ok() -> Response:
        return Response(status_code=200)

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

    @app.exception_handler(PickRefusedError)
    async def refused(_r: Request, e: PickRefusedError) -> Response:
        return JavaJson({"error": str(e)}, status_code=409 if e.blocked else 400)

    # ------------------------------------------------------------------------------------------- DreamController

    @app.get("/api/dream/looks")
    def get_looks() -> Response:
        return JavaJson(looks.SELECTABLE_LOOKS)

    @app.get("/api/dream/idea-packs")
    def get_idea_packs() -> Response:
        return JavaJson(to_json(idea_packs.packs()))

    @app.get("/api/dream/lessons")
    def get_lessons() -> Response:
        return JavaJson(lessons.lessons())

    @app.post("/api/dream/lessons/{lesson_id}")
    async def post_lesson(lesson_id: uuid.UUID, request: Request) -> Response:
        b = await body(request)
        lessons.update(lesson_id, b.get("rule"), b.get("target"), b.get("status"))
        return ok()

    @app.get("/api/dream/calibration")
    def get_calibration() -> Response:
        return JavaJson(studio.calibration())

    @app.post("/api/dream/{dream_id}/reviews")
    async def post_review(dream_id: uuid.UUID, request: Request) -> Response:
        b = await body(request)
        shot = uuid.UUID(b["shotId"]) if b.get("shotId") and str(b["shotId"]).strip() else None
        return JavaJson({"id": lessons.review(dream_id, shot, int(b.get("rating", 0)), b.get("note"))})

    @app.get("/api/dream/{dream_id}/reviews")
    def get_reviews(dream_id: uuid.UUID) -> Response:
        return JavaJson(lessons.reviews(dream_id))

    @app.post("/api/dream/{dream_id}/hook")
    async def post_hook(dream_id: uuid.UUID, request: Request) -> Response:
        b = await body(request)
        studio.set_hook(dream_id, b.get("text"), b.get("color"))
        return ok()

    @app.post("/api/dream/{dream_id}/paths")
    async def post_paths(dream_id: uuid.UUID, request: Request) -> Response:
        b = await body(request)
        studio.set_paths(dream_id, b.get("a"), b.get("b"))
        return ok()

    @app.post("/api/dream/{dream_id}/continue")
    async def post_continue(dream_id: uuid.UUID, request: Request) -> Response:
        b = await body(request)
        return JavaJson({"id": studio.continue_dream(dream_id, PathPick.owner(b.get("path")))})

    @app.get("/api/dream/{dream_id}/chain")
    def get_chain(dream_id: uuid.UUID) -> Response:
        return JavaJson(studio.chain(dream_id))

    @app.post("/api/dream")
    async def post_create(request: Request) -> Response:
        return JavaJson({"id": studio.create(from_json(NewDream, await body(request)))})

    @app.get("/api/dream")
    def get_list() -> Response:
        return JavaJson(studio.list())

    @app.get("/api/dream/{dream_id}")
    def get_detail(dream_id: uuid.UUID) -> Response:
        return JavaJson(detail_json(studio.detail(dream_id)))

    @app.post("/api/dream/{dream_id}/steps/{step}/approve")
    def post_approve(dream_id: uuid.UUID, step: str) -> Response:
        studio.approve(dream_id, _step(step))
        return ok()

    @app.post("/api/dream/{dream_id}/steps/{step}/redo")
    async def post_redo(dream_id: uuid.UUID, step: str, request: Request) -> Response:
        b = await body(request, required=False)
        studio.redo(dream_id, _step(step), None if b is None else b.get("shotIds"), None if b is None else b.get("notes"))
        return ok()

    @app.post("/api/dream/{dream_id}/steps/{step}/mode")
    async def post_mode(dream_id: uuid.UUID, step: str, request: Request) -> Response:
        b = await body(request)
        studio.set_mode(dream_id, _step(step), StageMode.parse(b.get("mode")))
        return ok()

    @app.post("/api/dream/{dream_id}/rerun/{step}")
    def post_rerun(dream_id: uuid.UUID, step: str) -> Response:
        studio.rerun(dream_id, _step(step))
        return ok()

    @app.post("/api/dream/{dream_id}/scout/angle/{n}")
    def post_angle(dream_id: uuid.UUID, n: int) -> Response:
        studio.pick_angle(dream_id, n)
        return ok()

    @app.post("/api/dream/{dream_id}/editing")
    async def post_editing(dream_id: uuid.UUID, request: Request) -> Response:
        b = await body(request)
        studio.editing(dream_id, b.get("musicEdit"), b.get("ambience"), b.get("pacing"))
        return ok()

    @app.post("/api/dream/{dream_id}/retry")
    def post_retry(dream_id: uuid.UUID) -> Response:
        studio.retry(dream_id)
        return ok()

    @app.post("/api/dream/{dream_id}/stop")
    def post_stop(dream_id: uuid.UUID) -> Response:
        studio.stop_clips(dream_id)
        return ok()

    @app.post("/api/dream/{dream_id}/cancel")
    def post_cancel(dream_id: uuid.UUID) -> Response:
        studio.cancel(dream_id)
        return ok()

    # ------------------------------------------------------------------------------------------- DreamGpuController

    def need_gpu() -> Any:
        if gpu is None:
            raise HTTPException(status_code=409, detail="The GPU broker is off (DREAM_GPU_PROVIDER=modal)")
        return gpu

    def pick_state(dream_id: uuid.UUID) -> dict[str, Any]:
        g = need_gpu()
        p = g.pick(dream_id)
        return {"mode": p.mode, "choice": "" if p.choice is None else pick_key(p.choice), "rented": g.is_open(dream_id),
                "estimate": g.rental_estimate(dream_id), "usersOnPod": g.users_on_pod(), "hold": "manual" if g.hold_manual(dream_id) else "auto",
                "idleMinutes": g.idle_minutes(dream_id), "offInSeconds": g.off_in_seconds(dream_id, datetime.now(UTC)),
                "boot": g.boot_timing(dream_id)}

    @app.post("/api/internal/gpu/{dream_id}/heartbeat")
    def gpu_heartbeat(dream_id: uuid.UUID) -> Response:
        if gpu is None:
            return JavaJson({"ok": True, "pod": "OPEN"})
        return JavaJson({"ok": True, "pod": gpu.heartbeat(dream_id).name})

    @app.post("/api/internal/gpu/{dream_id}/replace")
    async def gpu_replace(dream_id: uuid.UUID, request: Request) -> Response:
        b = await body(request, required=False)
        s = need_gpu().replace(dream_id) if b is None else gpu.replace(dream_id, b.get("unreadyUrl"), b.get("why"))
        return JavaJson({"url": s.url, "readyTimeoutSeconds": s.ready_timeout_seconds})

    @app.post("/api/internal/gpu/{dream_id}/boot-timing")
    async def gpu_boot_timing(dream_id: uuid.UUID, request: Request) -> Response:
        b = await body(request)
        if isinstance(b.get("timing"), dict):
            need_gpu().report_boot(dream_id, b["timing"])
        return JavaJson({"ok": True})

    @app.get("/api/gpu/broker/options")
    def gpu_options() -> Response:
        return JavaJson(to_json(need_gpu().options()))

    @app.get("/api/dream/{dream_id}/gpu")
    def gpu_get_pick(dream_id: uuid.UUID) -> Response:
        return JavaJson(pick_state(dream_id))

    @app.post("/api/dream/{dream_id}/gpu/hold")
    async def gpu_set_hold(dream_id: uuid.UUID, request: Request) -> Response:
        b = await body(request)
        try:
            need_gpu().set_hold(dream_id, b.get("hold"), b.get("idleMinutes"))
        except ValueError as e:
            return JavaJson({"error": str(e)}, status_code=400)
        return JavaJson(pick_state(dream_id))

    @app.post("/api/dream/{dream_id}/gpu/off")
    def gpu_off(dream_id: uuid.UUID) -> Response:
        need_gpu().turn_off(dream_id)
        return JavaJson(pick_state(dream_id))

    @app.post("/api/dream/{dream_id}/gpu")
    async def gpu_set_pick(dream_id: uuid.UUID, request: Request) -> Response:
        b = await body(request)
        try:
            need_gpu().set_pick(dream_id, b.get("mode"), b.get("choice"))
        except PickRefusedError:
            raise
        except ValueError as e:
            return JavaJson({"error": str(e)}, status_code=400)
        return JavaJson(pick_state(dream_id))

    @app.get("/api/gpu/broker")
    def gpu_status() -> Response:
        if gpu is None or not gpu.enabled():
            return JavaJson({"enabled": False})
        return JavaJson({"enabled": True, "today": gpu.today(), "providers": gpu.provider_statuses()})

    @app.get("/api/gpu/broker/providers")
    def gpu_providers() -> Response:
        return JavaJson(need_gpu().provider_statuses())

    @app.post("/api/gpu/broker/providers/{provider}/preflight")
    def gpu_preflight(provider: str) -> Response:
        r = need_gpu().preflight(provider)  # free pre-check, rents nothing
        return JavaJson({"provider": provider, "ok": r.ok, "checks": to_json(r.checks)})

    @app.post("/api/gpu/broker/providers/{provider}/canary")
    def gpu_canary(provider: str, approve: bool = False, maxUsd: float = 0.60) -> Response:  # noqa: N803
        """ONE real, tiny rental: spends real money, so it needs approve=true, which only the owner sends."""
        if not approve:
            raise HTTPException(status_code=400, detail="The canary rents a real GPU for a few minutes. Send approve=true to confirm.")
        return JavaJson(need_gpu().canary(provider, maxUsd))

    # ------------------------------------------------------------------------------------------- AutopilotController

    @app.get("/api/autopilot")
    def ap_status() -> Response:
        return JavaJson(autopilot.status())

    @app.get("/api/autopilot/settings")
    def ap_settings() -> Response:
        return JavaJson(to_json(autopilot.settings()))

    @app.put("/api/autopilot/settings")
    async def ap_save_settings(request: Request) -> Response:
        return JavaJson(to_json(autopilot.save_settings(from_json(AutopilotSettings, await body(request)))))

    @app.get("/api/autopilot/ideas")
    def ap_ideas() -> Response:
        return JavaJson(autopilot.ideas())

    @app.post("/api/autopilot/ideas")
    async def ap_add_idea(request: Request) -> Response:
        b = await body(request)
        return JavaJson({"id": autopilot.add_idea(b.get("idea"), b.get("look"))})

    @app.delete("/api/autopilot/ideas/{idea_id}")
    def ap_delete_idea(idea_id: uuid.UUID) -> Response:
        autopilot.delete_idea(idea_id)
        return ok()

    @app.get("/api/autopilot/runs")
    def ap_runs() -> Response:
        return JavaJson(autopilot.runs())

    @app.post("/api/autopilot/runs/{run_id}/start")
    def ap_start(run_id: uuid.UUID) -> Response:
        autopilot.start_now(run_id)
        return ok()

    @app.post("/api/autopilot/runs/{run_id}/post")
    async def ap_post(run_id: uuid.UUID, request: Request) -> Response:
        b = await body(request)
        autopilot.post(run_id, b.get("title"), b.get("description"), b.get("hashtags"), b.get("privacy"), ctx.gateway)
        return ok()

    @app.post("/api/autopilot/runs/{run_id}/dismiss")
    def ap_dismiss(run_id: uuid.UUID) -> Response:
        autopilot.dismiss(run_id)
        return ok()

    @app.post("/api/autopilot/runs/{run_id}/retry-kit")
    def ap_retry_kit(run_id: uuid.UUID) -> Response:
        autopilot.retry_kit(run_id)
        return ok()

    @app.post("/api/autopilot/tick")
    def ap_tick() -> Response:
        autopilot.tick()
        return ok()
