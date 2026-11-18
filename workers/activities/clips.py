"""M2b: moving video per shot with Wan 2.2 image-to-video on Modal (app projectv-wan).

Two ways to run a batch:
- one container (drafts): the clips render one after another on ONE GPU and stream back one by one, so the
  ~2 min container start + model load is paid once per batch;
- parallel (finals): one call per clip on up to 4 GPUs, because a final takes ~8 min and wall time matters.

Either way every clip is saved to object storage the moment it is finished (a stop or a crash never loses finished
clips), per-clip progress goes to the control plane for the dashboard, and the owner's Stop is honoured: the
remaining work is cancelled so no GPU keeps running (CLAUDE.md rule 8).
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.request
import uuid
import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import modal
from temporalio import activity

from workers import gemini_pool
from workers.common import beat as _beat, gemini, get_bytes, log, put_bytes, put_file, run_ffmpeg, workdir

POLL_SECONDS = 5


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


def _clip(video_id: str, j: dict, r: dict) -> dict:
    """Saves a finished clip to object storage at once (a later stop or crash can't lose it) and describes it."""
    uri = None
    if r.get("mp4"):
        uri = put_bytes(f"videos/{video_id}/clips/{j['kind']}/shot{j['shotNumber']:03d}-take{j['take']}.mp4",
                        r["mp4"], "video/mp4")
    log.info("Clip shot %s %s: %s", j["shotNumber"], j["kind"], "saved" if uri else r.get("error"))
    return {"shotId": j["shotId"], "take": j["take"], "kind": j["kind"], "uri": uri,
            "gpuSeconds": round(float(r.get("seconds") or 0), 1), "frames": int(r.get("frames") or 0),
            "fps": int(r.get("fps") or 0), "mode": r.get("mode") or j["mode"], "error": r.get("error")}


def _stopped(j: dict, error: str = "stopped") -> dict:
    return {"shotId": j["shotId"], "take": j["take"], "kind": j["kind"], "uri": None, "gpuSeconds": 0, "frames": 0,
            "fps": 0, "mode": j["mode"], "error": error}


def _report(base: str, video_id: str, waiting: list, clips: list[dict], progress, total: int) -> None:
    snapshot = []
    for j, key in waiting:
        p = progress.get(key) or {"state": "queued"}
        snapshot.append({"shotId": j["shotId"], "shotNumber": j["shotNumber"], "kind": j["kind"], **p})
    for c in clips:
        snapshot.append({"shotId": c["shotId"], "kind": c["kind"],
                         "state": "done" if c["uri"] else "stopped" if c["error"] == "stopped" else "failed"})
    _post(f"{base}/api/internal/clips/progress", {"videoId": video_id, "clips": snapshot})
    _beat({"done": len(clips), "total": total})


def _parallel(wan, progress, base: str, video_id: str, jobs: list) -> tuple[list[dict], bool]:
    """One Modal call per clip on up to 4 GPUs."""
    pending = {key: (j, key, wan.generate_one.spawn(payload)) for j, key, payload in jobs}  # one entry per take
    clips: list[dict] = []
    try:
        while pending:
            if _stop_requested(base, video_id):
                for j, key, call in pending.values():
                    try:
                        call.cancel()
                    except Exception as e:  # noqa: BLE001
                        log.warning("cancel %s failed: %s", key, e)
                    clips.append(_stopped(j))
                return clips, True
            for pkey, (j, key, call) in list(pending.items()):
                try:
                    r = call.get(timeout=0)
                except Exception as e:  # noqa: BLE001
                    if _not_ready(e):
                        continue
                    r = {"mp4": None, "seconds": 0, "error": f"{type(e).__name__}: {e}"[:600]}
                del pending[pkey]
                clips.append(_clip(video_id, j, r))
            _report(base, video_id, [(j, key) for j, key, _ in pending.values()], clips, progress, len(jobs))
            if pending:
                time.sleep(POLL_SECONDS)
    except BaseException:
        # Activity cancelled or the worker is shutting down: never leave GPU calls running.
        for _, _, call in pending.values():
            try:
                call.cancel()
            except Exception:  # noqa: BLE001
                pass
        raise
    return clips, False


def _sequential(wan, progress, base: str, video_id: str, jobs: list) -> tuple[list[dict], bool]:
    """All clips in ONE container, streamed back one by one. Stop sets a flag the container checks before each
    clip; the clip being rendered finishes (seconds, for a draft) and the container then scales down."""
    stop_key = f"stop-{video_id[:8]}-{uuid.uuid4().hex[:8]}"
    by_key = {key: j for j, key, _ in jobs}
    results: queue.Queue = queue.Queue()

    def consume():
        try:
            for r in wan.generate_stream.remote_gen([payload for _, _, payload in jobs], stop_key):
                results.put(("clip", r))
        except BaseException as e:  # noqa: BLE001
            results.put(("error", e))
        finally:
            results.put(("end", None))

    threading.Thread(target=consume, daemon=True, name="wan-stream").start()
    clips: list[dict] = []
    done: set[str] = set()
    stopped = False
    failure = None
    try:
        while True:
            try:
                kind, r = results.get(timeout=POLL_SECONDS)
            except queue.Empty:
                kind, r = None, None
            if kind == "clip":
                key = r.get("id")
                if key in by_key and key not in done:
                    done.add(key)
                    clips.append(_stopped(by_key[key]) if r.get("error") == "stopped" else _clip(video_id, by_key[key], r))
                continue  # drain quickly; report at the next quiet moment
            if kind == "error":
                failure = f"{type(r).__name__}: {r}"[:600]
                continue
            if kind == "end":
                break
            if not stopped and _stop_requested(base, video_id):
                stopped = True
                progress[stop_key] = True
            _report(base, video_id, [(j, key) for j, key, _ in jobs if key not in done], clips, progress, len(jobs))
    except BaseException:
        progress[stop_key] = True  # cancelled: the container stops after the clip in hand and scales down
        raise
    for j, key, _ in jobs:  # anything the stream never returned
        if key not in done:
            clips.append(_stopped(j) if stopped else _stopped(j, failure or "the GPU stream ended before this clip"))
    return clips, stopped


OMNI_WORKERS = 3  # clips in flight at once on Gemini Omni (each ~40 s)


def _omni_one(model: str, video_id: str, j: dict, resolution: str) -> dict:
    """One clip on Gemini Omni Flash (Interactions API), first frame = the approved keyframe. Runs through the
    multi-project failover; the clip's own audio track is dropped (narration, music and sound are mixed later)."""
    seconds = max(3, min(10, round(j["numFrames"] / 24)))
    aspect = "9:16" if j["height"] > j["width"] else "16:9"
    text = (f"Animate this image as a {seconds}-second {aspect} cinematic shot, starting exactly on this frame. "
            f"{j['prompt']}. No text, no dialogue, no music.")
    started = time.time()

    def call():
        return gemini().interactions.create(
            api_version="v1beta", model=model,
            input=[{"type": "image", "data": base64.b64encode(get_bytes(j["imageUri"])).decode(), "mime_type": "image/png"},
                   {"type": "text", "text": text}],
            response_format={"type": "video", "duration": f"{seconds}s", "resolution": resolution, "aspect_ratio": aspect})
    try:
        r = gemini_pool.execute(call, f"omni clip shot {j['shotNumber']}")
        video = getattr(r, "output_video", None)
        if not video or not video.data:
            raise RuntimeError("Gemini Omni returned no video")
        usage = getattr(r, "usage", None)
        tokens = int(getattr(usage, "total_output_tokens", 0) or 0)
        with workdir("omni") as d:
            raw, silent = Path(d) / "raw.mp4", Path(d) / "clip.mp4"
            raw.write_bytes(base64.b64decode(video.data))
            run_ffmpeg(["-i", str(raw), "-an", "-c:v", "copy", str(silent)])
            uri = put_file(f"videos/{video_id}/clips/{j['kind']}/shot{j['shotNumber']:03d}-take{j['take']}.mp4",
                           silent, "video/mp4")
        log.info("Omni clip shot %s %s saved (%.0f s, %d tokens)", j["shotNumber"], j["kind"], time.time() - started, tokens)
        return {"shotId": j["shotId"], "take": j["take"], "kind": j["kind"], "uri": uri, "gpuSeconds": 0,
                "frames": seconds * 24, "fps": 24, "mode": "omni", "error": None, "outputTokens": tokens}
    except Exception as e:  # noqa: BLE001 - one failed clip never fails the batch
        log.warning("Omni clip shot %s failed: %s", j["shotNumber"], e)
        return {**_stopped(j, f"{type(e).__name__}: {e}"[:600]), "outputTokens": 0}


def _omni(req: dict, base: str, video_id: str) -> tuple[list[dict], bool]:
    """Clips on Gemini Omni: a few in flight at once; the owner's Stop cancels the clips not yet started."""
    model, resolution = req.get("omniModel") or "gemini-omni-1.2-flash", req.get("resolution") or "720p"
    jobs = [(j, f"{j['shotId'][:8]}-{j['kind']}{j['take']}") for j in req["jobs"]]
    clips: list[dict] = []
    stopped = False
    with ThreadPoolExecutor(OMNI_WORKERS, thread_name_prefix="omni") as pool:
        pending = {pool.submit(_omni_one, model, video_id, j, resolution): (j, key) for j, key in jobs}
        while pending:
            for f in [f for f in pending if f.done()]:
                pending.pop(f)
                clips.append(f.result())
            if not stopped and _stop_requested(base, video_id):
                stopped = True
                for f, (j, _) in list(pending.items()):
                    if f.cancel():
                        pending.pop(f)
                        clips.append({**_stopped(j), "outputTokens": 0})
            _report(base, video_id, list(pending.values()), clips, {}, len(jobs))
            if pending:
                time.sleep(POLL_SECONDS)
    return clips, stopped


@activity.defn(name="generate_clips")
def generate_clips(req: dict) -> dict:
    """req: ClipRequest {videoId, modalApp, modalClass, progressUrl, containers,
             jobs:[{shotId, shotNumber, take, kind, mode, imageUri, prompt, width, height, numFrames, seed}]}
    -> ClipResult {clips:[{shotId, take, kind, uri, gpuSeconds, frames, fps, mode, error}], containers, stopped}"""
    video_id = req["videoId"]
    base = (req.get("progressUrl") or "http://127.0.0.1:8080").rstrip("/")
    if req.get("provider") == "omni":
        log.info("Starting %d clips for video %s on Gemini Omni", len(req["jobs"]), video_id)
        clips, stopped = _omni(req, base, video_id)
        _post(f"{base}/api/internal/clips/progress", {"videoId": video_id, "clips": [], "finished": True})
        return {"clips": clips, "containers": 0, "stopped": stopped}
    wan = modal.Cls.from_name(req.get("modalApp") or "projectv-wan", req.get("modalClass") or "Wan")()
    progress = modal.Dict.from_name("projectv-progress", create_if_missing=True)
    containers = max(1, min(4, int(req.get("containers") or 4), len(req["jobs"]) or 1))
    jobs = []
    for j in req["jobs"]:
        key = f"{video_id[:8]}-{j['shotId'][:8]}-{j['kind']}{j['take']}"
        jobs.append((j, key, {"id": key, "image": get_bytes(j["imageUri"]), "prompt": j["prompt"], "width": j["width"],
                              "height": j["height"], "numFrames": j["numFrames"], "seed": j["seed"], "mode": j["mode"]}))
    log.info("Starting %d clips for video %s on %d GPU container(s)", len(jobs), video_id, containers)
    run = _sequential if containers == 1 else _parallel
    clips, stopped = run(wan, progress, base, video_id, jobs)
    _post(f"{base}/api/internal/clips/progress", {"videoId": video_id, "clips": [], "finished": True})
    return {"clips": clips, "containers": containers if jobs else 0, "stopped": stopped}
