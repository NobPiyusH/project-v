"""Dream Short finish, on the laptop (free): every clip smoothed to 24 fps (RIFE on the laptop GPU when it can, else
FFmpeg motion interpolation), joined with soft dissolves, given the look (pastel haze, glow, film grain, vignette),
scored with a track from the owner's music folder (if set), loudness-normalised for Shorts, saved as one MP4.

Pure helpers (look_filter, dissolve_graph, pick_track) are unit-tested; the activity only wires files together.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path

from temporalio import activity

from workers.common import get_bytes, log, media_duration, put_file, run_ffmpeg, workdir

MUSIC_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}

# Each look as an FFmpeg filter chain applied after scaling to the final size. Dreamcore: lifted blacks and softer
# contrast (haze), a slight pink/teal shift, a soft glow (blurred copy screened over), moving film grain, vignette.
LOOKS = {
    "dreamcore": ("eq=saturation=0.86:contrast=0.9:brightness=0.03:gamma=1.04,"
                  "colorbalance=rs=0.05:gs=-0.01:bs=0.04:rh=0.03:bh=0.02,"
                  "split[base][gl0];[gl0]gblur=sigma=16[gl1];[base][gl1]blend=all_mode=screen:all_opacity=0.25,"
                  "curves=all='0/0.06 0.5/0.52 1/0.97',"
                  "noise=alls=8:allf=t+u,vignette=angle=PI/4.5"),
    "impossible": ("eq=saturation=1.04:contrast=1.03,"
                   "noise=alls=4:allf=t,vignette=angle=PI/5"),
}


def _beat(detail) -> None:
    try:
        activity.heartbeat(detail)
    except RuntimeError:  # outside an activity (tests, manual runs)
        pass


def look_filter(look: str | None) -> str:
    return LOOKS.get(look or "dreamcore", LOOKS["dreamcore"])


def dissolve_graph(durations: list[float], dissolve: float, width: int, height: int, fps: int, look: str | None) -> str:
    """filter_complex joining inputs 0..n-1 with cross-dissolves, then the look; output label [v].

    Each input is scaled/cropped to the final frame first. A dissolve can't be longer than a third of the shorter
    neighbouring clip, so very short clips still show."""
    n = len(durations)
    if n == 0:
        raise ValueError("no clips")
    parts = [f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
             f"crop={width}:{height},fps={fps},format=yuv420p,setsar=1[c{i}]" for i in range(n)]
    last = "c0"
    offset = durations[0]
    for i in range(1, n):
        d = round(max(0.1, min(dissolve, durations[i - 1] / 3, durations[i] / 3)), 3)
        offset -= d
        out = f"x{i}"
        parts.append(f"[{last}][c{i}]xfade=transition=fade:duration={d}:offset={round(offset, 3)}[{out}]")
        offset += durations[i]
        last = out
    parts.append(f"[{last}]{look_filter(look)},format=yuv420p[v]")
    return ";".join(parts)


def total_duration(durations: list[float], dissolve: float) -> float:
    total = durations[0] if durations else 0.0
    for i in range(1, len(durations)):
        total += durations[i] - max(0.1, min(dissolve, durations[i - 1] / 3, durations[i] / 3))
    return total


def pick_track(music_dir: str | None, seed: str) -> Path | None:
    """One track from the owner's folder, chosen by the Short's id (stable on a retry, varied between Shorts)."""
    if not music_dir:
        return None
    root = Path(music_dir)
    if not root.is_dir():
        log.warning("Dream music folder %s not found; finishing without music", music_dir)
        return None
    tracks = sorted(p for p in root.iterdir() if p.suffix.lower() in MUSIC_EXTENSIONS)
    if not tracks:
        return None
    rnd = random.Random(int(hashlib.sha256(seed.encode()).hexdigest(), 16))
    return tracks[rnd.randrange(len(tracks))]


def _smooth(src: Path, dst: Path, src_fps: float, dst_fps: int) -> str:
    """16 -> 24 fps. RIFE on the laptop GPU (same pinned model as the main finish); FFmpeg if it can't run."""
    if src_fps >= dst_fps:
        run_ffmpeg(["-i", str(src), "-an", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(dst)])
        return "none"
    try:
        from workers.local import rife
        if rife.available():
            rife.smooth(src, dst, src_fps, dst_fps)
            return "rife-laptop"
    except Exception as e:  # noqa: BLE001 - e.g. out of GPU memory: FFmpeg instead
        log.warning("Laptop RIFE failed (%s); smoothing with FFmpeg", e)
    run_ffmpeg(["-i", str(src), "-an", "-vf", f"minterpolate=fps={dst_fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1",
                "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(dst)])
    return "ffmpeg"


def finish(req: dict, tmp: Path) -> dict:
    dream_id = req["dreamId"]
    width, height, fps = int(req.get("width") or 1080), int(req.get("height") or 1920), int(req.get("fps") or 24)
    dissolve = float(req.get("dissolveSec") or 0.5)
    smoothed: list[Path] = []
    how: set[str] = set()
    for n, clip in enumerate(req["clips"]):
        src, dst = tmp / f"in{n}.mp4", tmp / f"smooth{n}.mp4"
        src.write_bytes(get_bytes(clip["uri"]))
        how.add(_smooth(src, dst, float(clip.get("fps") or 16), fps))
        smoothed.append(dst)
        _beat({"smoothed": n + 1, "total": len(req["clips"])})
    durations = [media_duration(p) for p in smoothed]
    length = total_duration(durations, dissolve)
    graph = dissolve_graph(durations, dissolve, width, height, fps, req.get("look"))
    track = pick_track(req.get("musicDir"), dream_id)
    args: list[str] = []
    for p in smoothed:
        args += ["-i", str(p)]
    if track:
        args += ["-stream_loop", "-1", "-i", str(track)]
        fade_out = max(0.0, length - 1.5)
        graph += (f";[{len(smoothed)}:a]atrim=0:{length:.3f},afade=t=in:d=1,afade=t=out:st={fade_out:.3f}:d=1.5,"
                  "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]")
    else:  # silent track: platforms and editors expect audio
        args += ["-f", "lavfi", "-t", f"{length:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        graph += f";[{len(smoothed)}:a]anull[a]"
    out = tmp / "dream-short.mp4"
    run_ffmpeg([*args, "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-t", f"{length:.3f}",
                "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(fps),
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
    uri = put_file(f"videos/{dream_id}/dream-short.mp4", out, "video/mp4")
    smoothing = "rife-laptop" if how == {"rife-laptop"} else "ffmpeg" if "ffmpeg" in how else sorted(how)[0]
    log.info("Dream Short %s finished: %.1f s (%s, music: %s)", dream_id, length, smoothing, track.name if track else None)
    return {"uri": uri, "durationSec": round(media_duration(out), 3), "width": width, "height": height, "fps": fps,
            "smoothing": smoothing, "music": track.name if track else None, "error": None}


@activity.defn(name="dream_finish")
def dream_finish(req: dict) -> dict:
    """req: DreamFinishRequest {dreamId, look, clips:[{uri, fps}], width, height, fps, dissolveSec, musicDir, progressUrl}
    -> DreamFinishResult {uri, durationSec, width, height, fps, smoothing, music, error}"""
    with workdir("dream") as d:
        return finish(req, Path(d))
