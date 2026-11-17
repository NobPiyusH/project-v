"""Finishing (M2c part 2a): smooth motion. Sends a video's generated clips to the projectv-finish Modal app (one L4,
clips in turn), which re-times each 16 fps Wan clip to a true 24 fps with Practical-RIFE in-between frames. Every
smoothed clip is saved the moment it arrives; the owner's Stop is honoured between clips (CLAUDE.md rule 8).
"""

from __future__ import annotations

import queue
import threading
import uuid

import modal
from temporalio import activity

from workers.activities.clips import POLL_SECONDS, _post, _stop_requested
from workers.common import get_bytes, log, put_bytes


def _beat(detail) -> None:
    try:
        activity.heartbeat(detail)
    except RuntimeError:
        pass


@activity.defn(name="smooth_clips")
def smooth_clips(req: dict) -> dict:
    """req: {videoId, progressUrl, dstFps, jobs:[{shotId, shotNumber, sourceAssetId, uri, srcFps}]}
    -> {clips:[{shotId, sourceAssetId, uri, gpuSeconds, frames, fps, error}], containers, stopped}"""
    video_id = req["videoId"]
    base = (req.get("progressUrl") or "http://127.0.0.1:8080").rstrip("/")
    jobs = req["jobs"]
    if not jobs:
        return {"clips": [], "containers": 0, "stopped": False}
    smooth = modal.Cls.from_name("projectv-finish", "Smooth")()
    progress = modal.Dict.from_name("projectv-progress", create_if_missing=True)
    stop_key = f"stop-smooth-{video_id[:8]}-{uuid.uuid4().hex[:8]}"
    payloads, by_key = [], {}
    for j in jobs:
        key = f"{video_id[:8]}-{j['shotId'][:8]}-smooth"
        by_key[key] = j
        payloads.append({"id": key, "mp4": get_bytes(j["uri"]), "srcFps": j.get("srcFps") or 16,
                         "dstFps": req.get("dstFps") or 24})
    results: queue.Queue = queue.Queue()

    def consume():
        try:
            for r in smooth.smooth_stream.remote_gen(payloads, stop_key):
                results.put(("clip", r))
        except BaseException as e:  # noqa: BLE001
            results.put(("error", e))
        finally:
            results.put(("end", None))

    threading.Thread(target=consume, daemon=True, name="rife-stream").start()
    clips, done, stopped, failure = [], set(), False, None
    try:
        while True:
            try:
                kind, r = results.get(timeout=POLL_SECONDS)
            except queue.Empty:
                kind, r = None, None
            if kind == "clip":
                j = by_key.get(r.get("id"))
                if j and r["id"] not in done:
                    done.add(r["id"])
                    uri = None
                    if r.get("mp4"):
                        uri = put_bytes(f"videos/{video_id}/clips/smooth/shot{j['shotNumber']:03d}-"
                                        f"{j['sourceAssetId'][:8]}.mp4", r["mp4"], "video/mp4")
                    clips.append({"shotId": j["shotId"], "sourceAssetId": j["sourceAssetId"], "uri": uri,
                                  "gpuSeconds": float(r.get("seconds") or 0), "frames": int(r.get("frames") or 0),
                                  "fps": float(r.get("fps") or 0), "error": r.get("error")})
                    log.info("Smoothed shot %s: %s", j["shotNumber"], "saved" if uri else r.get("error"))
                continue
            if kind == "error":
                failure = f"{type(r).__name__}: {r}"[:500]
                continue
            if kind == "end":
                break
            if not stopped and _stop_requested(base, video_id):
                stopped = True
                progress[stop_key] = True
            _post(f"{base}/api/internal/clips/progress", {"videoId": video_id, "clips": [
                {"shotId": j["shotId"], "shotNumber": j["shotNumber"], "kind": "smooth",
                 **(progress.get(k) or {"state": "queued"})} for k, j in by_key.items() if k not in done]})
            _beat({"done": len(done), "total": len(jobs)})
    except BaseException:
        progress[stop_key] = True
        raise
    for k, j in by_key.items():
        if k not in done:
            clips.append({"shotId": j["shotId"], "sourceAssetId": j["sourceAssetId"], "uri": None, "gpuSeconds": 0,
                          "frames": 0, "fps": 0, "error": "stopped" if stopped else failure or "the GPU stream ended early"})
    _post(f"{base}/api/internal/clips/progress", {"videoId": video_id, "clips": [], "finished": True})
    return {"clips": clips, "containers": 1, "stopped": stopped}
