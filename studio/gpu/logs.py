"""One bounded read of a pod's log stream for the dashboard's side panel. Port of PodLogs.

RunPod serves logs as Server-Sent Events (GET /v2/pods/{id}/logs, data: {"ts","source","line"}). The stream never ends by
itself and the endpoint sometimes stalls, so each call reads for at most max_wait, stops early once the stream goes quiet, and
never blocks longer. The key stays on the server.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

_POD_ID = re.compile(r"[A-Za-z0-9_-]{4,64}")
MAX_LINE_CHARS = 2000
MAX_LINES = 400


@dataclass
class Line:
    ts: str
    source: str
    line: str


@dataclass
class Page:
    """next: the timestamp to pass as `since` on the following call (None when nothing came back)."""

    lines: list[Line] = field(default_factory=list)
    next: str | None = None
    error: str | None = None

    def to_json(self) -> dict:
        return {"lines": [{"ts": x.ts, "source": x.source, "line": x.line} for x in self.lines], "next": self.next, "error": self.error}


def valid_pod_id(pod_id: str | None) -> bool:
    return pod_id is not None and _POD_ID.fullmatch(pod_id) is not None


class PodLogs:
    def __init__(self, base_url: str, api_key: str, max_wait_ms: int = 4000, quiet_ms: int = 700):
        self._base = base_url.rstrip("/")
        self._key = (api_key or "").strip()
        self._max_wait, self._quiet = max_wait_ms, quiet_ms

    def configured(self) -> bool:
        return bool(self._key)

    def read(self, pod_id: str, since: str | None, tail: int) -> Page:
        """since: RFC3339 timestamp from the previous page, or None for the last `tail` lines."""
        if not self.configured():
            return Page([], None, "No RunPod key is configured on the control plane")
        if not valid_pod_id(pod_id):
            return Page([], None, "Not a valid pod id")
        query = f"since={quote(since, safe='')}" if since and since.strip() else f"tail={max(0, min(tail, 1000))}"
        url = f"{self._base}/pods/{pod_id}/logs?{query}"
        lines: list[Line] = []
        state = {"last": time.monotonic(), "error": None, "failed": False}
        stop = threading.Event()
        holder: dict[str, httpx.Response] = {}

        def reader() -> None:
            try:
                with httpx.Client(timeout=httpx.Timeout(self._max_wait / 1000, connect=5)) as client, client.stream(
                    "GET", url, headers={"Authorization": f"Bearer {self._key}", "Accept": "text/event-stream"}
                ) as r:
                    holder["r"] = r
                    if r.status_code // 100 != 2:
                        state["error"], state["failed"] = f"RunPod answered {r.status_code}", True
                        return
                    for text in r.iter_lines():
                        if stop.is_set() or len(lines) >= MAX_LINES:
                            return
                        if text.startswith("data:"):
                            parsed = _parse(text[5:].strip())
                            if parsed is not None:
                                lines.append(parsed)
                                state["last"] = time.monotonic()
            except Exception as e:  # noqa: BLE001 - closing the stream ends the read; a real error is reported only with no data
                if not lines and state["error"] is None and not stop.is_set():
                    state["error"] = f"RunPod log stream: {type(e).__name__}"

        t = threading.Thread(target=reader, daemon=True)
        start = time.monotonic()
        t.start()
        try:
            while t.is_alive():
                time.sleep(0.05)
                now = time.monotonic()
                quiet = bool(lines) and (now - state["last"]) * 1000 > self._quiet
                if quiet or (now - start) * 1000 > self._max_wait:
                    break
        finally:
            stop.set()
            r = holder.get("r")
            if r is not None:
                try:
                    r.close()  # unblocks the reader
                except Exception:  # noqa: BLE001
                    pass
        out = list(lines)
        if not out:
            # a quiet pod is normal; only report a failure the reader actually saw
            return Page(out, since, state["error"] if (state["failed"] or state["error"] is not None) else None)
        return Page(out, out[-1].ts, None)


def _parse(data: str) -> Line | None:
    try:
        n = json.loads(data)
        if not isinstance(n, dict):
            return None
        line = str(n.get("line", ""))
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + "…"
        return Line(str(n.get("ts", "")), str(n.get("source", "container")), line)
    except ValueError:  # a malformed event is skipped, not fatal
        return None
