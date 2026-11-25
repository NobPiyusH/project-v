"""Dry run of a plan file for the dashboard's "Load plan file" box (26 Nov 2026): validates it with the same loader POST /api/videos uses
(`studio.planner.imported.parse`), and summarises what the owner is about to start. Nothing is stored, started or spent.

Also the one list of stages that call PAID services, shown before Start and next to each stage of a video (GET /api/stages `paid`).
A stage's mode (Auto / Review / Manual) is applied AFTER the stage has run; what stops a paid stage BEFORE it spends is the hold before run
(`control.hold_check`, workflow patch 2026-11-25-hold-before-run): a paid stage in Manual mode, or any paid stage of a video with
`approve_paid_first` (on by default for a video made from a plan file), waits for Approve / Skip first. A stage that the plan can leave
out entirely (music, narration) is also offered as "leave out", which the dashboard sends as `video.music = "none"` in the plan."""

from __future__ import annotations

import math
import uuid
from typing import Any

from studio.media.types import ClipJob
from studio.planner.imported import ImportedPlan, parse
from studio.production import camera
from studio.production.video_gpu import DRAFT

# stage id -> what it calls (plain words). Free stages of an imported plan (PLAN, CONCEPT, SCRIPT, SHOT_PLAN placement) are not listed.
PAID_STAGES: dict[str, str] = {
    "SHOT_PLAN": "AI story review of the shot plan (a paid text model)",
    "MUSIC": "Music from Lyria (Gemini, paid per track)",
    "NARRATION": "Narration voice from the paid voice provider (and an AI check of every line)",
    "KEYFRAMES": "Pictures on a rented GPU pod, plus an AI picture review (paid)",
    "DRAFT_GEN": "Video clips on a rented GPU pod (asks you to approve the estimate first)",
    "FINAL_GEN": "Final clips (GPU pod or Gemini Omni; asks you first)",
    "CRITIC": "AI quality check of every take (a paid model)",
}
# Stages that are placeholders today but will call a paid service when built (priced in pricing.holdEstimates, 0 = nothing to spend yet):
# they join PAID_STAGES in HOLDABLE, the set of stages the workflow may hold before they run. Not shown as paid in the plan file box.
PAID_WHEN_BUILT: dict[str, str] = {
    "UPSCALE": "GPU upscaling and smoothing on a rented pod (asks you first)",
    "TAKE_SELECTION": "AI pick of the best take (not built yet)",
    "AUDIO": "Final audio mix (not built yet)",
    "PACKAGING": "Captions, titles and thumbnails (not built yet)",
}
HOLDABLE: dict[str, str] = {**PAID_STAGES, **PAID_WHEN_BUILT}
# paid stages the plan itself can drop before the video starts: stage id -> (VideoPlan field, value that removes the stage)
LEAVE_OUT = {"MUSIC": ("music", "none"), "NARRATION": ("narration", "none")}


def paid_note(stage: str) -> str | None:
    return PAID_STAGES.get(stage)


def estimate(v: Any, imp: ImportedPlan) -> dict[str, Any]:
    """The DRAFT_GEN estimate for this plan: the same pod-seconds and price functions the studio's estimate uses (VideoGpu.clip_seconds / inr
    on the broker route, else the Modal H100 price), over one job per beat built the way VideoStage.jobs builds them."""
    vs = v.video
    vertical = imp.height > imp.width
    cams_on = bool(vs.camera_presets) and vertical and v.video_gpu is not None and v.video_gpu.clips(DRAFT)
    jobs: list[ClipJob] = []
    for i, b in enumerate(imp.beats):
        if cams_on and b.camera:
            jobs.append(ClipJob("plan", i + 1, 1, DRAFT, "camera", "", b.prompt, camera.WIDTH, camera.HEIGHT, camera.FRAMES, 0, camera=b.camera))
        else:
            n = _frames(b.durationSec)
            jobs.append(ClipJob("plan", i + 1, 1, DRAFT, "fast", "", b.prompt, 480 if vertical else 832, 832 if vertical else 480, n, 0))
    fx = float(v.pricing.fx())
    if v.video_gpu is not None and v.video_gpu.clips(DRAFT):
        sec = v.video_gpu.clip_seconds(uuid.uuid4(), jobs)
        inr = float(v.video_gpu.inr(sec))
        where = "a rented GPU pod at the cheapest live price"
    else:
        sec = vs.gpu_seconds(jobs, DRAFT)
        inr = float(v.pricing.modal_gpu_inr("H100", sec))
        where = "Modal H100"
    return {"clips": len(jobs), "minutes": math.ceil(sec / 60), "inrLow": round(inr), "inrHigh": round(inr * 2), "usdLow": round(inr / fx, 2),
            "usdHigh": round(inr * 2 / fx, 2), "where": where,
            "note": f"Draft clips only, on {where}. The high figure is a second take of every clip. Pictures, music, voice and checks are extra."}


def _frames(seconds: float) -> int:
    from studio.production.video_stage import frames
    return frames(seconds)


def summary(v: Any, doc: dict[str, Any]) -> dict[str, Any]:
    """{"ok": True, ...summary} or {"ok": False, "error": plain words}. Never raises for a bad plan."""
    try:
        imp = parse(doc)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        return {"ok": False, "error": _plain(e)}
    vp = imp.video
    beats = [{"n": i + 1, "startSec": b.startSec, "durationSec": b.durationSec, "camera": b.camera, "prompt": b.prompt}
             for i, b in enumerate(imp.beats)]
    starts = imp.line_starts()
    lines = [{"n": i + 1, "text": ln.text, "beat": ln.beat, "startSec": starts.get(i)} for i, ln in enumerate(imp.lines)]
    moon = imp.moon
    layer = (moon or {}).get("layer") or {}
    out: dict[str, Any] = {
        "ok": True, "title": vp.title or "Imported plan", "totalSec": imp.total_sec(), "width": imp.width, "height": imp.height,
        "beats": beats, "lines": lines, "cameraPresets": sorted({b.camera for b in imp.beats if b.camera}),
        "moonLayer": moon is not None, "moonImage": layer.get("imagePath") if isinstance(layer, dict) else None,
        "music": vp.music, "narration": vp.narration,
        "paidStages": [{"id": k, "note": n, "leaveOut": k in LEAVE_OUT} for k, n in PAID_STAGES.items()],
    }
    try:
        out["estimate"] = estimate(v, imp)
    except Exception as e:  # noqa: BLE001 - the estimate must never hide a valid plan
        out["estimate"] = None
        out["estimateError"] = f"No estimate: {type(e).__name__}"
    return out


def _plain(e: BaseException) -> str:
    if isinstance(e, KeyError):
        return f"plan is missing '{e.args[0]}'" if e.args else "plan is incomplete"
    return str(e) or type(e).__name__
