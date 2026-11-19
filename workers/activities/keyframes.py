"""Keyframes: one still per shot. The control plane decides the provider per shot (hero -> Nano Banana via the paid
Gemini API, the rest -> Z-Image Turbo on free Modal credits); this activity just executes those decisions."""

from __future__ import annotations

import concurrent.futures
import time
import urllib.request

import modal
from google.genai import types
from temporalio import activity

from workers.common import MEDIA_TIMEOUT_MS, gemini, log, put_bytes, usage_dict, with_models

MODAL_BATCH = 8  # images per Modal call; heartbeat between batches


def _references(query: str) -> tuple[list, list[str]]:
    """Up to 2 licence-safe real photos of the subject (Wikimedia Commons) as image parts, plus their credit lines.
    A failed lookup just means no references: the keyframe is still made."""
    if not query:
        return [], []
    from workers.activities.footage import reference_photos

    parts, credits = [], []
    try:
        for r in reference_photos(query, 2):
            req = urllib.request.Request(r["thumbUrl"], headers={"User-Agent": "projectv-studio/1.0 (private documentary tool)"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                parts.append(types.Part.from_bytes(data=resp.read(), mime_type="image/jpeg"))
            credits.append(f"{r['title']} ({r['author'] or 'Wikimedia Commons'}, {r['licence']})")
    except Exception as e:  # noqa: BLE001
        log.warning("Reference photos for %r unavailable (%s)", query, type(e).__name__)
    return parts, credits


def _nano_banana(req: dict, job: dict) -> dict:
    t = time.time()
    refs, credits = _references(job.get("referenceQuery") or "")
    # Real photos teach the model what the subject really looks like; the composition stays the prompt's own.
    contents = ([*refs, f"The image(s) above are real reference photos of {job['referenceQuery']}. Use them ONLY for "
                 "the subject's true anatomy, shape and colours; do not copy their composition, background or "
                 "lighting.\n\n" + job["prompt"]] if refs else job["prompt"])
    used, response = with_models(req["geminiImageModel"], lambda m: gemini().models.generate_content(
        model=m,
        contents=contents,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=job.get("aspectRatio", "16:9"), image_size=job.get("imageSize", "1K")),
            http_options=types.HttpOptions(timeout=MEDIA_TIMEOUT_MS),
        ),
    ), what=f"image {job['shotId']}")
    for part in response.candidates[0].content.parts:
        if part.inline_data and (part.inline_data.mime_type or "").startswith("image"):
            mime = part.inline_data.mime_type
            ext = "png" if "png" in mime else "jpg"
            uri = put_bytes(f"videos/{req['videoId']}/keyframes/{job['shotId']}-{job['attempt']}.{ext}",
                            part.inline_data.data, mime)
            return {"shotId": job["shotId"], "uri": uri, "provider": "gemini_api", "model": used,
                    "seed": None, "seconds": round(time.time() - t, 2), "usage": usage_dict(response), "error": None,
                    "references": credits}
    raise RuntimeError("Image model returned no image")


def _safe(fn, req, job):
    try:
        return fn(req, job)
    except Exception as e:  # one bad shot must not lose the whole batch
        return {"shotId": job["shotId"], "uri": None, "provider": job["provider"], "error": f"{type(e).__name__}: {e}"[:500]}


@activity.defn(name="generate_keyframes")
def generate_keyframes(req: dict) -> dict:
    """req: {videoId, geminiImageModel, modalApp, modalClass,
             jobs:[{shotId, attempt, provider: gemini_api|modal_zimage, prompt, width, height, aspectRatio, imageSize, seed}]}
    -> {results:[{shotId, uri, provider, model, seed, seconds, usage, error}], modalGpuSeconds}"""
    jobs = req["jobs"]
    results: list[dict] = []

    gem_jobs = [j for j in jobs if j["provider"] == "gemini_api"]
    modal_jobs = [j for j in jobs if j["provider"] == "modal_zimage"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        gem_futures = [pool.submit(_safe, _nano_banana, req, j) for j in gem_jobs]

        modal_seconds = 0.0
        if modal_jobs:
            zimage = modal.Cls.from_name(req.get("modalApp", "projectv-zimage"), req.get("modalClass", "ZImage"))()
            for i in range(0, len(modal_jobs), MODAL_BATCH):
                batch = modal_jobs[i:i + MODAL_BATCH]
                payload = [{"id": j["shotId"], "prompt": j["prompt"], "width": j.get("width", 1344),
                            "height": j.get("height", 768), "seed": j.get("seed", 0)} for j in batch]
                try:
                    out = zimage.generate.remote(payload)
                except Exception as e:
                    results += [{"shotId": j["shotId"], "uri": None, "provider": "modal_zimage",
                                 "error": f"{type(e).__name__}: {e}"[:500]} for j in batch]
                    continue
                by_id = {j["shotId"]: j for j in batch}
                for r in out:
                    j = by_id[r["id"]]
                    uri = put_bytes(f"videos/{req['videoId']}/keyframes/{j['shotId']}-{j['attempt']}.png", r["png"], "image/png")
                    modal_seconds += r["seconds"]
                    results.append({"shotId": j["shotId"], "uri": uri, "provider": "modal_zimage",
                                    "model": "Tongyi-MAI/Z-Image-Turbo", "seed": r["seed"],
                                    "seconds": round(r["seconds"], 2), "usage": {}, "error": None})
                activity.heartbeat(len(results))

        salad_seconds = _salad_pictures(req, [j for j in jobs if j["provider"] == "salad"], results)

        for f in concurrent.futures.as_completed(gem_futures):
            results.append(f.result())
            activity.heartbeat(len(results))

    return {"results": results, "modalGpuSeconds": round(modal_seconds, 2), "saladGpuSeconds": round(salad_seconds, 2)}


def _salad_pictures(req: dict, jobs: list[dict], results: list[dict]) -> float:
    """Z-Image pictures on the Short's Salad container group (gpuUrl). Each picture is its own job, so a lost
    instance only costs that picture a resubmit. Returns the GPU seconds the worker reported."""
    if not jobs:
        return 0.0
    from workers.salad import client as salad

    url = (req.get("gpuUrl") or "").rstrip("/")
    seconds = 0.0
    beat = salad.lease_beat(req.get("progressUrl") or "http://127.0.0.1:8080", req["videoId"], activity_beat)
    for j in jobs:
        try:
            if not url:
                raise RuntimeError("Salad route: the control plane sent no container group URL")
            png, info = salad.run(url, "picture", {"prompt": j["prompt"], "seed": int(j.get("seed") or 0),
                                                    "width": int(j.get("width", 768)), "height": int(j.get("height", 1344))},
                                  float(req.get("readyTimeoutSeconds") or 1800), beat=beat)
            uri = put_bytes(f"videos/{req['videoId']}/keyframes/{j['shotId']}-{j['attempt']}.png", png, "image/png")
            s = float(info.get("seconds") or 0)
            seconds += s
            results.append({"shotId": j["shotId"], "uri": uri, "provider": "salad", "model": "Tongyi-MAI/Z-Image-Turbo",
                            "seed": int(j.get("seed") or 0), "seconds": round(s, 2), "usage": {}, "error": None})
        except Exception as e:  # noqa: BLE001 - one bad shot must not lose the batch
            results.append({"shotId": j["shotId"], "uri": None, "provider": "salad", "error": f"{type(e).__name__}: {e}"[:500]})
        activity_beat(len(results))
    return seconds


def activity_beat(detail) -> None:
    from workers.common import beat

    beat(detail)
