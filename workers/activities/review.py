"""Creative Director image review: a vision model looks at every keyframe WITH its narration line, its paragraph and
its neighbours, and judges the story the pictures tell at three levels (shot, paragraph/act, whole video).

The rubric comes from the control plane (CreativeDirectorAgent.RUBRIC), so this review and the text review of the
shot plan score the same way. The result is structured JSON; the control plane turns scores into findings, keeps
better prompts for weak shots and redoes only those shots.
"""

from __future__ import annotations

import time
from pathlib import Path

from google.genai import types
from pydantic import BaseModel
from temporalio import activity

from workers.common import download, gemini, log, run_ffmpeg, usage_dict, with_models, workdir

MAX_IMAGES_PER_CALL = 48  # beyond this, the review runs per paragraph (act) so every image keeps enough attention
IMAGE_SIDE = 512          # long side in pixels: enough to judge content and continuity, small enough to be cheap
CALL_TIMEOUT_MS = 180_000


class ShotCritique(BaseModel):
    shot: int
    meaning: int
    accuracy: int
    continuity: int
    literal: bool
    writing: bool = False
    problem: str
    fixPrompt: str


class ActCritique(BaseModel):
    act: str
    flow: int
    keyMoment: int
    continuity: int
    mood: int
    summary: str
    problem: str
    shotsToFix: list[int]


class VideoCritique(BaseModel):
    arc: int
    hook: int
    ending: int
    variety: int
    summary: str
    problem: str


class CreativeReview(BaseModel):
    video: VideoCritique
    acts: list[ActCritique]
    shots: list[ShotCritique]


def _beat(detail: str) -> None:
    try:
        activity.heartbeat(detail)
    except RuntimeError:  # outside an activity (tests, manual runs)
        pass


def _small_jpeg(uri: str, tmp: Path, n: int) -> bytes:
    src = download(uri, tmp / f"s{n}{Path(uri).suffix}")
    out = tmp / f"s{n}.jpg"
    run_ffmpeg(["-i", str(src), "-vf", f"scale='if(gt(iw,ih),{IMAGE_SIDE},-2)':'if(gt(iw,ih),-2,{IMAGE_SIDE})'",
                "-q:v", "4", "-frames:v", "1", str(out)])
    return out.read_bytes()


def _header(req: dict, partial: str | None) -> str:
    lines = [
        "You are the Creative Director of a premium documentary studio. You did not make these images: review them.",
        req.get("rubric") or "",
        "",
        f"Video: {req.get('title')} ({req.get('aspectRatio')}). Visual style: {req.get('visualStyle')}",
    ]
    for key, label in (("logline", "Logline"), ("bigIdea", "Big idea"), ("emotionalArc", "Emotional arc"),
                       ("musicBrief", "Music")):
        if req.get(key):
            lines.append(f"{label}: {req[key]}")
    lines.append("\nFull narration by paragraph (line | starts at | text):")
    act = object()
    for ln in req.get("lines") or []:
        if ln.get("act") != act:
            act = ln.get("act")
            lines.append(f"## {act or '(no act)'}")
        lines.append(f"{ln['index']} | {ln.get('startSec', 0):.1f}s | {ln['text']}")
    if partial:
        lines.append(f"\nThis call shows only the shots of the paragraph “{partial}”. Review those shots and that "
                     "paragraph; for the whole-video scores judge only what you can see, and say so in the summary.")
    lines.append("\nThe images follow in order. Each is preceded by its shot number, line, timing and intent. "
                 "Return only the JSON object, with one entry per shot shown and one per paragraph shown.")
    return "\n".join(lines)


def _review(req: dict, shots: list[dict], tmp: Path, partial: str | None) -> tuple[dict, str, dict]:
    lines = {ln["index"]: ln for ln in req.get("lines") or []}
    parts: list = [_header(req, partial)]
    for s in shots:
        line = lines.get(s.get("lineIndex")) if s.get("lineIndex") is not None else None
        heard = f'line {s["lineIndex"]}: "{line["text"]}"' if line else "no narration (music / visual beat)"
        parts.append(f"\nShot {s['n']} | {s.get('act') or '-'} | {s.get('startSec', 0):.1f}s for "
                     f"{s.get('durationSec', 0):.1f}s | {s.get('priority')} | heard: {heard} | intended: "
                     f"{s.get('description')}")
        parts.append(types.Part.from_bytes(data=_small_jpeg(s["imageUri"], tmp, s["n"]), mime_type="image/jpeg"))
        _beat(f"image {s['n']}")

    def call(model: str):
        return gemini().models.generate_content(
            model=model,
            contents=parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CreativeReview,
                temperature=0.2,
                http_options=types.HttpOptions(timeout=CALL_TIMEOUT_MS),
            ),
        )

    used, response = with_models(req.get("model"), call, what="CREATIVE_REVIEW images", default="gemini-3.6-flash")
    parsed = response.parsed
    review = parsed.model_dump() if isinstance(parsed, BaseModel) else CreativeReview.model_validate_json(response.text).model_dump()
    return review, used, usage_dict(response)


def _chunks(shots: list[dict]) -> list[tuple[str | None, list[dict]]]:
    if len(shots) <= MAX_IMAGES_PER_CALL:
        return [(None, shots)]
    groups: dict[str, list[dict]] = {}
    for s in shots:
        groups.setdefault(s.get("act") or "(no act)", []).append(s)
    out = []
    for act, group in groups.items():
        for i in range(0, len(group), MAX_IMAGES_PER_CALL):
            out.append((act, group[i:i + MAX_IMAGES_PER_CALL]))
    return out


@activity.defn(name="review_images")
def review_images(req: dict) -> dict:
    """req: ImageReviewRequest {videoId, model, rubric, title, aspectRatio, visualStyle, logline, bigIdea, emotionalArc,
             musicBrief, lines:[{index, act, text, startSec}],
             shots:[{n, shotId, lineIndex, act, startSec, durationSec, description, keyframePrompt, priority, imageUri}]}
    -> {review: {video, acts, shots}, model, usage, imagesSent}"""
    shots = [s for s in req.get("shots") or [] if s.get("imageUri")]
    if not shots:
        return {"review": None, "model": None, "usage": None, "imagesSent": 0}
    t = time.time()
    acts, critiques, videos, used = [], [], [], None
    usage = {"promptTokens": 0, "outputTokens": 0, "thoughtTokens": 0, "totalTokens": 0}
    with workdir("review") as tmp:
        for partial, group in _chunks(shots):
            review, used, u = _review(req, group, Path(tmp), partial)
            acts += review.get("acts") or []
            critiques += review.get("shots") or []
            if review.get("video"):
                videos.append(review["video"])
            for k in usage:
                usage[k] += u.get(k, 0)
    # Several calls (long videos): keep the weakest whole-video view, so a problem seen in one paragraph isn't lost.
    video = min(videos, key=lambda v: min(v["arc"], v["hook"], v["ending"], v["variety"])) if videos else None
    log.info("Creative review of %d images with %s took %.0fs", len(shots), used, time.time() - t)
    return {"review": {"video": video, "acts": acts, "shots": critiques}, "model": used, "usage": usage,
            "imagesSent": len(shots)}
