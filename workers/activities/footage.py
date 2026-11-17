"""Free real footage: the NASA Image and Video Library (images-api.nasa.gov) and Wikimedia Commons (no keys needed).

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
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "projectv-studio/1.0 (private documentary tool)"}), timeout=30) as r:
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


# ------------------------------------------------------------------------------------------------ Wikimedia Commons
# Second free source (18 Nov 2026): real nature, animals, places and events, including much NOAA deep-sea footage.
# Only licences that never bind the finished video are accepted: public domain, CC0 and CC BY (credit required).
# Share-alike (CC BY-SA) and non-commercial licences are skipped.

COMMONS = "https://commons.wikimedia.org/w/api.php"
COMMONS_PREFIX = "commons:"
OK_LICENCE = re.compile(r"^(public domain|pd|cc0|cc[ -]by \d(\.\d)?)", re.I)


def _plain(html: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def _commons_query(query: str, kind: str, limit: int, thumb: int) -> list[dict]:
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": limit * 3,
        # Every word quoted: Commons search otherwise stems ("anglerfish" matched "angle grinder").
        "gsrsearch": " ".join(f'"{w}"' for w in query.split()) + f" filetype:{kind}",
        "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime",
        "iiurlwidth": thumb})
    pages = (_get_json(f"{COMMONS}?{q}").get("query") or {}).get("pages") or {}
    out = []
    for p in sorted(pages.values(), key=lambda p: p.get("index", 0)):
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        lic = _plain((meta.get("LicenseShortName") or {}).get("value"))
        if not OK_LICENCE.match(lic) or "sa" in lic.lower().split():
            continue
        if min(info.get("width") or 0, info.get("height") or 0) < 480:
            continue
        author = _plain((meta.get("Artist") or {}).get("value")) or _plain((meta.get("Credit") or {}).get("value"))
        out.append({"title": p.get("title", ""), "url": info.get("url"), "thumbUrl": info.get("thumburl"),
                    "licence": lic, "licenceUrl": _plain((meta.get("LicenseUrl") or {}).get("value")),
                    "author": author[:200], "description": _plain((meta.get("ImageDescription") or {}).get("value"))[:400],
                    "date": _plain((meta.get("DateTimeOriginal") or {}).get("value"))[:10],
                    "width": info.get("width"), "height": info.get("height")})
        if len(out) >= limit:
            break
    return out


def search_commons(query: str, limit: int = 4) -> list[dict]:
    """Video candidates from Wikimedia Commons in the same shape as NASA's (the id carries the source)."""
    out = []
    for c in _commons_query(query, "video", limit, 480):
        credit = f"{c['author'] or 'Wikimedia Commons'} / {c['licence']}"
        out.append({"nasaId": COMMONS_PREFIX + c["title"], "title": c["title"].removeprefix("File:")[:200],
                    "description": c["description"], "credit": credit[:300], "date": c["date"],
                    "thumbUrl": c["thumbUrl"], "previewUrl": c["url"]})
    return out


def reference_photos(query: str, limit: int = 2) -> list[dict]:
    """Licence-safe real photos of a subject (for AI keyframes to get anatomy and appearance right)."""
    return _commons_query(query, "bitmap", limit, 1024)


def _search_all(query: str) -> list[dict]:
    """NASA first (space), then Commons (nature, animals, places); a failing source never hides the other."""
    out, errors = [], []
    for fn, n in ((search, 4), (search_commons, 4)):
        try:
            out += fn(query, n)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{fn.__name__}: {type(e).__name__}")
    if not out and errors:
        raise RuntimeError("; ".join(errors))
    return out


@activity.defn(name="search_footage")
def search_footage(req: dict) -> dict:
    """req: {queries:[{shotId, query}]} -> {results:[{shotId, query, candidates:[...] , error}]}. Free."""
    results = []
    for q in req.get("queries") or []:
        try:
            results.append({"shotId": q["shotId"], "query": q["query"], "candidates": _search_all(q["query"]),
                            "error": None})
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
    -> {uri, width, height, durationSec, sourceWidth, sourceHeight, upscale, startSec, sourceUrl}
    A Commons candidate's id starts with "commons:"; its original file is used."""
    if req["nasaId"].startswith(COMMONS_PREFIX):
        title = req["nasaId"].removeprefix(COMMONS_PREFIX)
        q = urllib.parse.urlencode({"action": "query", "format": "json", "titles": title, "prop": "imageinfo",
                                    "iiprop": "url"})
        pages = _get_json(f"{COMMONS}?{q}")["query"]["pages"]
        src = next(iter(pages.values()))["imageinfo"][0]["url"]
    else:
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
