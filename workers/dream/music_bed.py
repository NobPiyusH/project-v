"""Dream Short music from the owner's own track (the "music bed"), on the laptop.

Each Short gets its own piece of the track. Several candidate pieces are found (seeded by the Short, away from pieces
other Shorts used, never a quiet stretch), each starting on a strong beat so the music never comes in mid-phrase.
When a model is given, Gemini listens to the candidates next to the Short's pictures and brief and keeps the best
fit (a few paise); otherwise, or if that call fails, the loudest candidate wins. The piece gets soft fades and is
saved to storage as the Short's music; the finish then cuts the shots on its beats.

The track is downloaded from storage once and cached on the laptop (it is long; every Short only needs seconds).
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import subprocess
import tempfile
from pathlib import Path

from google.genai import types
from pydantic import BaseModel
from temporalio import activity

from workers.common import (MEDIA_TIMEOUT_MS, download, ffmpeg_bin, gemini, get_bytes, log, media_duration, put_file,
                            run_ffmpeg, usage_dict, with_models, workdir)

QUIET_DB = -35.0  # a piece whose average loudness is below this is skipped (silence, a fade, a very soft passage)
EDGE_SEC = 5.0  # stay this far from the track's start and end
TRIES = 24  # candidate starts drawn from the seed
SNAP_BACK, SNAP_AHEAD = 1.5, 3.0  # where a start may move to find a strong beat


class Pick(BaseModel):
    best: int  # 1-based piece number
    why: str


def pick_start(seed: str, bed_sec: float, target: float, used: list[float], tries: int = TRIES) -> list[float]:
    """Candidate starts in order of preference: random from the seed, away from used starts when possible."""
    room = bed_sec - target - 2 * EDGE_SEC
    if room <= 0:
        return [0.0]
    rnd = random.Random(int(hashlib.sha256(seed.encode()).hexdigest(), 16))
    fresh, clash = [], []
    for _ in range(tries * 4):
        start = round(EDGE_SEC + rnd.random() * room, 2)
        (clash if any(abs(start - u) < target for u in used) else fresh).append(start)
        if len(fresh) >= tries:
            break
    return (fresh + clash)[:tries]


def mean_volume(path: Path, start: float, length: float) -> float:
    """Average loudness (dB) of a piece of the file, from FFmpeg's volumedetect."""
    out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-ss", f"{start:.2f}", "-t", f"{length:.2f}",
                          "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True, check=False).stderr
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", out)
    return float(m.group(1)) if m else -99.0


def strongest_beat(beats: list[float], strength: list[float], lo: float, hi: float, near: float) -> float | None:
    """The beat in [lo, hi] with the strongest attack (a phrase or bar start), ties going to the one nearest `near`."""
    best = None
    for t, s in zip(beats, strength):
        if lo <= t <= hi and (best is None or (s, -abs(t - near)) > (best[1], -abs(best[0] - near))):
            best = (t, s)
    return None if best is None else best[0]


def snap_to_beat(bed: Path, start: float, tmp: Path) -> float:
    """Move a start onto a strong beat close by, so the piece begins like the start of a phrase."""
    import librosa
    import numpy as np

    lo = max(0.0, start - SNAP_BACK)
    win = tmp / "snap.wav"
    run_ffmpeg(["-ss", f"{lo:.3f}", "-t", f"{SNAP_BACK + SNAP_AHEAD + 2:.3f}", "-i", str(bed), "-vn", "-ac", "1",
                "-ar", "22050", str(win)])
    y, sr = librosa.load(str(win), sr=22050, mono=True)
    env = librosa.onset.onset_strength(y=y, sr=sr)
    _, frames = librosa.beat.beat_track(onset_envelope=env, sr=sr)
    if len(frames) == 0:
        return start
    times = librosa.frames_to_time(frames, sr=sr)
    strength = [float(env[min(f, len(env) - 1)]) for f in frames]
    t = strongest_beat([float(x) for x in times], strength, 0, SNAP_BACK + SNAP_AHEAD, SNAP_BACK)
    return round(lo + float(np.round(t, 3)), 3) if t is not None else start


def _cache_dir() -> Path:
    d = Path(os.getenv("PROJECTV_CACHE_DIR") or Path(tempfile.gettempdir()) / "projectv-cache") / "music-bed"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cached_bed(uri: str) -> Path:
    name = uri.rsplit("/", 1)[-1]
    local = _cache_dir() / (hashlib.sha256(uri.encode()).hexdigest()[:12] + "-" + name)
    if not local.exists() or local.stat().st_size == 0:
        part = local.with_suffix(local.suffix + ".part")
        download(uri, part)
        part.replace(local)
    return local


def cut(bed: Path, start: float, length: float, dst: Path) -> None:
    fade_out = max(0.0, length - 1.5)
    run_ffmpeg(["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(bed), "-vn",
                "-af", f"afade=t=in:d=0.3,afade=t=out:st={fade_out:.3f}:d=1.5,aresample=48000",
                "-ac", "2", "-c:a", "pcm_s16le", str(dst)])


def _preview(bed: Path, start: float, length: float, dst: Path) -> bytes:
    """A small MP3 of a candidate for Gemini to listen to."""
    run_ffmpeg(["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(bed), "-vn", "-ac", "1", "-ar", "24000",
                "-c:a", "libmp3lame", "-b:a", "64k", str(dst)])
    return dst.read_bytes()


def listen_pick(req: dict, bed: Path, starts: list[float], length: float, tmp: Path) -> tuple[int, str, str, dict]:
    """Gemini hears every candidate next to the Short's pictures and brief; returns (index, why, model, usage)."""
    parts: list = [
        "You are the music editor of a short, wordless, music-led vertical video. Below are the video's brief and "
        "pictures, then numbered pieces of one music track. Pick the ONE piece whose mood, energy and feel fits the "
        "video best (it should feel good or nostalgic and suit the pictures); prefer a piece that starts cleanly and "
        "flows, with no abrupt change or ending inside it.\n\nBrief:\n" + (req.get("brief") or "")]
    for uri in (req.get("pictureUris") or [])[:3]:
        try:
            parts.append(types.Part.from_bytes(data=get_bytes(uri), mime_type="image/png"))
        except Exception as e:  # noqa: BLE001 - a missing picture only means less to go on
            log.warning("Music pick: picture %s unavailable (%s)", uri, type(e).__name__)
    for n, s in enumerate(starts, 1):
        parts.append(f"\nPiece {n}:")
        parts.append(types.Part.from_bytes(data=_preview(bed, s, length, tmp / f"cand{n}.mp3"), mime_type="audio/mpeg"))
    parts.append(f"\nAnswer as JSON: best (a piece number from 1 to {len(starts)}) and why (one short sentence).")

    def call(model: str):
        return gemini().models.generate_content(
            model=model, contents=parts,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Pick, temperature=0.2,
                                               http_options=types.HttpOptions(timeout=MEDIA_TIMEOUT_MS)))

    used, response = with_models(req["model"], call, what="Dream Short music pick")
    pick = response.parsed if isinstance(response.parsed, Pick) else Pick.model_validate_json(response.text)
    index = min(max(pick.best, 1), len(starts)) - 1
    return index, pick.why.strip(), used, usage_dict(response)


def music_bed(req: dict, tmp: Path) -> dict:
    bed = cached_bed(req["bedUri"])
    bed_sec = media_duration(bed)
    length = float(req["targetSec"]) + 0.5  # a little over the Short; the finish trims and fades the end
    wanted = max(1, int(req.get("candidates") or 1))
    starts = pick_start(str(req.get("seed") or req["dreamId"]), bed_sec, length, list(req.get("usedStartsSec") or []))
    loud: list[tuple[float, float]] = []  # (start, dB) of pieces that aren't quiet
    fallback = (starts[0], -99.0)
    for s in starts:
        v = mean_volume(bed, s, length)
        if v > fallback[1]:
            fallback = (s, v)
        if v >= QUIET_DB:
            loud.append((s, v))
            if len(loud) >= wanted:
                break
        if activity.in_activity():
            activity.heartbeat({"checked": s})
    if not loud:
        loud = [fallback]
    cands = [snap_to_beat(bed, s, tmp) for s, _ in loud]
    loudest = cands[max(range(len(cands)), key=lambda k: loud[k][1])]
    chosen, why, model, usage = loudest, "picked by loudness", None, None
    if req.get("model") and len(cands) > 1:
        try:
            i, why, model, usage = listen_pick(req, bed, cands, length, tmp)
            chosen = cands[i]
        except Exception as e:  # noqa: BLE001 - the Short goes on with a piece picked by loudness
            why = f"picked by loudness (listening failed: {type(e).__name__})"
            chosen = loudest
            log.warning("Dream Short %s music pick failed: %s", req["dreamId"], str(e)[:200])
    out = tmp / "bed.wav"
    cut(bed, chosen, length, out)
    uri = put_file(f"videos/{req['dreamId']}/music-bed-{int(chosen * 1000)}.wav", out, "audio/wav")
    log.info("Dream Short %s music: %.1f s of %s from %.2f s (%d heard; %s)", req["dreamId"], length, bed.name, chosen,
             len(cands) if usage else 0, why)
    return {"uri": uri, "durationSec": round(media_duration(out), 3), "startSec": chosen, "bedDurationSec": round(bed_sec, 3),
            "bedName": req["bedUri"].rsplit("/", 1)[-1], "model": model, "usage": usage, "why": why,
            "heard": len(cands) if usage else 0}


@activity.defn(name="dream_music_bed")
def dream_music_bed(req: dict) -> dict:
    with workdir("dream-bed-") as tmp:
        return music_bed(req, Path(tmp))
