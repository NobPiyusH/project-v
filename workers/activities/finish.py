"""Finishing (M2c part 2): smooth motion and sharpness on Modal GPUs.

- smooth_clips: the projectv-finish app (one L4) re-times each 16 fps Wan clip to a true 24 fps with Practical-RIFE.
- upscale_clips: the projectv-upscale app (one H100) restores each clip to a crisp 1080p with SeedVR2 7B.

Every finished clip is saved the moment it arrives; the owner's Stop is honoured between clips (smooth) or groups of
clips (upscale) (CLAUDE.md rule 8).
"""

from __future__ import annotations

import contextvars
import json
import os
import queue
import subprocess
import threading
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path

import modal
from temporalio import activity

from workers.activities.clips import POLL_SECONDS, _post, _stop_requested
from workers.common import BUCKET, exists, ffprobe_bin, get_bytes, log, put_bytes, put_file, workdir
from workers.common import beat as _beat

HEARTBEAT_SECONDS = 15


class _Heartbeat:
    """Heartbeats from its OWN thread every 15 s (18 Nov 2026 fix): the progress loop makes network calls that can
    stall on a slow or dropped connection, and a late heartbeat made Temporal kill a healthy upscale twice. The
    thread runs in a copy of the activity's context, so activity.heartbeat works there."""

    def __init__(self):
        self.detail: dict = {}
        self._stop = threading.Event()
        ctx = contextvars.copy_context()
        self._thread = threading.Thread(target=lambda: ctx.run(self._loop), daemon=True, name="heartbeat")

    def _loop(self):
        while not self._stop.wait(HEARTBEAT_SECONDS):
            _beat(self.detail)

    def __enter__(self):
        _beat(self.detail)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()


def _out_key(video_id: str, folder: str, j: dict) -> str:
    return f"videos/{video_id}/clips/{folder}/shot{j['shotNumber']:03d}-{j['sourceAssetId'][:8]}.mp4"


def _probe(key: str) -> tuple[int, float]:
    """(frames, fps) of a stored clip, for clips reused on a retry."""
    with workdir("probe") as tmp:
        path = Path(tmp) / "clip.mp4"
        path.write_bytes(get_bytes(f"s3://{BUCKET}/{key}"))
        out = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=r_frame_rate:format=duration", "-of", "json", str(path)],
                             capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    num, den = (d["streams"][0]["r_frame_rate"].split("/") + ["1"])[:2]
    fps = float(num) / float(den or 1)
    return round(float(d["format"]["duration"]) * fps), fps


def _already_done(video_id: str, folder: str, jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Resume (18 Nov 2026 fix): clips this batch already saved on an earlier attempt are reused, not re-rendered,
    so a crash or retry loses at most the clip in progress. Returns (reused results, jobs still to do)."""
    reused, todo = [], []
    for j in jobs:
        key = _out_key(video_id, folder, j)
        if exists(key):
            try:
                frames, fps = _probe(key)
                reused.append({"shotId": j["shotId"], "sourceAssetId": j["sourceAssetId"], "uri": f"s3://{BUCKET}/{key}",
                               "gpuSeconds": 0, "frames": frames, "fps": fps, "error": None})
                log.info("%s shot %s: reusing the clip saved on an earlier attempt", folder, j["shotNumber"])
                continue
            except Exception as e:  # noqa: BLE001 - unreadable leftover: redo it
                log.warning("could not reuse %s (%s); redoing it", key, e)
        todo.append(j)
    return reused, todo


def _stream(req: dict, kind: str, folder: str, payload: Callable[[dict], dict],
            start: Callable[[list[dict], str], Iterable[dict]]) -> dict:
    """Shared driver: send every job's clip to a GPU generator, save each result as it arrives, report progress,
    honour Stop. -> {clips:[{shotId, sourceAssetId, uri, gpuSeconds, frames, fps, error}], containers, stopped}"""
    video_id = req["videoId"]
    base = (req.get("progressUrl") or "http://127.0.0.1:8080").rstrip("/")
    jobs = req["jobs"]
    if not jobs:
        return {"clips": [], "containers": 0, "stopped": False}
    reused, jobs = _already_done(video_id, folder, jobs)
    if not jobs:
        _post(f"{base}/api/internal/clips/progress", {"videoId": video_id, "clips": [], "finished": True})
        return {"clips": reused, "containers": 0, "stopped": False}
    with _Heartbeat() as hb:
        result = _stream_jobs(req, kind, folder, payload, start, jobs, hb)
    result["clips"] = reused + result["clips"]
    return result


def _stream_jobs(req: dict, kind: str, folder: str, payload, start, jobs: list[dict], hb: _Heartbeat) -> dict:
    video_id = req["videoId"]
    base = (req.get("progressUrl") or "http://127.0.0.1:8080").rstrip("/")
    progress = modal.Dict.from_name("projectv-progress", create_if_missing=True)
    stop_key = f"stop-{kind}-{video_id[:8]}-{uuid.uuid4().hex[:8]}"
    payloads, by_key = [], {}
    for j in jobs:
        key = f"{video_id[:8]}-{j['shotId'][:8]}-{kind}"
        by_key[key] = j
        payloads.append({"id": key, "mp4": get_bytes(j["uri"]), **payload(j)})
    results: queue.Queue = queue.Queue()

    def consume():
        try:
            for r in start(payloads, stop_key):
                results.put(("clip", r))
        except BaseException as e:  # noqa: BLE001
            results.put(("error", e))
        finally:
            results.put(("end", None))

    threading.Thread(target=consume, daemon=True, name=f"{kind}-stream").start()
    clips, done, stopped, failure = [], set(), False, None
    try:
        while True:
            try:
                what, r = results.get(timeout=POLL_SECONDS)
            except queue.Empty:
                what, r = None, None
            if what == "clip":
                j = by_key.get(r.get("id"))
                if j and r["id"] not in done:
                    done.add(r["id"])
                    uri = None
                    if r.get("mp4"):
                        uri = put_bytes(_out_key(video_id, folder, j), r["mp4"], "video/mp4")
                    clips.append({"shotId": j["shotId"], "sourceAssetId": j["sourceAssetId"], "uri": uri,
                                  "gpuSeconds": float(r.get("seconds") or 0), "frames": int(r.get("frames") or 0),
                                  "fps": float(r.get("fps") or 0), "error": r.get("error")})
                    log.info("%s shot %s: %s", kind, j["shotNumber"], "saved" if uri else r.get("error"))
                continue
            if what == "error":
                failure = f"{type(r).__name__}: {r}"[:500]
                continue
            if what == "end":
                break
            hb.detail = {"done": len(done), "total": len(jobs)}
            if not stopped and _stop_requested(base, video_id):
                stopped = True
                progress[stop_key] = True
            try:  # progress is best effort: a slow or dropped connection must never fail the batch
                _post(f"{base}/api/internal/clips/progress", {"videoId": video_id, "clips": [
                    {"shotId": j["shotId"], "shotNumber": j["shotNumber"], "kind": kind,
                     **(progress.get(k) or {"state": "queued"})} for k, j in by_key.items() if k not in done]})
            except Exception as e:  # noqa: BLE001
                log.debug("progress snapshot skipped: %s", e)
    except BaseException:
        progress[stop_key] = True
        raise
    for k, j in by_key.items():
        if k not in done:
            clips.append({"shotId": j["shotId"], "sourceAssetId": j["sourceAssetId"], "uri": None, "gpuSeconds": 0,
                          "frames": 0, "fps": 0, "error": "stopped" if stopped else failure or "the GPU stream ended early"})
    _post(f"{base}/api/internal/clips/progress", {"videoId": video_id, "clips": [], "finished": True})
    return {"clips": clips, "containers": 1, "stopped": stopped}


def _smooth_local(req: dict) -> tuple[list[dict], list[dict]]:
    """Smooth on the laptop's GPU (free). Returns (done clips, jobs left for Modal). Any failure hands the rest over."""
    from workers.local import rife

    video_id, done, left = req["videoId"], [], []
    base = (req.get("progressUrl") or "http://127.0.0.1:8080").rstrip("/")
    with workdir("rife") as tmp:
        for n, j in enumerate(req["jobs"]):
            if left or _stop_requested(base, video_id):
                left.append(j)
                continue
            try:
                src, dst = Path(tmp) / f"in{n}.mp4", Path(tmp) / f"out{n}.mp4"
                src.write_bytes(get_bytes(j["uri"]))
                r = rife.smooth(src, dst, float(j.get("srcFps") or 16), float(req.get("dstFps") or 24))
                uri = put_file(f"videos/{video_id}/clips/smooth/shot{j['shotNumber']:03d}-{j['sourceAssetId'][:8]}.mp4",
                               dst, "video/mp4")
                # gpuSeconds 0: the laptop costs nothing, so no Modal charge is recorded for these clips.
                done.append({"shotId": j["shotId"], "sourceAssetId": j["sourceAssetId"], "uri": uri, "gpuSeconds": 0,
                             "frames": r["frames"], "fps": r["fps"], "error": None})
                log.info("smooth shot %s on the laptop GPU in %.1fs", j["shotNumber"], r["seconds"])
            except Exception as e:  # noqa: BLE001 - e.g. out of GPU memory: Modal does the rest
                log.warning("Local RIFE failed on shot %s (%s); Modal takes over", j["shotNumber"], e)
                left.append(j)
            _post(f"{base}/api/internal/clips/progress", {"videoId": video_id, "clips": [
                {"shotId": x["shotId"], "shotNumber": x["shotNumber"], "kind": "smooth", "state": "queued"}
                for x in req["jobs"][n + 1:]]})
            _beat({"done": len(done), "total": len(req["jobs"])})
    return done, left


@activity.defn(name="smooth_clips")
def smooth_clips(req: dict) -> dict:
    """req: {videoId, progressUrl, dstFps, jobs:[{shotId, shotNumber, sourceAssetId, uri, srcFps}]}
    Laptop GPU first (free, PROJECTV_LOCAL_RIFE=0 turns it off), Modal for anything left."""
    if req.get("jobs") and os.getenv("PROJECTV_LOCAL_RIFE", "1") != "0":
        from workers.local import rife
        if rife.available():
            from workers.routing_log import log_decision
            log_decision(req.get("videoId"), "UPSCALE", "interp.rife", "local_laptop",
                         "laptop GPU first: free; Modal L4 takes anything it can't finish",
                         [{"provider": "local_laptop"}, {"provider": "modal_l4"}])
            done, left = _smooth_local(req)
            if not left:
                _post(f"{(req.get('progressUrl') or 'http://127.0.0.1:8080').rstrip('/')}/api/internal/clips/progress",
                      {"videoId": req["videoId"], "clips": [], "finished": True})
                return {"clips": done, "containers": 0, "stopped": False}
            rest = _modal_smooth({**req, "jobs": left})
            return {**rest, "clips": done + rest["clips"]}
    from workers.routing_log import log_decision
    log_decision(req.get("videoId"), "UPSCALE", "interp.rife", "modal_l4",
                 "laptop RIFE off or unavailable", [{"provider": "local_laptop"}, {"provider": "modal_l4"}])
    return _modal_smooth(req)


def _modal_smooth(req: dict) -> dict:
    smooth = modal.Cls.from_name("projectv-finish", "Smooth")()
    return _stream(req, "smooth", "smooth",
                   lambda j: {"srcFps": j.get("srcFps") or 16, "dstFps": req.get("dstFps") or 24},
                   lambda payloads, stop_key: smooth.smooth_stream.remote_gen(payloads, stop_key))


@activity.defn(name="upscale_clips")
def upscale_clips(req: dict) -> dict:
    """req: {videoId, progressUrl, jobs:[{shotId, shotNumber, sourceAssetId, uri}]} (same shape as smooth_clips).
    Clips are restored to a 1080 px short side (1080x1920 for a Short) by SeedVR2 7B."""
    up = modal.Cls.from_name("projectv-upscale", "Upscale")()
    # One batch per clip (no padding): same quality in the A/B of 18 Nov 2026, ~40% less H100 time. Set
    # PROJECTV_SHARPEN_FIT=0 to go back to 61-frame batches.
    fit = os.getenv("PROJECTV_SHARPEN_FIT", "1") != "0"
    return _stream(req, "sharp", "sharp", lambda j: {},
                   lambda payloads, stop_key: up.upscale_stream.remote_gen(payloads, 1080, 61, stop_key, 8, fit))
