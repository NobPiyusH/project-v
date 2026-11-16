"""M2b: moving video per shot. Sends one Wan 2.2 image-to-video job per shot to the Modal app (projectv-wan), up to
4 GPUs in parallel, and saves every clip to object storage the moment it is finished (a stop or a crash never loses
finished clips). While it runs it reports per-clip progress to the control plane for the dashboard and checks for the
owner's Stop: on Stop the remaining Modal calls are cancelled, so no GPU keeps running (CLAUDE.md rule 8).
"""

from __future__ import annotations

import json
import time
import urllib.request

import modal
from temporalio import activity

from workers.common import get_bytes, log, put_bytes

POLL_SECONDS = 5


def _beat(detail) -> None:
    try:
        activity.heartbeat(detail)
    except RuntimeError:  # outside an activity (tests, manual runs)
        pass


def _post(url: str, body: dict) -> None:
    try:
        request = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=5).close()
    except Exception as e:  # progress is best effort; never fail clips over it
        log.debug("progress post failed: %s", e)


def _stop_requested(base: str, video_id: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/api/internal/clips/{video_id}/stop", timeout=5) as r:
            return bool(json.load(r).get("stop"))
    except Exception:
        return False


def _not_ready(e: Exception) -> bool:
    """FunctionCall.get(timeout=0) raises the built-in TimeoutError while the clip is still rendering. Modal's own
    FunctionTimeoutError (the clip ran past its limit) and OutputExpiredError are real failures."""
    return isinstance(e, TimeoutError) and not isinstance(e, modal.exception.Error)


@activity.defn(name="generate_clips")
def generate_clips(req: dict) -> dict:
    """req: ClipRequest {videoId, modalApp, modalClass, progressUrl,
             jobs:[{shotId, shotNumber, take, kind, mode, imageUri, prompt, width, height, numFrames, seed}]}
    -> ClipResult {clips:[{shotId, take, kind, uri, gpuSeconds, frames, fps, mode, error}], containers, stopped}"""
    video_id = req["videoId"]
    base = (req.get("progressUrl") or "http://127.0.0.1:8080").rstrip("/")
    jobs = req["jobs"]
    wan = modal.Cls.from_name(req.get("modalApp") or "projectv-wan", req.get("modalClass") or "Wan")()
    progress = modal.Dict.from_name("projectv-progress", create_if_missing=True)

    pending: dict[str, tuple[dict, str, object]] = {}
    for j in jobs:
        key = f"{video_id[:8]}-{j['shotId'][:8]}-{j['kind']}{j['take']}"
        payload = {"id": key, "image": get_bytes(j["imageUri"]), "prompt": j["prompt"], "width": j["width"],
                   "height": j["height"], "numFrames": j["numFrames"], "seed": j["seed"], "mode": j["mode"]}
        pending[j["shotId"]] = (j, key, wan.generate_one.spawn(payload))
    log.info("Started %d %s clips for video %s on %s", len(jobs), jobs[0]["kind"] if jobs else "", video_id,
             req.get("modalApp"))

    clips: list[dict] = []
    stopped = False
    try:
        while pending:
            if _stop_requested(base, video_id):
                stopped = True
                for shot_id, (j, key, call) in list(pending.items()):
                    try:
                        call.cancel()
                    except Exception as e:  # noqa: BLE001
                        log.warning("cancel %s failed: %s", key, e)
                    clips.append({"shotId": shot_id, "take": j["take"], "kind": j["kind"], "uri": None, "gpuSeconds": 0,
                                  "frames": 0, "fps": 0, "mode": j["mode"], "error": "stopped"})
                pending.clear()
                break
            for shot_id, (j, key, call) in list(pending.items()):
                try:
                    r = call.get(timeout=0)
                except Exception as e:  # noqa: BLE001
                    if _not_ready(e):
                        continue
                    r = {"mp4": None, "seconds": 0, "error": f"{type(e).__name__}: {e}"[:600]}
                del pending[shot_id]
                uri = None
                if r.get("mp4"):
                    uri = put_bytes(f"videos/{video_id}/clips/{j['kind']}/shot{j['shotNumber']:03d}-take{j['take']}.mp4",
                                    r["mp4"], "video/mp4")
                clips.append({"shotId": shot_id, "take": j["take"], "kind": j["kind"], "uri": uri,
                              "gpuSeconds": round(float(r.get("seconds") or 0), 1), "frames": int(r.get("frames") or 0),
                              "fps": int(r.get("fps") or 0), "mode": r.get("mode") or j["mode"], "error": r.get("error")})
                log.info("Clip shot %s %s: %s", j["shotNumber"], j["kind"], "saved" if uri else r.get("error"))
            snapshot = []
            for shot_id, (j, key, _) in pending.items():
                p = progress.get(key) or {"state": "queued"}
                snapshot.append({"shotId": shot_id, "shotNumber": j["shotNumber"], "kind": j["kind"], **p})
            for c in clips:
                snapshot.append({"shotId": c["shotId"], "kind": c["kind"],
                                 "state": "done" if c["uri"] else "stopped" if c["error"] == "stopped" else "failed"})
            _post(f"{base}/api/internal/clips/progress", {"videoId": video_id, "clips": snapshot})
            _beat({"done": len(clips), "total": len(jobs)})
            if pending:
                time.sleep(POLL_SECONDS)
    except BaseException:
        # Activity cancelled or the worker is shutting down: never leave GPU calls running.
        for _, key, call in pending.values():
            try:
                call.cancel()
            except Exception:  # noqa: BLE001
                pass
        raise
    _post(f"{base}/api/internal/clips/progress", {"videoId": video_id, "clips": [], "finished": True})
    return {"clips": clips, "containers": min(4, len(jobs)), "stopped": stopped}
