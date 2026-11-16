"""Free real footage from the NASA Image and Video Library (images-api.nasa.gov; no key needed).

NASA media is generally a US Government work, not copyrighted in the US and usable commercially, as long as it doesn't
imply NASA endorsement and doesn't use the NASA insignia as a logo. Some items carry third-party credits; every
candidate keeps its credit line and centre, and the clip's provenance records them.

Quality first: candidates are only suggested; the owner picks. A picked clip is cut from NASA's highest-resolution
original (FFmpeg seeks over HTTP, so only the needed seconds are downloaded), cropped to the video's frame and encoded
at high quality. The upscale factor is reported, so a soft (heavily enlarged) crop is visible before it is used.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from temporalio import activity

from workers.common import ffprobe_bin, log, put_file, run_ffmpeg, workdir

API = "https://images-api.nasa.gov"
ASSETS = "https://images-assets.nasa.gov"
SKIP_TITLES = re.compile(r"narrated|interview|briefing|news conference|press conference|q&a|live shot|panel|"
                         r"countdown clock|b-roll package|soundbite|social media", re.I)
TARGET = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "projectv-studio"}), timeout=30) as r:
        return json.load(r)


def _https(url: str) -> str:
    return url.replace("http://", "https://", 1)


def search(query: str, limit: int = 5) -> list[dict]:
    """Video candidates for a query, best first, without talking-head material."""
    q = urllib.parse.urlencode({"q": query, "media_type": "video"})
    items = _get_json(f"{API}/search?{q}").get("collection", {}).get("items", [])
    out = []
    for it in items:
        d = (it.get("data") or [{}])[0]
        title = d.get("title") or ""
        if not d.get("nasa_id") or SKIP_TITLES.search(title):
            continue
        nasa_id = d["nasa_id"]
        base = f"{ASSETS}/video/{urllib.parse.quote(nasa_id)}/{urllib.parse.quote(nasa_id)}"
        credit = ", ".join(x for x in (d.get("center"), d.get("secondary_creator"), d.get("photographer"))
                           if x and x != "None")
        out.append({"nasaId": nasa_id, "title": title[:200],
                    "description": (d.get("description_508") or d.get("description") or "")[:400],
                    "credit": credit[:300], "date": (d.get("date_created") or "")[:10],
                    "thumbUrl": base + "~medium.jpg", "previewUrl": base + "~small.mp4"})
        if len(out) >= limit:
            break
    return out


@activity.defn(name="search_footage")
def search_footage(req: dict) -> dict:
    """req: {queries:[{shotId, query}]} -> {results:[{shotId, query, candidates:[...] , error}]}. Free."""
    results = []
    for q in req.get("queries") or []:
        try:
            results.append({"shotId": q["shotId"], "query": q["query"], "candidates": search(q["query"]), "error": None})
        except Exception as e:  # noqa: BLE001 - one failed search must not lose the others
            results.append({"shotId": q["shotId"], "query": q["query"], "candidates": [],
                            "error": f"{type(e).__name__}: {str(e)[:200]}"})
        try:
            activity.heartbeat(q["shotId"])
        except RuntimeError:
            pass
    return {"results": results}


def _best_source(nasa_id: str) -> str:
    """Highest quality rendition NASA has for this item: the original master first."""
    hrefs = [i["href"] for i in _get_json(f"{API}/asset/{urllib.parse.quote(nasa_id)}")["collection"]["items"]]
    for suffix in ("~orig.mp4", "~large.mp4", "~medium.mp4", "~mobile.mp4", "~small.mp4"):
        for h in hrefs:
            if h.endswith(suffix):
                return _https(h)
    mp4s = [h for h in hrefs if h.endswith(".mp4")]
    if not mp4s:
        raise RuntimeError(f"NASA item {nasa_id} has no MP4 rendition")
    return _https(mp4s[0])


def _probe(url_or_path: str) -> dict:
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", url_or_path],
                         capture_output=True, text=True, timeout=120, check=True).stdout
    d = json.loads(out)
    s = (d.get("streams") or [{}])[0]
    return {"width": int(s.get("width") or 0), "height": int(s.get("height") or 0),
            "duration": float((d.get("format") or {}).get("duration") or 0)}


@activity.defn(name="fetch_footage")
def fetch_footage(req: dict) -> dict:
    """req: {videoId, shotId, shotNumber, nasaId, startSec (null = auto), durationSec, aspectRatio}
    -> {uri, width, height, durationSec, sourceWidth, sourceHeight, upscale, startSec, sourceUrl}"""
    src = _best_source(req["nasaId"])
    info = _probe(src)
    tw, th = TARGET.get(req.get("aspectRatio") or "16:9", (1920, 1080))
    want = max(1.0, float(req.get("durationSec") or 4))
    total = info["duration"]
    start = req.get("startSec")
    if start is None:
        # NASA edits usually open on a title card / logo: skip the first fifth (at least 2 s) when the clip allows.
        start = min(max(2.0, total * 0.2), max(0.0, total - want - 0.5))
    start = max(0.0, min(float(start), max(0.0, total - want)))
    # Centre crop to the target frame, then scale; report how much the crop had to be enlarged.
    sw, sh = info["width"], info["height"]
    target_ratio = tw / th
    crop_w, crop_h = (min(sw, round(sh * target_ratio)), sh) if sw / sh > target_ratio else (sw, min(sh, round(sw / target_ratio)))
    upscale = round(max(tw / crop_w, th / crop_h), 2) if crop_w and crop_h else None
    with workdir("footage") as tmp:
        out = Path(tmp) / "clip.mp4"
        run_ffmpeg(["-ss", f"{start:.3f}", "-i", src, "-t", f"{want:.3f}", "-an",
                    "-vf", f"crop={crop_w // 2 * 2}:{crop_h // 2 * 2},scale={tw}:{th}:flags=lanczos,format=yuv420p",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-movflags", "+faststart", str(out)])
        got = _probe(str(out))
        uri = put_file(f"videos/{req['videoId']}/footage/shot{int(req.get('shotNumber') or 0):03d}-"
                       f"{re.sub(r'[^A-Za-z0-9_-]', '_', req['nasaId'])[:60]}.mp4", out, "video/mp4")
    log.info("NASA footage %s for shot %s: %.1fs from %.1fs, upscale x%s", req["nasaId"], req.get("shotNumber"),
             got["duration"], start, upscale)
    return {"uri": uri, "width": tw, "height": th, "durationSec": round(got["duration"], 3), "sourceWidth": sw,
            "sourceHeight": sh, "upscale": upscale, "startSec": round(start, 2), "sourceUrl": src}
