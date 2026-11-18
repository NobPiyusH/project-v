"""Dream Short music from the owner's own track (the "music bed"), on the laptop (free).

Each Short gets its own piece of the track: a start picked from the Short's seed, kept away from the pieces other
Shorts already used, and never a quiet stretch (checked with FFmpeg's volumedetect). The piece gets soft fades and
is saved to storage as the Short's music; the finish then mixes it like any score.

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

from temporalio import activity

from workers.common import download, ffmpeg_bin, log, media_duration, put_file, run_ffmpeg, workdir

QUIET_DB = -35.0  # a piece whose average loudness is below this is skipped (silence, a fade, a very soft passage)
EDGE_SEC = 5.0  # stay this far from the track's start and end
TRIES = 12


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
                "-af", f"afade=t=in:d=0.8,afade=t=out:st={fade_out:.3f}:d=1.5,aresample=48000",
                "-ac", "2", "-c:a", "pcm_s16le", str(dst)])


def music_bed(req: dict, tmp: Path) -> dict:
    bed = cached_bed(req["bedUri"])
    bed_sec = media_duration(bed)
    length = float(req["targetSec"]) + 0.5  # a little over the Short; the finish trims and fades the end
    starts = pick_start(str(req.get("seed") or req["dreamId"]), bed_sec, length, list(req.get("usedStartsSec") or []))
    chosen, loud = starts[0], -99.0
    for s in starts:
        v = mean_volume(bed, s, length)
        if v > loud:
            chosen, loud = s, v
        if v >= QUIET_DB:
            break
        if activity.in_activity():
            activity.heartbeat({"skipped_quiet": s})
    out = tmp / "bed.wav"
    cut(bed, chosen, length, out)
    uri = put_file(f"videos/{req['dreamId']}/music-bed-{int(chosen * 1000)}.wav", out, "audio/wav")
    log.info("Dream Short %s music: %.1f s of %s from %.1f s (%.1f dB)", req["dreamId"], length, bed.name, chosen, loud)
    return {"uri": uri, "durationSec": round(media_duration(out), 3), "startSec": chosen, "bedDurationSec": round(bed_sec, 3),
            "bedName": req["bedUri"].rsplit("/", 1)[-1]}


@activity.defn(name="dream_music_bed")
def dream_music_bed(req: dict) -> dict:
    with workdir("dream-bed-") as tmp:
        return music_bed(req, Path(tmp))
