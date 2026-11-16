"""Keyframes: one still per shot. The control plane decides the provider per shot (hero -> Nano Banana via the paid
Gemini API, the rest -> Z-Image Turbo on free Modal credits); this activity just executes those decisions."""

from __future__ import annotations

import concurrent.futures
import time

import modal
from google.genai import types
from temporalio import activity

from workers.common import gemini, put_bytes, usage_dict, with_models

MODAL_BATCH = 8  # images per Modal call; heartbeat between batches


def _nano_banana(req: dict, job: dict) -> dict:
    t = time.time()
    used, response = with_models(req["geminiImageModel"], lambda m: gemini().models.generate_content(
        model=m,
        contents=job["prompt"],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio=job.get("aspectRatio", "16:9"), image_size=job.get("imageSize", "1K")),
        ),
    ), what=f"image {job['shotId']}")
    for part in response.candidates[0].content.parts:
        if part.inline_data and (part.inline_data.mime_type or "").startswith("image"):
            mime = part.inline_data.mime_type
            ext = "png" if "png" in mime else "jpg"
            uri = put_bytes(f"videos/{req['videoId']}/keyframes/{job['shotId']}-{job['attempt']}.{ext}",
                            part.inline_data.data, mime)
            return {"shotId": job["shotId"], "uri": uri, "provider": "gemini_api", "model": used,
                    "seed": None, "seconds": round(time.time() - t, 2), "usage": usage_dict(response), "error": None}
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

        for f in concurrent.futures.as_completed(gem_futures):
            results.append(f.result())
            activity.heartbeat(len(results))

    return {"results": results, "modalGpuSeconds": round(modal_seconds, 2)}
