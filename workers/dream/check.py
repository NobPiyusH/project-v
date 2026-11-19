"""Dream Short quality check (learning loop, phase 3): before the owner sees a finished Short, Gemini watches a small
copy of it against a checklist built from the owner's rules and examples and lists concrete problems by shot.

It never blocks or fails the Short: a failed check just means no report. A ~360p copy keeps it to a few paise.
"""

from __future__ import annotations

from pathlib import Path

from google.genai import types
from pydantic import BaseModel
from temporalio import activity

from workers.common import MEDIA_TIMEOUT_MS, gemini, get_bytes, log, run_ffmpeg, usage_dict, with_models, workdir

CHECKLIST = {
    "noPeople": "No people or animals appear anywhere (the camera is a first-person view; nobody is ever in frame)",
    "cleanText": "Any text or sign is readable and correct; no garbled letters, no brand names or logos",
    "looksReal": "Looks like real footage a person filmed; no warping, morphing, melting objects, flicker or plastic AI look",
    "continuity": "Objects stay the same from shot to shot (same colour, shape, place) when they are meant to be the same",
    "humanCamera": "The camera moves like a person holding a phone (stand, step, slow walk), never a drone, dolly, orbit or zoom",
    "musicFit": "The music suits the pictures and the cuts land on it",
    "story": "Shots follow the planned order and the ending pays off",
    "feelGood": "It feels warm, nostalgic, calm and beautiful (never scary or sad)",
}


class Issue(BaseModel):
    shot: int  # 1-based, 0 = the whole Short
    atSec: float
    problem: str
    fix: str  # what to redo, in the owner's words (e.g. "redo clip 3 with no person")


class Score(BaseModel):
    key: str  # checklist key
    score: int  # 1..10


class Report(BaseModel):
    # A list, not dict[str, int]: the Gemini Developer API rejects schemas with additionalProperties.
    scores: list[Score]
    overall: int  # 1..10
    issues: list[Issue]
    summary: str  # one sentence for the owner


def proxy(src: Path, dst: Path) -> Path:
    """A small copy for watching: 360 px wide, low bitrate, sound kept."""
    run_ffmpeg(["-i", str(src), "-vf", "scale=360:-2", "-c:v", "libx264", "-crf", "30", "-preset", "veryfast",
                "-c:a", "aac", "-b:a", "64k", str(dst)])
    return dst


def prompt(brief: str) -> str:
    items = "\n".join(f"- {k}: {v}" for k, v in CHECKLIST.items())
    return ("You are the quality checker of a private studio's wordless, music-led vertical Shorts. Watch this finished "
            "Short closely, sound on, and judge it against the checklist. Be strict: one person walking into a shot, one "
            "garbled sign or one melting object is a real problem. For each problem say which shot (1-based, in play "
            "order), roughly when (seconds), what is wrong, and the smallest fix (e.g. 'redo clip 3', 'redo picture 2 "
            "with notes: no sign').\n\nWhat this Short is meant to be:\n" + brief + "\n\nChecklist (score each 1-10):\n"
            + items + "\n\nAnswer as JSON: scores [{key, score 1-10}], overall (1-10), issues [{shot, atSec, problem, fix}], "
            "summary (one sentence).")


PICTURE_ITEMS = ("noPeople", "cleanText", "looksReal", "continuity", "story", "feelGood")


def picture_prompt(brief: str) -> str:
    """The same checklist for the still pictures, before any clip is paid for (motion and music can't be judged yet)."""
    items = "\n".join(f"- {k}: {CHECKLIST[k]}" for k in PICTURE_ITEMS)
    return ("You are the quality checker of a private studio's wordless vertical Shorts. Below are the still pictures "
            "that will each be animated into one shot, in order (picture 1 = shot 1). Problems found now are cheap to "
            "fix; after animation they cost a GPU run. Be strict about people, garbled or branded text, objects that "
            "change between pictures when they should be the same, and anything that looks like a 3D render.\n\n"
            "What this Short is meant to be:\n" + brief + "\n\nChecklist (score each 1-10):\n" + items
            + "\n\nAnswer as JSON: scores [{key, score 1-10}], overall (1-10), issues [{shot, atSec (0), problem, fix}] where fix "
            "is e.g. 'redo picture 2 with notes: same green machine', summary (one sentence).")


def check(req: dict, tmp: Path) -> dict:
    if req.get("pictureUris"):  # before clips: the stills
        parts = []
        for n, uri in enumerate(req["pictureUris"], 1):
            parts += [types.Part(text=f"Picture {n}:"), types.Part.from_bytes(data=get_bytes(uri), mime_type="image/png")]
        parts.append(types.Part(text=picture_prompt(req.get("brief") or "")))
    else:  # after the finish: the Short itself, as a small copy
        src = tmp / "short.mp4"
        src.write_bytes(get_bytes(req["videoUri"]))
        data = proxy(src, tmp / "small.mp4").read_bytes()
        parts = [types.Part.from_bytes(data=data, mime_type="video/mp4"), types.Part(text=prompt(req.get("brief") or ""))]

    def call(m: str):
        return gemini().models.generate_content(
            model=m, contents=types.Content(parts=parts),
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Report,
                                               temperature=0.2, http_options=types.HttpOptions(timeout=MEDIA_TIMEOUT_MS)))

    used, r = with_models(req.get("model"), call, what="Dream Short check", default="gemini-3.6-flash")
    rep = r.parsed if isinstance(r.parsed, Report) else Report.model_validate_json(r.text)
    out = rep.model_dump()
    out["scores"] = {s["key"]: max(1, min(10, int(s["score"]))) for s in out["scores"] if s["key"] in CHECKLIST}
    out["overall"] = max(1, min(10, int(out["overall"])))
    log.info("Dream Short %s check: %d/10, %d issues (%s)", req["dreamId"], out["overall"], len(out["issues"]), used)
    return {**out, "model": used, "usage": usage_dict(r), "error": None}


@activity.defn(name="dream_check")
def dream_check(req: dict) -> dict:
    """req: DreamCheckRequest {dreamId, videoUri, brief, model} -> DreamCheckResult {scores, overall, issues, summary,
    model, usage, error}. Never raises for a model problem: the report says what went wrong instead."""
    try:
        with workdir("dream-check-") as tmp:
            return check(req, Path(tmp))
    except Exception as e:  # noqa: BLE001 - a missing check never stops a Short
        log.warning("Dream Short check failed: %s", e)
        return {"scores": {}, "overall": 0, "issues": [], "summary": "", "model": None, "usage": None,
                "error": f"{type(e).__name__}: {str(e)[:200]}"}
