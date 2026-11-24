"""FastAPI control plane. Same paths and JSON shapes as the Java API (all under /api); routes arrive stage by stage."""

from __future__ import annotations

import json
import math
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from studio.config import Settings
from studio.db import Db, PgDb, ThreadLocalPgDb
from studio.gpu.events import PgPodEvents
from studio.gpu.leases import PgPodLeases
from studio.gpu.logs import PodLogs
from studio.gpu.pods import GpuPodLimits, RunPodApi
from studio.gpu.watchdog import PodWatchdog, WatchdogLoop
from studio.ledger import BudgetGuard, CostLedger, Pricing
from studio.media.wire import to_json
from studio.planner.presets import StylePresets
from studio.provider.registry import ProviderRegistry
from studio.routing import Router
from studio.singleton import JOBS, WATCHDOG, Singleton


def _default(o: Any) -> Any:
    if isinstance(o, Decimal):  # Java sends BigDecimal as a JSON number, not a string
        return float(o)
    if type(o).__name__ == "date":  # a java.sql.Date column: Jackson sends it as midnight UTC, e.g. autopilot_run.run_date
        return f"{o.isoformat()}T00:00:00.000Z"
    if isinstance(o, datetime):  # Java sends timestamps as UTC with millisecond precision: 2026-11-22T18:16:06.990Z
        u = o.astimezone(UTC)
        return u.strftime("%Y-%m-%dT%H:%M:%S.") + f"{u.microsecond // 1000:03d}Z"
    if isinstance(o, uuid.UUID):
        return str(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(type(o))


class JavaJson(Response):
    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return json.dumps(content, default=_default, separators=(",", ":")).encode()



def create_app(db: Db | None = None, settings: Settings | None = None, dream_ctx: Any = None, video_ctx: Any = None, lock_dsn: str | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = db or ThreadLocalPgDb(settings.dsn)  # one connection per thread: requests and background jobs run on many
    ledger = CostLedger(db)
    providers = ProviderRegistry(db, settings.config_dir)
    pricing = Pricing(settings.config_dir)
    router = Router(db, BudgetGuard(db, pricing), pricing)
    presets = StylePresets(settings.config_dir)
    # ---- GPU pods: the watchdog loop (STUDIO_GPU_WATCHDOG, on by default) is the only watchdog; an advisory lock keeps it to one process
    pod_api = RunPodApi(settings.runpod_base_url, settings.runpod_api_key)
    leases = PgPodLeases(db)
    pod_events = PgPodEvents(db, ledger, pricing)
    watchdog = PodWatchdog(pod_api, leases, pod_events, GpuPodLimits.from_settings(settings))
    pod_logs = PodLogs(settings.runpod_base_url, settings.runpod_api_key)
    loop = WatchdogLoop([watchdog], settings.gpu_watchdog_poll_seconds)

    ctx = dream_ctx
    if ctx is None and settings.dream_routes:
        from studio.api.wiring import build_dream_context

        ctx = build_dream_context(db, settings)
    vctx = video_ctx
    if vctx is None and settings.video_routes:
        from studio.api.video_wiring import build_video_context

        vctx = build_video_context(db, settings, dream_ctx=ctx)
    jobs: list[Any] = []
    if ctx is not None and settings.jobs:
        from studio.api.jobs import dream_jobs

        jobs = dream_jobs(ctx)

    dsn = lock_dsn or settings.dsn
    watchdog_lock = Singleton(dsn, WATCHDOG, "the GPU watchdog")
    jobs_lock = Singleton(dsn, JOBS, "the periodic jobs")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.migrate_on_start:
            from studio.migrate import migrate

            migrate(PgDb(dsn))  # an edited or failed migration stops the start (MigrationError)
        if settings.gpu_watchdog_python and watchdog_lock.try_acquire():
            loop.start()
        if jobs and jobs_lock.try_acquire():
            for j in jobs:
                j.start()
        yield
        for j in jobs:
            j.stop()
        loop.stop()
        watchdog_lock.release()
        jobs_lock.release()

    app = FastAPI(title="Cinestack control plane (Python)", default_response_class=JavaJson, lifespan=lifespan)

    @app.exception_handler(Exception)
    async def unexpected(request: Any, e: Exception) -> Response:
        """Like Spring's default error body: an unexpected exception is a 500 (a missing row, a refused step...)."""
        return JavaJson({"timestamp": datetime.now(UTC), "status": 500, "error": "Internal Server Error", "message": str(e),
                         "path": request.url.path}, status_code=500)

    if ctx is not None and settings.dream_routes:
        from studio.api.dream_routes import register_dream_routes

        register_dream_routes(app, ctx, JavaJson)

    if vctx is not None and settings.video_routes:
        from studio.api.video_routes import register_video_routes

        register_video_routes(app, vctx, JavaJson)

    @app.get("/api/ledger")
    def get_ledger() -> Response:
        # Built by hand: FastAPI's own encoder would turn Decimal money into strings.
        return JavaJson(ledger.totals_by_provider())

    @app.get("/api/providers")
    def get_providers() -> Response:
        return JavaJson(providers.all())

    # ---- routing (RoutingController) and styles
    @app.get("/api/routing/providers")
    def routing_providers() -> Response:
        return JavaJson(router.providers())

    @app.get("/api/routing/budgets")
    def routing_budgets() -> Response:
        return JavaJson(router.budgets())

    @app.get("/api/routing/health")
    def routing_health() -> Response:
        return JavaJson(
            db.rows("SELECT provider_id, fails, cooldown_until, last_error, updated_at FROM provider_health ORDER BY provider_id")
        )

    @app.get("/api/videos/{video_id}/routing")
    def routing_decisions(video_id: uuid.UUID) -> Response:
        return JavaJson(router.decisions(video_id))

    @app.put("/api/routing/providers/{provider_id}/enabled")
    def routing_set_enabled(provider_id: str, on: bool) -> Response:
        try:
            router.set_owner_disabled(provider_id, not on)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return Response(status_code=200)

    @app.post("/api/routing/providers/{provider_id}/reset")
    def routing_reset(provider_id: str) -> Response:
        router.reset_health(provider_id)
        return Response(status_code=200)

    @app.get("/api/styles")
    def styles() -> Response:
        return JavaJson(to_json(presets.all()))

    # ---- GPU (GpuWatchdogController, GpuLogsController)
    @app.get("/api/gpu/watchdog")
    def gpu_watchdog() -> Response:
        last = watchdog.last()
        return JavaJson({
            "limits": watchdog.limits().to_json(),
            "lastSweep": last.to_json() if last else None,  # None until the first sweep
            "lastSweepVast": None,  # Vast.ai arrives with the providers (S4c)
            "leases": leases.recent(20),
            "terminations": pod_events.recent(20),
        })

    @app.get("/api/gpu/pods")
    def gpu_pods() -> Response:
        out: dict[str, Any] = {"configured": pod_api.configured()}
        if not pod_api.configured():
            out["pods"] = []
            return JavaJson(out)
        try:
            now = datetime.now(UTC)
            pods = []
            for p in pod_api.list_pods():
                hours = 0.0 if p.created_at is None else int((now - p.created_at).total_seconds()) / 3600.0
                pods.append({
                    "id": p.id, "name": p.name, "status": p.status, "costPerHrUsd": p.cost_per_hr_usd,
                    "createdAt": None if p.created_at is None else p.created_at.isoformat().replace("+00:00", "Z"),
                    "runningMinutes": math.floor(hours * 60 + 0.5),
                    "spentUsdEstimate": math.floor(hours * p.cost_per_hr_usd * 100 + 0.5) / 100.0,
                })
            out["pods"] = pods
        except Exception as e:  # noqa: BLE001
            out["pods"] = []
            out["error"] = f"Could not list pods: {type(e).__name__}"
        return JavaJson(out)

    @app.get("/api/gpu/pods/{pod_id}/logs")
    def gpu_pod_logs(pod_id: str, since: str | None = None, tail: int = 150) -> Response:
        return JavaJson(pod_logs.read(pod_id, since, tail).to_json())

    return app
