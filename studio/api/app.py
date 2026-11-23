"""FastAPI control plane. Same paths and JSON shapes as the Java API (all under /api); routes arrive stage by stage."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response

from studio.config import Settings
from studio.db import Db, PgDb
from studio.ledger import BudgetGuard, CostLedger, Pricing
from studio.media.wire import to_json
from studio.planner.presets import StylePresets
from studio.provider.registry import ProviderRegistry
from studio.routing import Router


def _default(o: Any) -> Any:
    if isinstance(o, Decimal):  # Java sends BigDecimal as a JSON number, not a string
        return float(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(type(o))


class JavaJson(Response):
    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return json.dumps(content, default=_default, separators=(",", ":")).encode()


def create_app(db: Db | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = db or PgDb(settings.dsn)
    ledger = CostLedger(db)
    providers = ProviderRegistry(db, settings.config_dir)
    pricing = Pricing(settings.config_dir)
    router = Router(db, BudgetGuard(db, pricing), pricing)
    presets = StylePresets(settings.config_dir)
    app = FastAPI(title="Cinestack control plane (Python)", default_response_class=JavaJson)

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

    return app
