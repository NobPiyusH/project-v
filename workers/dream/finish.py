"""Dream Short finish, on the laptop (free): every clip smoothed to 24 fps (RIFE on the laptop GPU when it can, else
FFmpeg motion interpolation), joined with soft dissolves, given the look (pastel haze, glow, film grain, vignette),
scored with a track from the owner's music folder (if set), loudness-normalised for Shorts, saved as one MP4.

Pure helpers (look_filter, dissolve_graph, pick_track) are unit-tested; the activity only wires files together.
"""

from __future__ import annotations

import hashlib
import random
import re
import subprocess
from pathlib import Path

from temporalio import activity

from workers.common import ffmpeg_bin, get_bytes, log, media_duration, put_file, run_ffmpeg, workdir

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
    # The owner's examples (18 Nov 2026) are clean: no haze, no grain, no vignette; vivid colour, crisp detail.
    # A whisper of moving grain (alls=2) breaks up the too-smooth "AI" surface without looking like a filter.
    # Shadows are lifted a touch first (the 19 Nov test crushed the dark ground to pure black).
    "lonely": "curves=all='0/0.035 0.2/0.215 1/1',eq=saturation=1.12:contrast=1.04,unsharp=5:5:0.5:5:5:0,noise=alls=2:allf=t",
    "drive": "curves=all='0/0.035 0.2/0.215 1/1',eq=saturation=1.1:contrast=1.04,unsharp=5:5:0.5:5:5:0,noise=alls=2:allf=t",
    "walkthrough": ("curves=all='0/0.03 0.2/0.21 1/1',eq=saturation=1.08:contrast=1.03:brightness=0.01,"
                    "unsharp=5:5:0.4:5:5:0,noise=alls=2:allf=t"),
}


def _beat(detail) -> None:
    try:
        activity.heartbeat(detail)
    except RuntimeError:  # outside an activity (tests, manual runs)
        pass


# How a hand-held phone moves (owner, 19 Nov 2026: Wan's glide reads as AI, not a person). Pixels of drift at 1080 wide
# as (amplitude, frequency Hz) pairs for x and y; slow, irregular sums of sines, never repeating within a shot.
HANDHELD = {
    "stand": {"x": [(5, 0.17), (3, 0.41)], "y": [(4, 0.13), (2, 0.53)]},   # standing still, breathing
    "walk": {"x": [(6, 0.45), (2, 0.19)], "y": [(5, 1.8), (3, 0.23)]},     # slow steps: bob + sway
    "car": {"x": [(1.5, 4.7), (2, 0.3)], "y": [(2, 6.3), (2, 9.1)]},       # road vibration
}
HANDHELD_MARGIN = 1.04  # zoom in 4% so the drift never shows an edge


def handheld_filter(kind: str | None, width: int, height: int, shot: int) -> str:
    """Crop that drifts like a hand-held phone ('' for none). Phases differ per shot so cuts don't repeat a move."""
    spec = HANDHELD.get(kind or "none")
    if not spec:
        return ""
    scale = width / 1080

    def axis(name: str, centre: str) -> str:
        terms = "".join(f"+{a * scale:.2f}*sin(2*PI*{f}*t+{(shot * 1.7 + k * 2.3 + (name == 'y')):.2f})"
                        for k, (a, f) in enumerate(spec[name]))
        return f"{centre}{terms}"

    return (f",scale={round(width * HANDHELD_MARGIN / 2) * 2}:{round(height * HANDHELD_MARGIN / 2) * 2}:flags=lanczos,"
            f"crop={width}:{height}:x='{axis('x', '(iw-ow)/2')}':y='{axis('y', '(ih-oh)/2')}'")


def look_filter(look: str | None) -> str:
    return LOOKS.get(look or "dreamcore", LOOKS["dreamcore"])


def dissolve_graph(durations: list[float], dissolve: float, width: int, height: int, fps: int, look: str | None,
                   handheld: str | None = None) -> str:
    """filter_complex joining inputs 0..n-1 with cross-dissolves, then the look; output label [v].

    Each input is scaled/cropped to the final frame first. A dissolve can't be longer than a third of the shorter
    neighbouring clip, so very short clips still show."""
    n = len(durations)
    if n == 0:
        raise ValueError("no clips")
    parts = [f"[{i}:v]trim=duration={durations[i]:.3f},setpts=PTS-STARTPTS,"
             f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
             f"crop={width}:{height}{handheld_filter(handheld, width, height, i)},fps={fps},format=yuv420p,setsar=1[c{i}]"
             for i in range(n)]
    if dissolve <= 0:  # hard cuts
        parts.append("".join(f"[c{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[x]")
        parts.append(f"[x]{look_filter(look)},format=yuv420p[v]")
        return ";".join(parts)
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


def _dissolve_at(durations: list[float], dissolve: float, i: int) -> float:
    return round(max(0.1, min(dissolve, durations[i - 1] / 3, durations[i] / 3)), 3) if dissolve > 0 else 0.0


def beat_synced(durations: list[float], dissolve: float, beats: list[float], max_trim: float = 1.0) -> list[float]:
    """Shorten clips (never lengthen) so every cut, or the middle of every dissolve, lands on a beat of the music.

    Each clip may lose at most `max_trim` seconds (and never more than a third of itself); a cut with no beat that
    close keeps its natural place. The last clip is untouched (the music fades out over it)."""
    out = list(durations)
    base = 0.0  # where the current clip starts on the timeline
    for i in range(len(out) - 1):
        d = _dissolve_at(durations, dissolve, i + 1)
        natural = base + out[i] - d / 2  # cut point (or dissolve middle) with the clip at full length
        trim = min(max_trim, out[i] / 3)
        hits = [b for b in beats if natural - trim <= b <= natural + 1e-6]
        if hits:
            out[i] = round(out[i] - (natural - max(hits)), 3)
        base += out[i] - d
    return out


def track_beats(path: Path, seconds: float) -> list[float]:
    """Beat times (s) of the first `seconds` of a music file; [] if it has no clear beat."""
    try:
        import librosa
        y, sr = librosa.load(str(path), sr=22050, mono=True, duration=seconds)
        _, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
        return [round(float(t), 3) for t in beats]
    except Exception as e:  # noqa: BLE001 - no beat sync, the Short still finishes
        log.warning("No beats found in %s (%s); cuts keep their natural places", path.name, e)
        return []


def total_duration(durations: list[float], dissolve: float) -> float:
    total = durations[0] if durations else 0.0
    if dissolve <= 0:
        return sum(durations)
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


def _smooth(src: Path, dst: Path, src_fps: float, dst_fps: int, speed: float = 1.0) -> str:
    """16 -> 24 fps, played at `speed` (0.7 = 30% slower, new in-between frames fill the gaps). RIFE on the laptop GPU
    (same pinned model as the main finish); FFmpeg if it can't run."""
    src_fps = src_fps * speed  # a slowed clip is a clip shot at a lower frame rate, then interpolated up
    if src_fps >= dst_fps and speed == 1.0:
        run_ffmpeg(["-i", str(src), "-an", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(dst)])
        return "none"
    try:
        from workers.local import rife
        if rife.available():
            rife.smooth(src, dst, src_fps, dst_fps)
            return "rife-laptop"
    except Exception as e:  # noqa: BLE001 - e.g. out of GPU memory: FFmpeg instead
        log.warning("Laptop RIFE failed (%s); smoothing with FFmpeg", e)
    run_ffmpeg(["-i", str(src), "-an", "-vf", f"setpts=PTS/{speed},minterpolate=fps={dst_fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1",
                "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", str(dst)])
    return "ffmpeg"


CUT_JUMP = 12.0  # mean luma change between frames above which we call it a cut (measured on the owner's examples)


def summarise(motion: list[float], saturation: list[float], low: list[float]) -> dict:
    """Per-frame numbers -> the Short's metrics (pure, unit-tested)."""
    moving = [m for m in motion if m < CUT_JUMP]
    cuts = sum(1 for m in motion if m >= CUT_JUMP)
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else 0.0  # noqa: E731
    return {"motion": avg(moving), "cuts": float(cuts), "colour": avg(saturation), "shadows": avg(low)}


def measure(path: Path, tmp: Path) -> dict:
    """What a finished Short measures, for the learning loop: motion between frames (cuts left out), number of cuts,
    colour strength, the level of its darkest tenth (shadows), loudness. The same checks used on the owner's examples."""
    def series(vf: str, key: str) -> list[float]:
        f = tmp / f"m-{key}.txt"
        # run inside tmp: a Windows path (C:...) can't go inside a filter's file= option
        subprocess.run([ffmpeg_bin(), "-hide_banner", "-y", "-i", str(path), "-an", "-vf",
                        f"scale=180:320,{vf}signalstats,metadata=print:key=lavfi.signalstats.{key}:file={f.name}",
                        "-f", "null", "-"], cwd=tmp, capture_output=True, check=True)
        return [float(x) for x in re.findall(rf"{key}=([0-9.]+)", f.read_text())]
    try:
        out = summarise(series("tblend=all_mode=difference,", "YAVG"), series("", "SATAVG"), series("", "YLOW"))
        loud = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                              capture_output=True, text=True, check=False).stderr
        m = re.findall(r"I:\s+(-?[\d.]+) LUFS", loud)
        if m:
            out["loudnessLufs"] = float(m[-1])
        return out
    except Exception as e:  # noqa: BLE001 - no metrics never stops a Short
        log.warning("Measuring %s failed: %s", path.name, e)
        return {}


def dissolve_of(req: dict) -> float:
    """The request's dissolve length; 0 means hard cuts (not "unset")."""
    return 0.5 if req.get("dissolveSec") is None else float(req["dissolveSec"])


def finish(req: dict, tmp: Path) -> dict:
    dream_id = req["dreamId"]
    width, height, fps = int(req.get("width") or 1080), int(req.get("height") or 1920), int(req.get("fps") or 24)
    dissolve = dissolve_of(req)
    smoothed: list[Path] = []
    how: set[str] = set()
    for n, clip in enumerate(req["clips"]):
        src, dst = tmp / f"in{n}.mp4", tmp / f"smooth{n}.mp4"
        src.write_bytes(get_bytes(clip["uri"]))
        how.add(_smooth(src, dst, float(clip.get("fps") or 16), fps, float(req.get("speed") or 1.0)))
        smoothed.append(dst)
        _beat({"smoothed": n + 1, "total": len(req["clips"])})
    durations = [media_duration(p) for p in smoothed]
    track, music_name = None, None
    if req.get("musicUri"):  # the Short's own music: a piece of the owner's track, or a Lyria score
        track = tmp / "score.wav"
        track.write_bytes(get_bytes(req["musicUri"]))
        music_name = req.get("musicName") or "Lyria score"
    else:
        track = pick_track(req.get("musicDir"), dream_id)
        music_name = track.name if track else None
    synced = 0
    if track:  # cut on the music: every cut (or dissolve middle) moved onto a beat
        new = beat_synced(durations, dissolve, track_beats(track, total_duration(durations, dissolve) + 2))
        synced = sum(1 for a, b in zip(durations, new) if a != b)
        durations = new
    length = total_duration(durations, dissolve)
    graph = dissolve_graph(durations, dissolve, width, height, fps, req.get("look"), req.get("handheld"))
    args: list[str] = []
    for p in smoothed:
        args += ["-i", str(p)]
    if track:
        args += ["-stream_loop", "-1", "-i", str(track)]
        fade_out = max(0.0, length - 1.5)
        graph += (f";[{len(smoothed)}:a]atrim=0:{length:.3f},afade=t=in:d=0.3,afade=t=out:st={fade_out:.3f}:d=1.5,"
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
    log.info("Dream Short %s finished: %.1f s (%s, music: %s, %d cuts on the beat)", dream_id, length, smoothing,
             music_name, synced)
    duration = round(media_duration(out), 3)
    metrics = {**measure(out, tmp), "durationSec": duration, "shots": float(len(smoothed)), "cutsOnBeat": float(synced),
               "speed": float(req.get("speed") or 1.0)}
    return {"uri": uri, "durationSec": duration, "width": width, "height": height, "fps": fps,
            "smoothing": smoothing, "music": music_name, "error": None, "metrics": metrics}


@activity.defn(name="dream_finish")
def dream_finish(req: dict) -> dict:
    """req: DreamFinishRequest {dreamId, look, clips:[{uri, fps}], width, height, fps, dissolveSec, musicDir, progressUrl,
             musicUri}
    -> DreamFinishResult {uri, durationSec, width, height, fps, smoothing, music, error}"""
    with workdir("dream") as d:
        return finish(req, Path(d))
