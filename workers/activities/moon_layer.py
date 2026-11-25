"""Moon layer composite (stage MOON_LAYER): the plan's Moon picture (a LOCAL file, nothing downloaded) over the finished master, with the scale curve,
glow and caption of tools/film4k/moon_layer.py. Free: one ffmpeg run on the laptop. The master's own size, frame rate and audio are kept.

A picture that is missing is a non-retryable error (retrying cannot fix it): the stage then waits for the owner like any failed stage.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError

from tools.film4k import moon_layer as ml
from workers.common import download, ffmpeg_bin, ffprobe_bin, log, media_duration, put_file, run_ffmpeg, workdir

INPUT_MISSING = "MoonLayerInputMissing"
# plan key (camelCase) -> composite_cmd argument
LAYER_ARGS = {"diameterFrac": "diameter_frac", "scaleFrom": "scale_from", "scaleTo": "scale_to", "ease": "ease", "cx": "cx", "cyFrom": "cy_from",
              "cyTo": "cy_to", "glow": "glow", "glowScale": "glow_scale"}


def probe(path: Path) -> dict:
    """Size, frame rate and whether there is an audio track."""
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    streams = json.loads(out.stdout)["streams"]
    v = next(s for s in streams if s["codec_type"] == "video")
    num, _, den = v["r_frame_rate"].partition("/")
    fps = float(num) / float(den or 1)
    return {"width": int(v["width"]), "height": int(v["height"]), "fps": round(fps), "audio": any(s["codec_type"] == "audio" for s in streams)}


def build_command(plate: Path, moon_png: Path, out: Path, req: dict, info: dict, dur: float) -> list[str]:
    kwargs = {LAYER_ARGS[k]: v for k, v in (req.get("layer") or {}).items() if k in LAYER_ARGS}
    cap = req.get("caption")
    if cap:
        kwargs.update(caption=cap["text"], caption_start=float(cap.get("startSec", 0.0)), caption_end=float(cap.get("endSec", 4.0)))
    # The growth curve spans the whole master, so it is continuous from the first to the last frame.
    cmd = ml.composite_cmd(str(plate), str(moon_png), str(out), w=info["width"], h=info["height"], dur=dur, fps=info["fps"], keep_audio=info["audio"], **kwargs)
    cmd[0] = ffmpeg_bin()
    return cmd


@activity.defn(name="moon_layer_composite")
def moon_layer_composite(req: dict) -> dict:
    """req: {videoId, plateUri, imagePath, keyBlack, layer{...}, caption{text,startSec,endSec}|None}
    -> {videoUri, durationSec, width, height, fps, imageSha256}"""
    image = Path(req["imagePath"]) if req.get("imagePath") else None
    if image is None or not image.is_file():
        raise ApplicationError(f"The Moon picture was not found: set moon.layer.imagePath in the plan to a local image file (got {req.get('imagePath')!r}).",
                               type=INPUT_MISSING, non_retryable=True)
    with workdir("moon") as tmp:
        tmp = Path(tmp)
        plate = download(req["plateUri"], tmp / "plate.mp4")
        moon_png = image
        if req.get("keyBlack", True):  # a Moon on a black sky -> transparent (harmless on a picture that already has alpha)
            moon_png = tmp / "moon_keyed.png"
            run_ffmpeg(ml.moon_matte_cmd(str(image), str(moon_png))[2:])
        info = probe(plate)
        dur = media_duration(plate)
        out = tmp / "moon-layer.mp4"
        cmd = build_command(plate, moon_png, out, req, info, dur)
        run_ffmpeg(cmd[2:])  # drop the program and -y: run_ffmpeg adds its own
        uri = put_file(f"videos/{req['videoId']}/master/moon-layer.mp4", out, "video/mp4")
        duration = media_duration(out)
        sha = hashlib.sha256(image.read_bytes()).hexdigest()
    log.info("Moon layer for %s: %dx%d %.1fs", req["videoId"], info["width"], info["height"], duration)
    return {"videoUri": uri, "durationSec": round(duration, 3), "width": info["width"], "height": info["height"], "fps": info["fps"], "imageSha256": sha}
