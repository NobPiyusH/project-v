"""FastAPI control plane. Same paths and JSON shapes as the Java API (all under /api); routes arrive stage by stage."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from fastapi.responses import Response

from studio.config import Settings
from studio.db import Db, PgDb
from studio.ledger import CostLedger
from studio.provider.registry import ProviderRegistry


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
    app = FastAPI(title="Cinestack control plane (Python)", default_response_class=JavaJson)

    @app.get("/api/ledger")
    def get_ledger() -> Response:
        # Built by hand: FastAPI's own encoder would turn Decimal money into strings.
        return JavaJson(ledger.totals_by_provider())

    @app.get("/api/providers")
    def get_providers() -> Response:
        return JavaJson(providers.all())

    return app
