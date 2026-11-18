"""Modal's own billing numbers (this month) for the dashboard: what Modal metered, how much free credit covered it and
what is actually billed. Read every 10 minutes by the media worker and posted to the control plane, which keeps the
latest copy in memory. Best effort: a failure only means the dashboard shows the studio's own estimate."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request

log = logging.getLogger("projectv.modal_billing")
INTERVAL_SEC = 600


def summary() -> dict:
    import modal

    s = modal.Workspace.from_context().billing.summary()
    adj = s.adjustments or {}
    return {"meteredUsd": float(s.metered_cost), "creditsUsedUsd": -float(adj.get("Credits") or 0),
            "billedUsd": float(s.billed_cost), "periodStart": s.start.isoformat(), "periodEnd": s.end.isoformat(),
            "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def _post(base: str, body: dict) -> None:
    req = urllib.request.Request(f"{base}/api/internal/modal-billing", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).close()


def start(base: str | None = None) -> None:
    base = (base or os.getenv("PROJECTV_PUBLIC_URL") or "http://127.0.0.1:8080").rstrip("/")

    def loop():
        while True:
            wait = INTERVAL_SEC
            try:
                _post(base, summary())
            except Exception as e:  # noqa: BLE001 - dashboard nicety only (e.g. control plane still starting)
                log.debug("Modal billing refresh failed: %s", e)
                wait = 30
            time.sleep(wait)
    threading.Thread(target=loop, daemon=True, name="modal-billing").start()
