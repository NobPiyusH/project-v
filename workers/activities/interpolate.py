"""Local frame interpolation of the master (stage INTERPOLATE): RIFE (rife-ncnn-vulkan) on the laptop's own GPU, 24 to 48 fps, audio copied untouched.
Free (no pod, no paid call). The recipe and its pure command builders are tools/film4k/interpolate.py.

Settings (read in the worker's environment, defaults are the owner's laptop): INTERPOLATE_RIFE_EXE, INTERPOLATE_RIFE_MODEL, INTERPOLATE_WORKDIR
(work files go under E:/project-data, never C:). A missing RIFE program is a clear SKIP (the stage is recorded as skipped), not a failure; any other
error raises, so the workflow waits for the owner (Retry / Continue without it, rule 2b).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from temporalio import activity

from tools.film4k import interpolate as rife
from workers.common import download, ffmpeg_bin, ffprobe_bin, log, media_duration, put_file

TOOL = "rife-ncnn-vulkan"


def settings() -> tuple[str, str, str]:
    return (os.getenv("INTERPOLATE_RIFE_EXE") or rife.DEFAULT_EXE, os.getenv("INTERPOLATE_RIFE_MODEL") or rife.DEFAULT_MODEL,
            os.getenv("INTERPOLATE_WORKDIR") or rife.DEFAULT_WORKDIR)


@activity.defn(name="interpolate_master")
def interpolate_master(req: dict) -> dict:
    """req: {videoId, sourceUri, factor}
    -> {skip} | {videoUri, durationSec, width, height, fpsIn, fpsOut, framesIn, framesOut, model, tool, seconds}"""
    exe, model, work = settings()
    if not Path(exe).is_file():
        return {"skip": f"Interpolation skipped: the RIFE program was not found at {exe} (set INTERPOLATE_RIFE_EXE)."}
    factor = int(req.get("factor") or 2)
    started = time.monotonic()
    Path(work).mkdir(parents=True, exist_ok=True)
    scratch = Path(work) / f"job-{req['videoId']}"
    scratch.mkdir(exist_ok=True)
    try:
        src = download(req["sourceUri"], scratch / "source.mp4")
        out = scratch / "interpolated.mp4"
        r = rife.interpolate(str(src), str(out), ffmpeg_bin(), ffprobe_bin(), exe, work, model, factor)
        uri = put_file(f"videos/{req['videoId']}/master/interpolated.mp4", out, "video/mp4")
        duration = media_duration(out)
    finally:
        for p in scratch.glob("*"):
            p.unlink(missing_ok=True)
        scratch.rmdir()
    seconds = round(time.monotonic() - started, 1)
    log.info("Interpolation for %s: %.0f -> %.0f fps, %d -> %d frames, %.1fs of video in %.0fs", req["videoId"], r["fpsIn"], r["fpsOut"], r["framesIn"], r["framesOut"], duration, seconds)
    return {"videoUri": uri, "durationSec": round(duration, 3), "width": r["width"], "height": r["height"], "fpsIn": r["fpsIn"], "fpsOut": r["fpsOut"],
            "framesIn": r["framesIn"], "framesOut": r["framesOut"], "model": r["model"], "tool": TOOL, "seconds": seconds}
