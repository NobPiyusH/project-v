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
            "fps": int(r.get("fps") or 0), "mode": r.get("mode") or j["mode"], "error": r.get("error"),
            "coldStart": r.get("coldStart")}


def _stopped(j: dict, error: str = "stopped") -> dict:
    return {"shotId": j["shotId"], "take": j["take"], "kind": j["kind"], "uri": None, "gpuSeconds": 0, "frames": 0,
            "fps": 0, "mode": j["mode"], "error": error}


def _progress_of(progress, key: str) -> dict | None:
    """A clip's live state from the Modal Dict; None when Modal can't be reached for a moment. A short internet drop
    must never fail the whole batch (19 Nov: one failed read failed the step while the GPU kept going)."""
    try:
        return progress.get(key)
    except Exception as e:  # noqa: BLE001 - transient network trouble; the next poll tries again
        log.warning("Clip progress unavailable for a moment (%s)", type(e).__name__)
        return None


def _set_stop(progress, stop_key: str, tries: int = 24, wait: float = 5.0) -> bool:
    """Raise the stop flag the GPU container checks before each clip, retrying while the internet is down (up to
    ~2 min) so a failed or cancelled batch never leaves a GPU rendering clips nobody will collect (rule 8)."""
    for n in range(tries):
        try:
            progress[stop_key] = True
            return True
        except Exception as e:  # noqa: BLE001
            if n == tries - 1:
                log.error("Could not tell the GPU to stop (%s): check Modal for a running projectv-wan container", e)
                return False
            time.sleep(wait)
    return False


def _report(base: str, video_id: str, waiting: list, clips: list[dict], progress, total: int) -> None:
    snapshot = []
    for j, key in waiting:
        p = _progress_of(progress, key) or {"state": "queued"}
        snapshot.append({"shotId": j["shotId"], "shotNumber": j["shotNumber"], "kind": j["kind"], **p})
    for c in clips:
        snapshot.append({"shotId": c["shotId"], "kind": c["kind"],
                         "state": "done" if c["uri"] else "stopped" if c["error"] == "stopped" else "failed"})
    _post(f"{base}/api/internal/clips/progress", {"videoId": video_id, "clips": snapshot})
    _beat({"done": len(clips), "total": total})


class ChainBroken(RuntimeError):
    """A chained clip can't start because the shot before it failed in this batch."""


CLEAN_FILTER = "hqdn3d=1.2:1.2:2:2,unsharp=5:5:0.5:5:5:0"  # locked scene: undo a little of the video compression softness


def last_frame_png(mp4: bytes, clean: bool = False) -> bytes:
    """The last frame of a clip as a PNG: the start image of the next chained clip (one continuous camera move).
    `clean` (locked scene): a light denoise + sharpen first, so quality does not sag a little more with every link."""
    with workdir("lastframe") as d:
        src, out = Path(d) / "prev.mp4", Path(d) / "last.png"
        src.write_bytes(mp4)
        # -update 1 overwrites the one output file with every frame, so the file left is the last frame
        vf = ["-vf", CLEAN_FILTER] if clean else []
        run_ffmpeg(["-sseof", "-1", "-i", str(src), *vf, "-update", "1", "-frames:v", "100000", "-q:v", "1", str(out)])
        return out.read_bytes()


def continues(shot_number: int, chain: bool, group: int = 0) -> bool:
    """Whether shot `shot_number` (1-based) starts from the last frame of the shot before it. group 0 = one chain through
    the whole Short; N = chains of N shots (2: shots 1+2 chained, 3+4 chained, ...)."""
    return chain and shot_number > 1 and (group <= 0 or (shot_number - 1) % group != 0)


def start_image(j: dict, chain: bool, made: dict[int, bytes], failed: set[int], group: int = 0, clean: bool = False) -> bytes:
    """The image clip `j` starts from. The first shot (and any unchained clip): its own picture. A chained shot after the
    first: the last frame of the shot before it (made in this batch, or the clip the control plane named in chainFromUri
    when that shot is not part of the batch). Never a silent fallback to an unrelated picture (it would show as a jump):
    when the last frame can't be had the clip fails and waits for the owner (ChainBroken)."""
    if continues(j["shotNumber"], chain, group):
        before = j["shotNumber"] - 1
        if before in failed:
            raise ChainBroken(f"shot {before} has no clip, so shot {j['shotNumber']} can't continue from it; redo shot {before} first")
        mp4 = made.get(before)
        if mp4 is None and j.get("chainFromUri"):
            mp4 = get_bytes(j["chainFromUri"])
        if mp4 is None:
            raise ChainBroken(f"shot {before} has no clip yet, so shot {j['shotNumber']} can't continue from it")
        try:
            return last_frame_png(mp4, clean)
        except Exception as e:  # noqa: BLE001
            raise ChainBroken(f"the last frame of shot {before} could not be read ({e}); retry or redo shot {before}") from e
    return get_bytes(j["imageUri"])


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
                _set_stop(progress, stop_key)
            _report(base, video_id, [(j, key) for j, key, _ in jobs if key not in done], clips, progress, len(jobs))
    except BaseException:
        _set_stop(progress, stop_key)  # cancelled or failed: the container stops after the clip in hand and scales down
        raise
    for j, key, _ in jobs:  # anything the stream never returned
        if key not in done:
            clips.append(_stopped(j) if stopped else _stopped(j, failure or "the GPU stream ended before this clip"))
    return clips, stopped


def _chained(wan, progress, base: str, video_id: str, jobs: list, chain_flag: bool = True, group: int = 0, clean: bool = False) -> tuple[list[dict], bool]:
    """Chained clips on Modal: one call per clip, in order, each starting from the last frame of the one before. The
    GPU container stays warm between the calls (a cold start is reported per clip, so the ledger books it right)."""
    clips: list[dict] = []
    made: dict[int, bytes] = {}
    failed: set[int] = set()
    for i, (j, key, payload) in enumerate(jobs):
        if _stop_requested(base, video_id):
            clips += [_stopped(x) for x, _, _ in jobs[i:]]
            return clips, True
        _report(base, video_id, [(x, k) for x, k, _ in jobs[i:]], clips, progress, len(jobs))
        try:
            payload = {**payload, "image": start_image(j, chain_flag, made, failed, group, clean)}
            r = wan.generate_one.remote(payload)
        except Exception as e:  # noqa: BLE001 - one failed clip never fails the batch
            r = {"mp4": None, "seconds": 0, "error": f"{type(e).__name__}: {e}"[:600]}
        made = {j["shotNumber"]: r["mp4"]} if r.get("mp4") else {}
        if not r.get("mp4"):
            failed.add(j["shotNumber"])
        clips.append(_clip(video_id, j, r))
    return clips, False


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


def _pod(req: dict, base: str, video_id: str) -> tuple[list[dict], bool]:
    """Clips on the Short's rented GPU pod (control plane created it; gpuUrl is its gateway). One clip at a
    time on one GPU; a lost instance is waited for and the clip resubmitted (workers/pod/client.py). The owner's
    Stop is honoured between clips; the group itself is deleted by the control plane, never left running here."""
    from workers.pod import client as podc

    url = (req.get("gpuUrl") or "").rstrip("/")
    if not url:
        raise RuntimeError("GPU pod route: the control plane sent no container group URL")
    ready_s = float(req.get("readyTimeoutSeconds") or 1800)
    beat = podc.lease_beat(base, video_id, _beat)
    clips: list[dict] = []
    jobs = [(j, f"{j['shotId'][:8]}-{j['kind']}{j['take']}") for j in req["jobs"]]
    chain = bool(req.get("chain"))  # clips already run in turn on the pod; chaining feeds each one's last frame to the next
    made: dict[int, bytes] = {}
    failed: set[int] = set()
    for i, (j, _) in enumerate(jobs):
        if _stop_requested(base, video_id):
            clips += [_stopped(x) for x, _ in jobs[i:]]
            return clips, True
        _report(base, video_id, jobs[i:], clips, {}, len(jobs))
        fps = 16
        try:
            image = start_image(j, chain, made, failed, int(req.get("chainGroup") or 0), bool(req.get("locked")))
        except Exception as e:  # noqa: BLE001 - ChainBroken or an unreadable picture fails this clip only
            failed.add(j["shotNumber"])
            clips.append(_clip(video_id, j, {"mp4": None, "seconds": 0, "error": f"{type(e).__name__}: {e}"[:600]}))
            continue
        payload = {"image": base64.b64encode(image).decode(), "prompt": j["prompt"],
                   "negative": j.get("negative"), "seed": int(j["seed"]), "frames": int(j["numFrames"]), "fps": fps,
                   "width": int(j["width"]), "height": int(j["height"])}
        try:
            mp4, info = podc.run(url, "clip", payload, ready_s, beat=beat)
            r = {"mp4": mp4, "seconds": info.get("seconds") or 0, "frames": info.get("frames"), "fps": info.get("fps"),
                 "mode": j["mode"], "coldStart": i == 0}
        except Exception as e:  # noqa: BLE001 - one failed clip never fails the batch
            r = {"mp4": None, "seconds": 0, "error": f"{type(e).__name__}: {e}"[:600]}
        if chain:
            made = {j["shotNumber"]: r["mp4"]} if r.get("mp4") else {}
            if not r.get("mp4"):
                failed.add(j["shotNumber"])
        clips.append(_clip(video_id, j, r))
    return clips, False


HOLD_MAX_SECONDS = 300  # a held GPU stays warm for minutes, never hours (rule 8)


def hold_seconds(req: dict, containers: int) -> int:
    """How long the one GPU should stay warm after this batch: only when the AI Producer may redo clips by itself
    (holdSeconds > 0) and the batch runs on one container. Every call for that Short uses the same value, so its redo
    lands on the same warm container (a with_options variant autoscales on its own)."""
    hold = int(req.get("holdSeconds") or 0)
    return min(hold, HOLD_MAX_SECONDS) if hold > 0 and containers == 1 else 0


def _wan(req: dict, containers: int):
    cls = modal.Cls.from_name(req.get("modalApp") or "projectv-wan", req.get("modalClass") or "Wan")
    hold = hold_seconds(req, containers)
    if hold:
        log.info("Holding the GPU warm for %d s after this batch (AI Producer may redo)", hold)
        cls = cls.with_options(scaledown_window=hold)
    return cls()


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
    if req.get("provider") == "pod":
        log.info("Starting %d clips for video %s on a rented pod", len(req["jobs"]), video_id)
        clips, stopped = _pod(req, base, video_id)
        _post(f"{base}/api/internal/clips/progress", {"videoId": video_id, "clips": [], "finished": True})
        return {"clips": clips, "containers": 1, "stopped": stopped, "coldStarts": 0}
    progress = modal.Dict.from_name("projectv-progress", create_if_missing=True)
    chain = bool(req.get("chain"))
    containers = 1 if chain else max(1, min(4, int(req.get("containers") or 4), len(req["jobs"]) or 1))
    wan = _wan(req, containers)
    jobs = []
    for j in req["jobs"]:
        key = f"{video_id[:8]}-{j['shotId'][:8]}-{j['kind']}{j['take']}"
        jobs.append((j, key, {"id": key, "image": get_bytes(j["imageUri"]) if j.get("imageUri") else None, "prompt": j["prompt"], "width": j["width"],
                              "height": j["height"], "numFrames": j["numFrames"], "seed": j["seed"], "mode": j["mode"],
                              "negative": j.get("negative")}))
    log.info("Starting %d clips for video %s on %d GPU container(s)", len(jobs), video_id, containers)
    run = (lambda *a: _chained(*a, group=int(req.get("chainGroup") or 0), clean=bool(req.get("locked")))) if chain else _sequential if containers == 1 else _parallel
    clips, stopped = run(wan, progress, base, video_id, jobs)
    _post(f"{base}/api/internal/clips/progress", {"videoId": video_id, "clips": [], "finished": True})
    return {"clips": clips, "containers": containers if jobs else 0, "stopped": stopped, "coldStarts": cold_starts(clips)}


def cold_starts(clips: list[dict]) -> int | None:
    """GPU start-ups this batch really paid: clips that were the first in a fresh container. None when the deployed
    app does not report it yet (the ledger then books one per container, as before)."""
    flags = [c.get("coldStart") for c in clips if c.get("coldStart") is not None]
    return sum(1 for f in flags if f) if flags else None
