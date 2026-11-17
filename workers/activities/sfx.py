"""Sound effects from Freesound (18 Nov 2026): ambience and spot sounds under the narration and music.

Only Creative Commons 0 and Attribution sounds are used (never NonCommercial), so a monetised video is safe; every
used sound's author and licence go into the master's credits. Needs a free FREESOUND_API_KEY; without it the edit
simply has no sound effects. Previews (high-quality MP3) are used: plenty for ambience under a mix.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

from workers.common import log

API = "https://freesound.org/apiv2/search/text/"
LICENCE_FILTER = 'license:("Creative Commons 0" OR "Attribution")'
UA = {"User-Agent": "projectv-studio/1.0 (private documentary tool)"}


def segments(shots: list[dict]) -> list[dict]:
    """Consecutive shots with the same sfxQuery become one continuous ambience segment."""
    out: list[dict] = []
    for s in sorted(shots, key=lambda x: x["startSec"]):
        q = (s.get("sfxQuery") or "").strip().lower()
        if not q:
            continue
        end = float(s["startSec"]) + float(s["durationSec"])
        if out and out[-1]["query"] == q and abs(out[-1]["endSec"] - float(s["startSec"])) < 0.05:
            out[-1]["endSec"] = end
        else:
            out.append({"query": q, "startSec": float(s["startSec"]), "endSec": end})
    return out


def search(query: str, min_sec: float, key: str) -> dict | None:
    """Best-rated commercial-safe sound for a query, at least ~min_sec long when possible (shorter ones loop)."""
    params = {"query": query, "filter": f"{LICENCE_FILTER} duration:[1 TO 600]", "sort": "rating_desc",
              "fields": "id,name,license,username,duration,previews,avg_rating", "page_size": 15, "token": key}
    req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}", headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        results = json.load(r).get("results") or []
    if not results:
        return None
    long_enough = [x for x in results if float(x.get("duration") or 0) >= min_sec]
    return (long_enough or results)[0]


def fetch(segs: list[dict], tmp: Path) -> tuple[list[dict], list[str]]:
    """Downloads one sound per segment. Returns (mix entries {path,startSec,durationSec}, credit lines)."""
    key = os.getenv("FREESOUND_API_KEY")
    if not key or not segs:
        return [], []
    entries, credits, cache = [], [], {}
    for i, seg in enumerate(segs):
        dur = seg["endSec"] - seg["startSec"]
        try:
            if seg["query"] not in cache:
                cache[seg["query"]] = search(seg["query"], dur, key)
            snd = cache[seg["query"]]
            if not snd:
                continue
            path = tmp / f"sfx{snd['id']}.mp3"
            if not path.exists():
                url = snd["previews"]["preview-hq-mp3"]
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                    path.write_bytes(r.read())
            entries.append({"path": path, "startSec": seg["startSec"], "durationSec": dur})
            licence = "CC0" if "zero" in snd["license"] or "/publicdomain/" in snd["license"] else "CC BY"
            line = f'"{snd["name"]}" by {snd["username"]} (Freesound, {licence})'
            if line not in credits:
                credits.append(line)
        except Exception as e:  # noqa: BLE001 - a missing sound effect never stops the edit
            log.warning("Sound effect for %r unavailable (%s)", seg["query"], type(e).__name__)
    return entries, credits
