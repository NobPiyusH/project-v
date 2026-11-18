"""Routing decisions made inside the worker (voice engine, laptop vs cloud smoothing) are logged in the control
plane's routing_decision table too (CLAUDE.md rule 6). Best effort: a failed post never fails the media job."""

from __future__ import annotations

import json
import logging
import os
import urllib.request

log = logging.getLogger("projectv.routing")


def log_decision(video_id: str | None, stage: str, capability: str, chosen: str, reason: str,
                 candidates: list[dict] | None = None, base: str | None = None) -> None:
    if not video_id:
        return
    base = (base or os.getenv("PROJECTV_PUBLIC_URL") or "http://127.0.0.1:8080").rstrip("/")
    body = {"videoId": video_id, "stage": stage, "capability": capability, "chosen": chosen, "reason": reason,
            "candidates": candidates or []}
    try:
        req = urllib.request.Request(f"{base}/api/internal/routing", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).close()
    except Exception as e:  # noqa: BLE001 - logging must never break a media job
        log.debug("routing log failed: %s", e)
