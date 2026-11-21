"""Laptop side of the GPU pod route: talks to workers/pod/server.py (RunPod HTTP proxy or any provider endpoint).

The control plane owns the rented pod (create, lease, delete; com.projectv.gpu.broker). This module only waits for the
worker to be ready and runs jobs on it. A preempted or reallocated instance comes back empty (new disk, new job table):
a job that vanishes or a dropped connection means "wait for ready again and submit the same job again", at most
RETRIES times per item, then the item is reported failed (the step then waits for the owner, rule 2b).
No key is needed for RunPod's proxy; an optional POD_AUTH_HEADER ("Name: value") is sent when a provider needs one, never logged.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

RETRIES = 2
POLL = 3.0
# How long ONE job may run once the pod has started it. Time spent queued behind other Shorts' jobs never counts
# (21 Nov 2026: six Shorts on one pod timed out while waiting in the queue). The Temporal StartToClose is only the outer cap.
RUN_LIMIT_S = {"picture": 10 * 60, "clip": 45 * 60}


class NotReady(Exception):
    """The worker is gone or restarting (preempted / reallocated / still downloading)."""


class JobFailed(Exception):
    """The worker ran the job and it failed (a real error, not the instance going away)."""


class PodGone(Exception):
    """The control plane says the Short's pod no longer exists (deleted from outside, self-stopped).
    released=True: the studio itself released it (the Short ended), so nothing may rent again."""

    def __init__(self, msg: str, released: bool = False):
        super().__init__(msg)
        self.released = released


def _headers(extra: dict | None = None) -> dict:
    # RunPod's proxy sits behind Cloudflare, which answers 403 (error 1010) to Python's default "Python-urllib" agent.
    h = {"User-Agent": "projectv-pod-client/1.0"}
    custom = os.environ.get("POD_AUTH_HEADER", "")
    if ":" in custom:
        k, v = custom.split(":", 1)
        h[k.strip()] = v.strip()
    if extra:
        h.update(extra)
    return h


def _call(url: str, body: dict | None = None, timeout: float = 60) -> tuple[int, bytes]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers({"Content-Type": "application/json"} if data else None),
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""
    except (urllib.error.URLError, OSError) as e:
        raise NotReady(f"{type(e).__name__}: {e}") from e


def health(base: str) -> dict:
    try:
        status, body = _call(f"{base}/health", timeout=15)
    except NotReady as e:
        return {"ready": False, "phase": str(e)}
    try:
        h = json.loads(body or b"{}")
    except ValueError:
        h = {}
    h["ready"] = status == 200 and bool(h.get("ready"))
    return h


def wait_ready(base: str, timeout_s: float, beat=lambda info: None, sleep=time.sleep, clock=time.monotonic) -> None:
    """Blocks until /health says ready. Raises RuntimeError on a boot failure or after timeout_s."""
    end = clock() + timeout_s
    while True:
        h = health(base)
        if h["ready"]:
            return
        if h.get("phase") == "failed":
            raise RuntimeError(f"Pod worker failed to start: {h.get('error')}")
        beat({"pod": h.get("phase")})
        if clock() >= end:
            raise RuntimeError(f"Pod worker not ready after {timeout_s / 60:.0f} min (last phase: {h.get('phase')})")
        sleep(10)


def run_once(base: str, kind: str, payload: dict, beat=lambda info: None, sleep=time.sleep,
             clock=time.monotonic) -> tuple[bytes, dict]:
    """Submit one job and wait for its bytes. NotReady when the instance went away; JobFailed on a real error or when
    the job RUNS longer than RUN_LIMIT_S[kind] (queued time is free: the clock starts when the pod reports "running")."""
    limit = RUN_LIMIT_S.get(kind)
    started = None
    status, body = _call(f"{base}/{kind}", payload)
    if status == 503 or status >= 500:
        raise NotReady(f"submit {kind}: HTTP {status}")
    if status != 202:
        raise JobFailed(f"submit {kind}: HTTP {status} {body[:300].decode(errors='replace')}")
    jid = json.loads(body)["id"]
    while True:
        sleep(POLL)
        status, body = _call(f"{base}/jobs/{jid}")
        if status == 404:
            raise NotReady("job vanished (instance restarted)")
        if status != 200:
            raise NotReady(f"job poll: HTTP {status}")
        j = json.loads(body)
        beat({"pod": j.get("state")})
        if j["state"] == "running" and started is None:
            started = clock()
        if limit and started is not None and j["state"] == "running" and clock() - started > limit:
            raise JobFailed(f"{kind} ran {limit // 60} min on the pod without finishing")
        if j["state"] == "failed":
            raise JobFailed(j.get("error") or "failed")
        if j["state"] == "done":
            status, data = _call(f"{base}/jobs/{jid}/result", timeout=300)
            if status != 200:
                raise NotReady(f"result: HTTP {status}")
            return data, j


def run(base: str, kind: str, payload: dict, ready_timeout_s: float, beat=lambda info: None,
        sleep=time.sleep, clock=time.monotonic) -> tuple[bytes, dict]:
    """run_once with the preemption retry: wait for ready, then (re)submit, at most RETRIES extra times."""
    last = None
    for attempt in range(RETRIES + 1):
        wait_ready(base, ready_timeout_s, beat, sleep, clock)
        try:
            data, info = run_once(base, kind, payload, beat, sleep, clock)
            info["retries"] = attempt
            return data, info
        except NotReady as e:
            last = e
            beat({"pod": f"instance lost ({e}); retry {attempt + 1}/{RETRIES}"})
    raise RuntimeError(f"Pod {kind} failed after {RETRIES} retries: {last}")


def lease_beat(control_plane: str, video_id: str, inner=lambda info: None, every: float = 60.0, clock=time.monotonic,
               pod_url=None):
    """A beat callback that also pings the control plane (at most every `every` s) so the Short's GPU lease stays
    warm while the worker waits for downloads or runs jobs. Best effort: a failed ping never fails a job.
    The ping's answer says whether the pod still exists: GONE raises PodGone (the caller rents a replacement), NONE
    raises PodGone(released=True). A GONE whose worker still answers /health ready is ignored (a status blip must
    never cost a second rental). pod_url() gives the pod address currently in use (it changes after a replacement)."""
    last = [-1e9]

    def beat(info):
        inner(info)
        now = clock()
        if now - last[0] < every:
            return
        last[0] = now
        try:
            req = urllib.request.Request(f"{control_plane.rstrip('/')}/api/internal/gpu/{video_id}/heartbeat",
                                         data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as r:
                pod = str(json.loads(r.read() or b"{}").get("pod") or "OPEN").upper()
        except Exception:  # noqa: BLE001
            return
        if pod == "NONE":
            raise PodGone("the studio released this Short's GPU (the Short ended or was cancelled)", released=True)
        if pod == "GONE" and not (pod_url and health(pod_url())["ready"]):
            raise PodGone("the GPU pod no longer exists (deleted from outside or self-stopped)")
    return beat


def replace_pod(control_plane: str, video_id: str) -> tuple[str, float]:
    """Asks the control plane for a replacement pod (it books the gone one and rents again). Returns (url, ready_s)."""
    req = urllib.request.Request(f"{control_plane.rstrip('/')}/api/internal/gpu/{video_id}/replace",
                                 data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"replacement GPU refused: HTTP {e.code} {(e.read() or b'')[:300].decode(errors='replace')}") from e
    return str(body["url"]).rstrip("/"), float(body.get("readyTimeoutSeconds") or 1800)


class Rented:
    """The Short's pod for one GPU step: runs jobs and, when the pod vanishes mid-step, rents ONE replacement and
    resubmits the same job there. A second loss (or a released rental) fails the rest of the step fast, so it waits
    for the owner's Retry within minutes instead of sitting on a dead address for 30 (rule 2b)."""

    REPLACEMENTS = 1

    def __init__(self, control_plane: str, video_id: str, url: str, ready_s: float, inner=lambda info: None):
        self.control_plane, self.video_id = control_plane, video_id
        self.url, self.ready_s = url.rstrip("/"), ready_s
        self.replaced = 0
        self.lost: str | None = None  # set once the step cannot get a pod any more
        self.beat = lease_beat(control_plane, video_id, inner, pod_url=lambda: self.url)

    def run(self, kind: str, payload: dict) -> tuple[bytes, dict]:
        while True:
            if self.lost:
                raise RuntimeError(self.lost)
            try:
                return run(self.url, kind, payload, self.ready_s, beat=self.beat)
            except PodGone as e:
                if e.released or self.replaced >= self.REPLACEMENTS:
                    self.lost = f"GPU pod lost: {e}" + ("" if e.released else "; already replaced once. Retry rents a new one.")
                    raise RuntimeError(self.lost) from e
                self.replaced += 1
                self.beat({"pod": f"pod gone ({e}); renting a replacement"})
                try:
                    self.url, self.ready_s = replace_pod(self.control_plane, self.video_id)
                except Exception as re:  # noqa: BLE001
                    self.lost = f"GPU pod lost ({e}) and no replacement: {re}"
                    raise RuntimeError(self.lost) from re
