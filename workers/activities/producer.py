"""AI Producer review (P1, owner 18 Nov 2026; docs/plans/ai-producer.md): a multimodal Gemini model judges a stage's
output the way a producer would. It reads text, looks at images, WATCHES video and LISTENS to audio, item by item,
against the stage rubric and the owner's learned rules, and returns a verdict (approve / redo / ask) with precise redo
notes. Media is shrunk before sending (small JPEGs, 360p clips) so a review stays cheap; large batches are split.
"""

from __future__ import annotations

import time
from pathlib import Path

from google.genai import types
from pydantic import BaseModel
from temporalio import activity

from workers.common import beat as _beat, download, gemini, log, run_ffmpeg, usage_dict, with_models, workdir

CALL_TIMEOUT_MS = 240_000
MAX_ITEMS_PER_CALL = 12
MAX_BYTES_PER_CALL = 14 * 1024 * 1024   # inline media limit per request is ~20 MB; keep headroom


class ItemVerdict(BaseModel):
    id: str
    score: int
    ok: bool
    problem: str
    fix: str


class Review(BaseModel):
    verdict: str          # approve | redo | ask
    confidence: float
    summary: str
    redoNotes: str
    items: list[ItemVerdict]


def _media_part(item: dict, tmp: Path, n: int) -> tuple[types.Part | None, int]:
    """The item's media, shrunk: images to a 512 px JPEG, video to a small 360p MP4 (with sound), audio to mono MP3."""
    kind, uri = item.get("kind"), item.get("uri")
    if kind == "text" or not uri:
        return None, 0
    src = download(uri, tmp / f"in{n}{Path(uri).suffix or '.bin'}")
    if kind == "image":
        out = tmp / f"m{n}.jpg"
        run_ffmpeg(["-i", str(src), "-vf", "scale='if(gt(iw,ih),512,-2)':'if(gt(iw,ih),-2,512)'", "-q:v", "4",
                    "-frames:v", "1", str(out)])
        mime = "image/jpeg"
    elif kind == "video":
        out = tmp / f"m{n}.mp4"
        run_ffmpeg(["-i", str(src), "-vf", "scale='if(gt(iw,ih),-2,360)':'if(gt(iw,ih),360,-2)'", "-r", "12",
                    "-c:v", "libx264", "-crf", "32", "-preset", "veryfast", "-c:a", "aac", "-b:a", "48k", "-ac", "1",
                    str(out)])
        mime = "video/mp4"
    else:  # audio
        out = tmp / f"m{n}.mp3"
        run_ffmpeg(["-i", str(src), "-ac", "1", "-b:a", "64k", str(out)])
        mime = "audio/mpeg"
    data = out.read_bytes()
    return types.Part.from_bytes(data=data, mime_type=mime), len(data)


def _header(req: dict) -> str:
    rules = req.get("rules") or []
    lines = [req.get("rubric") or "", "", req.get("context") or ""]
    if rules:
        lines += ["", "The owner's rules and recent feedback (follow them; they come from his own decisions):"]
        lines += [f"- {r}" for r in rules]
    lines += ["", f"Stage: {req.get('stage')}. The items follow, each with its id, what it should be, and its content. "
              "Return only the JSON object with one entry per item shown."]
    return "\n".join(lines)


def _review_chunk(req: dict, chunk: list[tuple[dict, types.Part | None]]) -> tuple[dict, str, dict]:
    parts: list = [_header(req)]
    for item, media in chunk:
        parts.append(f"\nItem {item['id']} ({item.get('label')}) | should be: {item.get('expect')}")
        if item.get("kind") == "text":
            parts.append(item.get("text") or "")
        elif media is not None:
            parts.append(media)

    def call(model: str):
        return gemini().models.generate_content(
            model=model, contents=parts,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Review,
                                               temperature=0.2, http_options=types.HttpOptions(timeout=CALL_TIMEOUT_MS)))

    used, response = with_models(req.get("model"), call, what=f"PRODUCER review {req.get('stage')}",
                                 default="gemini-3.6-flash")
    parsed = response.parsed
    review = parsed.model_dump() if isinstance(parsed, BaseModel) else Review.model_validate_json(response.text).model_dump()
    return review, used, usage_dict(response)


def _combine(reviews: list[dict]) -> dict:
    """Several calls (many items): the strictest verdict wins (ask > redo > approve), the lowest confidence is kept."""
    rank = {"approve": 0, "redo": 1, "ask": 2}
    verdict = max((r.get("verdict", "ask").lower() for r in reviews), key=lambda v: rank.get(v, 2))
    return {"verdict": verdict, "confidence": min(float(r.get("confidence") or 0) for r in reviews),
            "summary": " ".join(r.get("summary") or "" for r in reviews).strip(),
            "redoNotes": " ".join(r.get("redoNotes") or "" for r in reviews if r.get("redoNotes")).strip(),
            "items": [i for r in reviews for i in (r.get("items") or [])]}


@activity.defn(name="producer_review")
def producer_review(req: dict) -> dict:
    """req: ProducerReviewRequest {videoId, stage, model, rubric, context, rules[], items:[{id, label, kind, uri, text, expect}]}
    -> ProducerReviewResult {verdict, confidence, summary, redoNotes, items:[{id, score, ok, problem, fix}], model, usage}"""
    items = req.get("items") or []
    t = time.time()
    usage = {"promptTokens": 0, "outputTokens": 0, "thoughtTokens": 0, "totalTokens": 0}
    reviews, used = [], None
    with workdir("producer") as tmp:
        chunk, size = [], 0
        for n, item in enumerate(items):
            media, nbytes = _media_part(item, Path(tmp), n)
            if chunk and (len(chunk) >= MAX_ITEMS_PER_CALL or size + nbytes > MAX_BYTES_PER_CALL):
                r, used, u = _review_chunk(req, chunk)
                reviews.append(r)
                usage = {k: usage[k] + u.get(k, 0) for k in usage}
                chunk, size = [], 0
            chunk.append((item, media))
            size += nbytes
            _beat(f"{req.get('stage')} item {n + 1}/{len(items)}")
        if chunk:
            r, used, u = _review_chunk(req, chunk)
            reviews.append(r)
            usage = {k: usage[k] + u.get(k, 0) for k in usage}
    out = _combine(reviews) if reviews else {"verdict": "ask", "confidence": 0, "summary": "Nothing to review.",
                                              "redoNotes": "", "items": []}
    log.info("Producer reviewed %d %s items with %s in %.0fs: %s", len(items), req.get("stage"), used,
             time.time() - t, out["verdict"])
    return {**out, "model": used, "usage": usage}
