"""Dream Short Scout: before writing, find Shorts that already exist for the owner's idea and watch them.

Finding: the YouTube Data API (YOUTUBE_API_KEY in .env; free quota) searches Shorts by the idea, then keeps the most
viewed short videos. Without a key, Gemini with Google Search lists Shorts it can find (less reliable, a few paise
per search). Watching: Gemini takes each public YouTube link directly (low media resolution to keep it cheap) and
describes what it shows, the look, the camera, the shots, the music, the hook and why it works. The control plane's
Claude brief then turns that into clichés to avoid and angles to stand out. We learn styles; nothing is copied.
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request

from google.genai import types
from pydantic import BaseModel
from temporalio import activity

from workers.common import MEDIA_TIMEOUT_MS, gemini, log, usage_dict, with_models

YT_API = "https://www.googleapis.com/youtube/v3"
MAX_SECONDS = 180  # YouTube Shorts are up to 3 minutes
VIDEO_ID = re.compile(r"(?:youtube\.com/(?:shorts/|watch\?v=)|youtu\.be/)([A-Za-z0-9_-]{11})")


class Phrases(BaseModel):
    phrases: list[str]


class Ranked(BaseModel):
    keep: list[int]  # candidate numbers, best match first


class Watched(BaseModel):
    relevant: bool  # does it really show something close to the idea (not just a popular video that came up)?
    shows: str  # one sentence: what the Short shows
    look: str
    camera: str
    shots: int
    avgShotSec: float
    music: str  # the music and how the cuts follow it
    hook: str  # what grabs you in the first second
    tools: str  # how it was probably made
    works: str  # why people watch it


def iso_seconds(d: str) -> float:
    """YouTube's ISO 8601 duration (PT1M5S) in seconds."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0.0
    days, h, mins, s = (int(x or 0) for x in m.groups())
    return float(days * 86400 + h * 3600 + mins * 60 + s)


def pick(videos: list[dict], n: int) -> list[dict]:
    """The best-matching real Shorts (≤ 3 min), in YouTube's relevance order, one per channel so one creator doesn't
    fill the list. Not re-sorted by views: on 19 Nov that pulled in viral videos that had nothing to do with the idea."""
    seen, out = set(), []
    for v in videos:
        if not v.get("durationSec") or v["durationSec"] > MAX_SECONDS or v.get("channel") in seen:
            continue
        seen.add(v.get("channel"))
        out.append(v)
        if len(out) >= n:
            break
    return out


def _get(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "projectv-studio/1.0"}), timeout=30) as r:
        return json.loads(r.read().decode())


STOP = {"a", "an", "the", "in", "on", "at", "of", "and", "or", "with", "to", "for", "by", "from", "into", "is", "are",
        "its", "it", "that", "this", "glowing", "standing", "very", "some", "one"}


def keywords(idea: str, most: int = 5) -> str:
    """A sentence -> a short search phrase (YouTube finds little for long sentences)."""
    words = [w for w in re.findall(r"[A-Za-z0-9']+", idea.lower()) if w not in STOP]
    return " ".join(words[:most])


def _json_call(model: str | None, prompt: str, schema, what: str):
    def call(m: str):
        return gemini().models.generate_content(model=m, contents=prompt, config=types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=schema, temperature=0.2,
            http_options=types.HttpOptions(timeout=MEDIA_TIMEOUT_MS)))
    _, r = with_models(model, call, what=what, default="gemini-3.6-flash")
    return (r.parsed if isinstance(r.parsed, schema) else schema.model_validate_json(r.text)), usage_dict(r)


def search_phrases(query: str, look: str | None, model: str | None) -> tuple[list[str], dict]:
    """YouTube's search is poor for a sentence (19 Nov: viral Lego and Roblox videos for a vending-machine idea).
    Gemini turns the idea into the short phrases and hashtags such Shorts are actually posted under."""
    p, u = _json_call(model, "Give 6 short YouTube search phrases (2-4 words each; aesthetic hashtags like #liminalspace, "
                      "#dreamcore, #aiart are welcome) that would find YouTube Shorts with visuals like this idea: 4 close "
                      "to the idea itself and 2 wider ones for the same aesthetic and mood. "
                      f"Idea: {query}. Look: {look or '-'}. Answer as JSON {{\"phrases\": [...]}}.", Phrases, "Scout phrases")
    return [x.strip() for x in p.phrases if x.strip()][:6] or [keywords(query)], u


def rank(query: str, look: str | None, cands: list[dict], model: str | None) -> tuple[list[dict], dict]:
    """Gemini reads titles and descriptions and keeps the ones really about this kind of visual, best first (cheap:
    text only, before paying to watch anything)."""
    lines = "\n".join(f"{k + 1}. {c['title']} | {(c.get('description') or '')[:160]}" for k, c in enumerate(cands))
    r, u = _json_call(model, f"Idea: {query}. Look: {look or '-'}.\nWhich of these YouTube Shorts most likely show visuals "
                      "like this idea, or the same aesthetic and mood (same kind of scene, light and feeling; not just a "
                      "shared word)? Close matches first, then same-aesthetic ones. Leave out anything unrelated. "
                      f"Answer as JSON {{\"keep\": [numbers, best match first]}}.\n{lines}", Ranked, "Scout rank")
    return [cands[k - 1] for k in dict.fromkeys(r.keep) if 1 <= k <= len(cands)], u


def youtube_find(query: str, key: str, n: int, look: str | None = None, model: str | None = None) -> tuple[list[dict], list[dict]]:
    """Search YouTube with Gemini's phrases (relevance order), keep real Shorts (≤ 3 min), let Gemini keep only the
    ones about this kind of visual, one per channel. Returns (videos, Gemini usage)."""
    usage: list[dict] = []
    phrases, u = search_phrases(query, look, model)
    usage.append(u)
    ids: list[str] = []
    for q in phrases:
        p = urllib.parse.urlencode({"part": "snippet", "q": q, "type": "video", "videoDuration": "short",
                                    "order": "relevance", "maxResults": 15, "key": key})
        ids += [i["id"]["videoId"] for i in _get(f"{YT_API}/search?{p}").get("items", []) if i.get("id", {}).get("videoId")]
    ids = list(dict.fromkeys(ids))[:50]  # one videos.list call takes 50
    if not ids:
        return [], usage
    q = urllib.parse.urlencode({"part": "snippet,statistics,contentDetails", "id": ",".join(ids), "key": key})
    order = {vid: k for k, vid in enumerate(ids)}
    videos = [{"url": f"https://www.youtube.com/watch?v={v['id']}", "title": html.unescape(v["snippet"].get("title") or ""),
               "description": html.unescape(v["snippet"].get("description") or ""),
               "channel": v["snippet"].get("channelTitle"), "views": int(v.get("statistics", {}).get("viewCount", 0) or 0),
               "durationSec": iso_seconds(v.get("contentDetails", {}).get("duration", ""))}
              for v in sorted(_get(f"{YT_API}/videos?{q}").get("items", []), key=lambda x: order.get(x["id"], 99))]
    shorts = [v for v in videos if v["durationSec"] and v["durationSec"] <= MAX_SECONDS]
    ranked, u = rank(query, look, shorts, model)
    usage.append(u)
    out = [{k: v for k, v in c.items() if k != "description"} for c in pick(ranked, n + 2)]
    return out, usage  # a couple spare: watching drops any that still turn out unrelated


def google_find(query: str, model: str | None, n: int) -> tuple[list[dict], dict]:
    """No YouTube key: Gemini + Google Search lists Shorts it can find. Returns (videos, usage)."""
    prompt = (f"Find {n * 2} popular YouTube Shorts (vertical, under 3 minutes) that show: {query}. List each one's full "
              "YouTube link (youtube.com/shorts/... or youtube.com/watch?v=...) and title, one per line.")

    def call(m: str):
        return gemini().models.generate_content(model=m, contents=prompt, config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())], http_options=types.HttpOptions(timeout=MEDIA_TIMEOUT_MS)))

    _, r = with_models(model, call, what="Scout search", default="gemini-3.6-flash")
    text = r.text or ""
    meta = getattr(r.candidates[0], "grounding_metadata", None) if r.candidates else None
    for chunk in (getattr(meta, "grounding_chunks", None) or []):
        web = getattr(chunk, "web", None)
        text += "\n" + (getattr(web, "uri", "") or "") + " " + (getattr(web, "title", "") or "")
    ids = list(dict.fromkeys(VIDEO_ID.findall(text)))[:n]
    return [{"url": f"https://www.youtube.com/watch?v={i}", "title": None, "channel": None, "views": None,
             "durationSec": None} for i in ids], usage_dict(r)


def watch(video: dict, query: str, model: str | None) -> tuple[dict, dict]:
    """Gemini watches one public Short from its link. Returns (what it saw, usage)."""
    prompt = (f"This is a YouTube Short that came up for the idea: {query}. Watch it closely, sound on. Answer as JSON: "
              "shows (one concrete sentence), look (colour, light, realism), camera (how it moves and who seems to hold it), "
              "shots (how many), avgShotSec, music (what it is and how the cuts follow it), hook (what grabs you in the "
              "first second), tools (how it was probably made: AI image/video tools, real footage, editing app), "
              "works (why people watch it), relevant (true only if it really shows something close to the idea, "
              "false if it is about something else).")

    def call(m: str):
        return gemini().models.generate_content(
            model=m, contents=types.Content(parts=[types.Part(file_data=types.FileData(file_uri=video["url"])),
                                                   types.Part(text=prompt)]),
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Watched, temperature=0.2,
                                               media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
                                               http_options=types.HttpOptions(timeout=MEDIA_TIMEOUT_MS)))

    _, r = with_models(model, call, what="Scout watch", default="gemini-3.6-flash")
    w = r.parsed if isinstance(r.parsed, Watched) else Watched.model_validate_json(r.text)
    return w.model_dump(), usage_dict(r)


def scout(req: dict) -> dict:
    query, n, model, look = req["query"], int(req.get("maxVideos") or 6), req.get("model"), req.get("look")
    usage: list[dict] = []
    key = os.getenv("YOUTUBE_API_KEY")
    source, searches, error = ("youtube", 2, None) if key else ("google", 1, None)
    try:
        if key:
            found, u = youtube_find(query, key, n, look, model)
            usage += u
        else:
            found, u = google_find(query, model, n)
            usage.append(u)
    except Exception as e:  # noqa: BLE001 - reported to the owner; the step waits for Retry
        log.warning("Scout search failed: %s", type(e).__name__)
        return {"videos": [], "usage": usage, "source": source, "searches": searches, "error": f"search failed: {type(e).__name__}"}
    videos = []
    known = req.get("known") or {}
    reused = 0
    for k, v in enumerate(found):
        if sum(1 for x in videos if not x["error"]) >= n:
            break
        m = VIDEO_ID.search(v.get("url") or "")
        cached = known.get(m.group(1)) if m else None
        if cached:  # watched before: reuse what Gemini saw (free); fresh title/views from this search win
            keep = {f: cached.get(f) for f in Watched.model_fields if f != "relevant"}
            videos.append({**v, **keep, "error": cached.get("error")})
            reused += 1
            continue
        try:
            seen, u = watch(v, query, model)
            usage.append(u)
            off = not seen.pop("relevant", True)
            videos.append({**v, **seen, "error": "not about this idea" if off else None})
        except Exception as e:  # noqa: BLE001 - one private or removed Short must not stop the rest
            videos.append({**v, "error": f"{type(e).__name__}: {str(e)[:160]}"})
        if activity.in_activity():
            activity.heartbeat({"watched": k + 1, "of": len(found)})
    if not found:
        error = "no Shorts found for this idea"
    log.info("Scout: %d found via %s, %d watched (%d reused from the cache)", len(found), source,
             sum(1 for v in videos if not v["error"]), reused)
    return {"videos": videos, "usage": usage, "source": source, "searches": searches, "error": error}


@activity.defn(name="dream_scout")
def dream_scout(req: dict) -> dict:
    """req: DreamScoutRequest {dreamId, query, model, maxVideos} -> DreamScoutResult {videos, usage, source, searches, error}"""
    return scout(req)
