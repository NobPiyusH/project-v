"""Score: Lyria generates the music; it is extended with crossfades to the video's length, then beat-mapped.
A/B candidate (19 Nov 2026, off by default): ACE-Step 1.5 on Modal renders several takes in one call and the take whose
loudness curve best follows the shot energy plan, without clipping, is kept."""

from __future__ import annotations

import random
import time
from pathlib import Path

import librosa
import modal
import numpy as np
import soundfile as sf
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
    """req: {videoId, model, prompt, targetSec, maxSegments, provider?, takes?, energyCurve?}
    -> {uri, durationSec, bpm, beatTimes[], downbeatTimes[], segments, model, usage[], provider,
        ACE-Step only: seed, gpuSeconds, takes[{seed, uri, energyMatch, peakDbfs, clipped, chosen}]}"""
    if req.get("provider") == "modal_acestep":
        return _acestep(req)
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

        out = _finish(joined, target, tmp, req["videoId"])
        return {**out, "segments": len(seg_paths), "model": model_list(req["model"])[0], "usage": usages,
                "provider": "gemini_lyria"}


def _finish(source: Path, target: float, tmp: Path, video_id: str) -> dict:
    """Trim to the target with a gentle fade-out, store as the video's score, beat-map it."""
    final = tmp / "score.wav"
    length = min(target, media_duration(source))
    fade_start = max(0.0, length - 4.0)
    run_ffmpeg(["-i", str(source), "-af", f"atrim=0:{length:.3f},afade=t=out:st={fade_start:.3f}:d=4",
                "-ar", "48000", "-ac", "2", str(final)])

    y, sr = librosa.load(str(final), sr=22050, mono=True)
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    bpm = float(np.atleast_1d(tempo)[0])
    beat_times = [round(float(t), 3) for t in beats]
    downbeats = beat_times[::4]  # assumes 4/4; good enough for cut placement

    uri = put_file(f"videos/{video_id}/music/score.wav", final, "audio/wav")
    return {"uri": uri, "durationSec": round(media_duration(final), 3), "bpm": round(bpm, 2),
            "beatTimes": beat_times, "downbeatTimes": downbeats}


# ---------------------------------------------------------------------------------------------------- ACE-Step

CLIP_LEVEL = 0.999   # |sample| at or above this is full scale
CLIP_RUN = 3         # this many full-scale samples in a row = a flat-topped (clipped) waveform


def clipped(samples: np.ndarray) -> bool:
    """True when any channel has CLIP_RUN or more consecutive full-scale samples (a flat top, i.e. real clipping)."""
    full = np.abs(np.atleast_2d(samples.T)) >= CLIP_LEVEL  # [channels, samples]
    for ch in full:
        run = 0
        for hit in np.flatnonzero(np.diff(np.concatenate(([0], ch.astype(np.int8), [0]))) != 0).reshape(-1, 2):
            run = max(run, hit[1] - hit[0])
        if run >= CLIP_RUN:
            return True
    return False


def loudness_curve(samples: np.ndarray, sr: int, seconds: int) -> np.ndarray:
    """Short-term loudness (RMS in dB over 1 s windows, 0.5 s hop), smoothed over 2 s, one value per second."""
    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    win, hop = sr, sr // 2
    rms = [np.sqrt(np.mean(mono[i:i + win] ** 2) + 1e-12) for i in range(0, max(1, len(mono) - win + 1), hop)]
    db = 20 * np.log10(np.asarray(rms))
    db = np.convolve(db, np.ones(4) / 4, mode="same")  # 4 hops = 2 s
    t = np.arange(len(db)) * 0.5 + 0.5
    return np.interp(np.arange(seconds) + 0.5, t, db)


def energy_match(samples: np.ndarray, sr: int, curve: list[float]) -> float:
    """Pearson correlation between the music's loudness curve and the shot energy plan (-1..1; 0 when no plan)."""
    if len(curve) < 4:
        return 0.0
    plan = np.asarray(curve, dtype=float)
    loud = loudness_curve(samples, sr, len(plan))
    if plan.std() < 1e-6 or loud.std() < 1e-6:
        return 0.0
    return float(np.corrcoef(plan, loud)[0, 1])


def pick_take(scored: list[dict]) -> dict:
    """Best energy match among the takes that do not clip. If every take clips, fail: a clipped score is never used."""
    clean = [t for t in scored if not t["clipped"]]
    if not clean:
        raise RuntimeError("Every ACE-Step take clipped; retry the stage for new seeds")
    return max(clean, key=lambda t: t["energyMatch"])


def _acestep(req: dict) -> dict:
    target = float(req["targetSec"])
    takes = max(1, int(req.get("takes") or 3))
    seeds = [random.randint(1, 2**31 - 1) for _ in range(takes)]
    ace = modal.Cls.from_name(req.get("modalApp") or "projectv-acestep", req.get("modalClass") or "AceStep")()
    t = time.time()
    out = ace.generate.remote({"caption": req["prompt"], "durationSec": round(target + 2.0, 1), "seeds": seeds})
    wall = time.time() - t  # container start + model load + takes: what Modal bills for this call
    activity.heartbeat("takes rendered")
    curve = req.get("energyCurve") or []
    with workdir("music") as tmp:
        tmp = Path(tmp)
        scored = []
        for i, take in enumerate(out["takes"]):
            path = tmp / f"take{i + 1}.wav"
            path.write_bytes(take["wav"])
            samples, sr = sf.read(str(path), always_2d=True)
            peak = float(np.max(np.abs(samples))) if samples.size else 0.0
            scored.append({"seed": take["seed"], "path": path, "clipped": clipped(samples),
                           "peakDbfs": round(20 * np.log10(max(peak, 1e-9)), 2),
                           "energyMatch": round(energy_match(samples, sr, curve), 3)})
        best = pick_take(scored)
        takes_out = []
        for i, s in enumerate(scored):
            uri = put_file(f"videos/{req['videoId']}/music/acestep-take{i + 1}-{s['seed']}.wav", s["path"], "audio/wav")
            takes_out.append({"seed": s["seed"], "uri": uri, "energyMatch": s["energyMatch"], "peakDbfs": s["peakDbfs"],
                              "clipped": s["clipped"], "chosen": s is best})
        result = _finish(best["path"], target, tmp, req["videoId"])
    activity.logger.info("ACE-Step: %d takes in %.0f s (GPU call %.0f s, load %s s); chose seed %s, match %.3f",
                         len(scored), wall, out.get("seconds") or 0, out.get("loadSeconds"), best["seed"], best["energyMatch"])
    return {**result, "segments": 1, "model": req["model"], "usage": [], "provider": "modal_acestep",
            "seed": best["seed"], "gpuSeconds": round(wall, 1), "takes": takes_out}
