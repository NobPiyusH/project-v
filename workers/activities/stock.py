"""Stock footage lane (docs/stock-footage-lane.md): real video clips from Pexels, Pixabay and Wikimedia Commons.

Given a shot (description, aspect, duration) the activity builds 2-3 search queries, searches the enabled sources, ranks the
candidates, downloads ONLY the chosen clip (size-capped), trims it to the shot and returns it with full provenance.
Never fails a shot: no key, no match, a network error or an oversized file all return found=false so the caller falls back to
the AI lane. Only video clips are ever used (owner rule 10: no panned stills). Keys come from the environment, never logged.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from temporalio import activity

from workers.common import log, media_duration, put_file, run_ffmpeg, workdir

UA = "projectv-studio/1.0 (private documentary tool)"  # generic client name: Commons wants a descriptive User-Agent
PEXELS = "https://api.pexels.com/v1/videos/search"
PIXABAY = "https://pixabay.com/api/videos/"
COMMONS = "https://commons.wikimedia.org/w/api.php"
ALL_SOURCES = ("pexels", "pixabay", "commons")
KEY_ENV = {"pexels": "PEXELS_API_KEY", "pixabay": "PIXABAY_API_KEY"}
OK_LICENCE = re.compile(r"^(public domain|pd|cc0|cc[ -]by \d(\.\d)?)", re.I)  # never share-alike, NC or ND
STOP = set("a an the of in on at to and or with for from by is are was be as it its this that into over under near "
           "shot scene video footage clip showing shows view".split())
MIN_FILL = 0.6  # a clip shorter than this share of the shot is rejected (assembly would have to slow it too much)


def _get_json(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _key(source: str) -> str | None:
    env = KEY_ENV.get(source)
    return (os.environ.get(env) or "").strip() or None if env else None


def available_sources(wanted) -> tuple[list[str], list[str]]:
    """(usable, skipped-with-reason): a keyed source without its key is skipped cleanly, never an error."""
    usable, skipped = [], []
    for s in wanted or []:
        if s not in ALL_SOURCES:
            continue
        if s in KEY_ENV and not _key(s):
            skipped.append(f"{s}: no {KEY_ENV[s]}")
        else:
            usable.append(s)
    return usable, skipped


# ------------------------------------------------------------------------------------------------------------- queries

def keyword_queries(description: str, n: int = 3) -> list[str]:
    """Deterministic fallback: the most content-rich words of the description, in a few short combinations."""
    words = [w for w in re.findall(r"[a-z][a-z'-]{2,}", (description or "").lower()) if w not in STOP]
    seen: list[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
    if not seen:
        return []
    out = [" ".join(seen[:3])]
    if len(seen) > 3:
        out.append(" ".join(seen[3:6]))
    if len(seen) > 2:
        out.append(" ".join([seen[0], seen[-1]]))
    return list(dict.fromkeys(q for q in out if q))[:n]


def _llm_queries(description: str, model: str | None, n: int) -> list[str]:
    from google.genai import types

    from workers.common import gemini, with_models

    prompt = (f"Write {n} short stock-footage search queries (2-4 plain words each, no brand names, no quotes) that would find "
              f"a real video clip for this shot:\n{description}\nReturn only a JSON array of strings.")
    _, resp = with_models(model, lambda m: gemini().models.generate_content(
        model=m, contents=prompt, config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.4)),
                          what="stock queries", default="gemini-3.6-flash")
    data = json.loads(resp.text)
    return [str(q).strip() for q in data if str(q).strip()][:n]


def build_queries(description: str, model: str | None = None, n: int = 3) -> list[str]:
    """2-3 queries from the studio text model; on any failure (or no model) the keyword fallback."""
    got: list[str] = []
    if model:
        try:
            got = [q for q in _llm_queries(description, model, n) if 1 <= len(q.split()) <= 6]
        except Exception as e:  # noqa: BLE001 - the lane must work without the model
            log.info("stock queries: model failed (%s), using keywords", type(e).__name__)
    return (got or keyword_queries(description, n))[:n]


# ------------------------------------------------------------------------------------------------------------- sources

def pick_file(files: list[dict], want_short: int = 1080) -> dict | None:
    """The smallest file whose short side reaches want_short (keeps the download small), else the largest available."""
    good = [f for f in files if f.get("url") and f.get("width") and f.get("height")]
    if not good:
        return None
    big = [f for f in good if min(f["width"], f["height"]) >= want_short]
    return min(big, key=lambda f: f["width"] * f["height"]) if big else max(good, key=lambda f: f["width"] * f["height"])


def _orientation(aspect: str) -> str:
    return {"9:16": "portrait", "4:5": "portrait", "1:1": "square"}.get(aspect or "16:9", "landscape")


def search_pexels(query: str, aspect: str, per_page: int = 12) -> list[dict]:
    q = urllib.parse.urlencode({"query": query, "orientation": _orientation(aspect), "per_page": per_page, "size": "medium"})
    out = []
    for v in _get_json(f"{PEXELS}?{q}", {"Authorization": _key("pexels") or ""}).get("videos") or []:
        f = pick_file([{"url": x.get("link"), "width": x.get("width"), "height": x.get("height")}
                       for x in v.get("video_files") or [] if (x.get("file_type") or "video/mp4") == "video/mp4"])
        if not f:
            continue
        user = v.get("user") or {}
        out.append({"source": "pexels", "id": str(v["id"]), "pageUrl": v.get("url"), "downloadUrl": f["url"],
                    "width": f["width"], "height": f["height"], "duration": float(v.get("duration") or 0),
                    "author": user.get("name") or "", "authorUrl": user.get("url") or "",
                    "licence": "Pexels License", "licenceUrl": "https://www.pexels.com/license/",
                    "text": re.sub(r"[-/_]", " ", (v.get("url") or "").rstrip("/").rsplit("/", 1)[-1])})
    return out


def search_pixabay(query: str, aspect: str, per_page: int = 12) -> list[dict]:
    q = urllib.parse.urlencode({"key": _key("pixabay") or "", "q": query[:100], "per_page": max(3, per_page),
                                "video_type": "film", "safesearch": "true", "min_width": 1280})
    out = []
    for v in _get_json(f"{PIXABAY}?{q}").get("hits") or []:
        f = pick_file([{"url": x.get("url"), "width": x.get("width"), "height": x.get("height")}
                       for x in (v.get("videos") or {}).values() if isinstance(x, dict)])
        if not f:
            continue
        out.append({"source": "pixabay", "id": str(v["id"]), "pageUrl": v.get("pageURL"), "downloadUrl": f["url"],
                    "width": f["width"], "height": f["height"], "duration": float(v.get("duration") or 0),
                    "author": v.get("user") or "", "authorUrl": "",
                    "licence": "Pixabay Content License", "licenceUrl": "https://pixabay.com/service/license-summary/",
                    "text": v.get("tags") or ""})
    return out


def _plain(html: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def search_commons(query: str, aspect: str, per_page: int = 8) -> list[dict]:
    q = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": per_page * 2,
        "gsrsearch": " ".join(f'"{w}"' for w in query.split()) + " filetype:video",
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata|metadata"})
    pages = (_get_json(f"{COMMONS}?{q}").get("query") or {}).get("pages") or {}
    out = []
    for p in sorted(pages.values(), key=lambda p: p.get("index", 0)):
        info = (p.get("imageinfo") or [{}])[0]
        meta = info.get("extmetadata") or {}
        lic = _plain((meta.get("LicenseShortName") or {}).get("value"))
        if not OK_LICENCE.match(lic) or "sa" in lic.lower().split() or not info.get("url"):
            continue
        length = next((float(m["value"]) for m in info.get("metadata") or []
                       if m.get("name") in ("length", "duration") and isinstance(m.get("value"), (int, float))), 0.0)
        title = p.get("title", "").removeprefix("File:")
        out.append({"source": "commons", "id": p.get("title", ""), "pageUrl": info.get("descriptionurl")
                    or "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(p.get("title", "").replace(" ", "_")),
                    "downloadUrl": info["url"], "width": info.get("width") or 0, "height": info.get("height") or 0,
                    "duration": length, "title": title,
                    "author": _plain((meta.get("Artist") or {}).get("value")) or _plain((meta.get("Credit") or {}).get("value")),
                    "authorUrl": "", "licence": lic, "licenceUrl": _plain((meta.get("LicenseUrl") or {}).get("value")),
                    "text": title + " " + _plain((meta.get("ImageDescription") or {}).get("value"))})
        if len(out) >= per_page:
            break
    return out


SEARCHERS = {"pexels": search_pexels, "pixabay": search_pixabay, "commons": search_commons}


# --------------------------------------------------------------------------------------------------------------- ranking

def relevance(cand: dict, queries: list[str]) -> float:
    """Cheap text match 0..1: the best share of a query's words found in the candidate's tags/title text."""
    hay = (cand.get("text") or "").lower()
    best = 0.0
    for q in queries:
        words = [w for w in re.findall(r"[a-z]{3,}", q.lower()) if w not in STOP]
        if words:
            best = max(best, sum(1 for w in words if w in hay) / len(words))
    return best


def _orient(w: int, h: int) -> str:
    return "square" if abs(w - h) <= 0.05 * max(w, h, 1) else "landscape" if w > h else "portrait"


def rank(cands: list[dict], queries: list[str], aspect: str, want_sec: float) -> list[dict]:
    """Best first. Wrong orientation or far too short is dropped; then resolution (>=1080 preferred), duration, relevance."""
    want_o = _orientation(aspect)
    scored = []
    for c in cands:
        if _orient(c["width"], c["height"]) != want_o and not (want_o == "square" and _orient(c["width"], c["height"]) != "portrait"):
            continue
        dur = c.get("duration") or 0.0
        if dur and dur < want_sec * MIN_FILL:
            continue
        short = min(c["width"], c["height"])
        s = (2.0 if short >= 1080 else 1.0 if short >= 720 else 0.0)
        s += 1.0 if (not dur or dur >= want_sec) else -2.0 * (want_sec - dur) / want_sec
        s -= 0.5 if dur > 90 else 0.0
        s += 3.0 * relevance(c, queries)
        scored.append((s, c))
    return [c for _, c in sorted(scored, key=lambda t: -t[0])]


# ----------------------------------------------------------------------------------------------------------- provenance

def provenance(c: dict, fetched_at: str) -> dict:
    """What the studio stores per clip. creditRequired: CC BY (law) and Pexels (its API guideline asks for a Pexels link)."""
    lic = c["licence"]
    if c["source"] == "commons":
        required = bool(re.match(r"cc[ -]by", lic, re.I))
        text = (f"\"{c.get('title', '')}\" by {c.get('author') or 'unknown author'}, {lic}"
                + (f" ({c['licenceUrl']})" if c.get("licenceUrl") else "") + f", via Wikimedia Commons: {c['pageUrl']}; trimmed")
    elif c["source"] == "pexels":
        required = True
        text = f"Video by {c.get('author') or 'a Pexels contributor'} on Pexels ({c['pageUrl']})"
    else:
        required = False
        text = f"Video by {c.get('author') or 'a Pixabay contributor'} on Pixabay ({c['pageUrl']})"
    return {"source": c["source"], "sourceId": c["id"], "pageUrl": c["pageUrl"], "author": c.get("author") or "",
            "licence": lic, "licenceUrl": c.get("licenceUrl") or "", "attribution": text, "creditRequired": required,
            "fetchedAt": fetched_at}


# ------------------------------------------------------------------------------------------------------------ download

def _download(url: str, dest: Path, max_bytes: int) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        n = int(r.headers.get("Content-Length") or 0)
        if n > max_bytes:
            raise ValueError(f"clip is {n // 1_000_000} MB, over the {max_bytes // 1_000_000} MB cap")
        got = 0
        while chunk := r.read(1 << 20):
            got += len(chunk)
            if got > max_bytes:
                raise ValueError(f"clip is over the {max_bytes // 1_000_000} MB cap")
            f.write(chunk)


def _miss(reason: str, queries: list[str], skipped: list[str]) -> dict:
    log.info("stock: no clip (%s)", reason)
    return {"found": False, "reason": reason, "queries": queries, "skipped": skipped}


@activity.defn(name="stock_fetch")
def stock_fetch(req: dict) -> dict:
    """req: {videoId, shotId, shotNumber, description, aspectRatio, durationSec, sources[], queryModel, maxMb}
    -> {found, reason, queries, skipped, uri, width, height, durationSec, sourceWidth, sourceHeight, provenance}"""
    aspect = req.get("aspectRatio") or "16:9"
    want = max(1.0, float(req.get("durationSec") or 4))
    queries: list[str] = []
    usable, skipped = available_sources(req.get("sources") or [])
    if not usable:
        return _miss("no stock source is usable (" + ("; ".join(skipped) or "none enabled") + ")", queries, skipped)
    try:
        queries = build_queries(req.get("description") or "", req.get("queryModel"))
        if not queries:
            return _miss("no search words in the shot description", queries, skipped)
        cands: list[dict] = []
        for src in usable:
            for q in queries:
                try:
                    cands += SEARCHERS[src](q, aspect)
                except Exception as e:  # noqa: BLE001 - one failing source/query never hides the others
                    skipped.append(f"{src}: {type(e).__name__}")
                    break
        seen, uniq = set(), []
        for c in cands:
            if (c["source"], c["id"]) not in seen:
                seen.add((c["source"], c["id"]))
                uniq.append(c)
        ranked = rank(uniq, queries, aspect, want)
        max_bytes = int(float(req.get("maxMb") or os.environ.get("STOCK_MAX_MB") or 400) * 1_000_000)
        for c in ranked[:3]:  # the best that downloads within the cap
            try:
                with workdir("stock") as tmp:
                    raw = Path(tmp) / "raw"
                    _download(c["downloadUrl"], raw, max_bytes)
                    total = media_duration(raw)
                    if total < want * MIN_FILL:
                        continue
                    start = max(0.0, min(total * 0.1, total - want))
                    out = Path(tmp) / "clip.mp4"
                    run_ffmpeg(["-ss", f"{start:.3f}", "-i", str(raw), "-t", f"{want:.3f}", "-an", "-c:v", "libx264",
                                "-preset", "slow", "-crf", "14", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)])
                    got = media_duration(out)
                    uri = put_file(f"videos/{req['videoId']}/stock/shot{int(req.get('shotNumber') or 0):03d}-"
                                   f"{c['source']}-{re.sub(r'[^A-Za-z0-9_-]', '_', c['id'])[:40]}.mp4", out, "video/mp4")
            except Exception as e:  # noqa: BLE001
                skipped.append(f"{c['source']} {c['id']}: {type(e).__name__}")
                continue
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            log.info("stock clip %s:%s for shot %s", c["source"], c["id"], req.get("shotNumber"))
            return {"found": True, "reason": None, "queries": queries, "skipped": skipped, "uri": uri,
                    "width": c["width"], "height": c["height"], "durationSec": round(got, 3), "sourceWidth": c["width"],
                    "sourceHeight": c["height"], "provenance": provenance(c, now)}
        return _miss("no suitable clip" if ranked else "no candidates", queries, skipped)
    except Exception as e:  # noqa: BLE001 - never fail the shot
        return _miss(f"{type(e).__name__}: {str(e)[:150]}", queries, skipped)
