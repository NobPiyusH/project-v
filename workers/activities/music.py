"""Score: Lyria generates the music; it is extended with crossfades to the video's length, then beat-mapped."""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
from google.genai import types
from temporalio import activity

from workers.common import (
    MEDIA_TIMEOUT_MS,
    gemini,
    media_duration,
    model_list,
    put_file,
    run_ffmpeg,
    usage_dict,
    with_models,
    workdir,
)


def _generate(model: str, prompt: str, dest: Path) -> tuple[Path, dict]:
    _, response = with_models(model, lambda m: gemini().models.generate_content(
        model=m, contents=prompt, config=types.GenerateContentConfig(http_options=types.HttpOptions(timeout=MEDIA_TIMEOUT_MS))),
        what="music")
    for part in response.candidates[0].content.parts:
        if part.inline_data and (part.inline_data.mime_type or "").startswith("audio"):
            dest.write_bytes(part.inline_data.data)
            return dest, usage_dict(response)
    raise RuntimeError("Music model returned no audio")


@activity.defn(name="generate_music")
def generate_music(req: dict) -> dict:
    """req: {videoId, model, prompt, targetSec, maxSegments}
    -> {uri, durationSec, bpm, beatTimes[], downbeatTimes[], segments, model, usage[]}"""
    target = float(req["targetSec"])
    max_segments = int(req.get("maxSegments", 5))
    usages = []
    with workdir("music") as tmp:
        tmp = Path(tmp)
        seg_paths = []
        total = 0.0
        while total < target - 2 and len(seg_paths) < max_segments:
            n = len(seg_paths)
            prompt = req["prompt"] if n == 0 else (
                req["prompt"] + f"\nThis is part {n + 1}: continue the same piece seamlessly, same key, tempo and "
                "instrumentation, developing the theme further.")
            path, usage = _generate(req["model"], prompt, tmp / f"seg{n}.mp3")
            usages.append(usage)
            seg_paths.append(path)
            total += media_duration(path) - (4.0 if n > 0 else 0.0)
            activity.heartbeat(n + 1)

        # Join segments with 4 s crossfades, then trim to the target with a gentle fade-out.
        joined = tmp / "joined.wav"
        if len(seg_paths) == 1:
            run_ffmpeg(["-i", str(seg_paths[0]), str(joined)])
        else:
            inputs = sum((["-i", str(p)] for p in seg_paths), [])
            chain, last = [], "[0:a]"
            for i in range(1, len(seg_paths)):
                out = f"[x{i}]"
                chain.append(f"{last}[{i}:a]acrossfade=d=4:c1=tri:c2=tri{out}")
                last = out
            run_ffmpeg([*inputs, "-filter_complex", ";".join(chain), "-map", last, str(joined)])

        final = tmp / "score.wav"
        length = min(target, media_duration(joined))
        fade_start = max(0.0, length - 4.0)
        run_ffmpeg(["-i", str(joined), "-af", f"atrim=0:{length:.3f},afade=t=out:st={fade_start:.3f}:d=4",
                    "-ar", "48000", "-ac", "2", str(final)])

        y, sr = librosa.load(str(final), sr=22050, mono=True)
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
        bpm = float(np.atleast_1d(tempo)[0])
        beat_times = [round(float(t), 3) for t in beats]
        downbeats = beat_times[::4]  # assumes 4/4; good enough for cut placement

        uri = put_file(f"videos/{req['videoId']}/music/score.wav", final, "audio/wav")
        return {
            "uri": uri,
            "durationSec": round(media_duration(final), 3),
            "bpm": round(bpm, 2),
            "beatTimes": beat_times,
            "downbeatTimes": downbeats,
            "segments": len(seg_paths),
            "model": model_list(req["model"])[0],
            "usage": usages,
        }
