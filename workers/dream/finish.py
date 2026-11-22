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
from workers.dream import edit, hook, polish, scenes

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
    # Unfiltered (owner, 20 Nov 2026): the picture as the model made it, no grading, haze, grain or sharpening.
    "unfiltered": "null",
    "lonely": "curves=all='0/0.035 0.2/0.215 1/1',eq=saturation=1.12:contrast=1.04,unsharp=5:5:0.5:5:5:0,noise=alls=2:allf=t",
    "drive": "curves=all='0/0.035 0.2/0.215 1/1',eq=saturation=1.1:contrast=1.04,unsharp=5:5:0.5:5:5:0,noise=alls=2:allf=t",
    # Idea pack "Impossible architecture": clean and crisp like the realistic looks, a touch more contrast for dusk skylines.
    "architecture": "curves=all='0/0.03 0.2/0.21 1/1',eq=saturation=1.1:contrast=1.06,unsharp=5:5:0.5:5:5:0,noise=alls=2:allf=t",
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


def dissolve_graph(durations: list[float], dissolve: float | list[float], width: int, height: int, fps: int,
                   look: str | None, handheld: str | None = None, stretch: list[float] | None = None) -> str:
    """filter_complex joining inputs 0..n-1 with cross-dissolves, then the look; output label [v].

    Each input is scaled/cropped to the final frame first. A dissolve can't be longer than a third of the shorter
    neighbouring clip, so very short clips still show. `dissolve` may be a list (one length per clip, [0] unused) and
    `stretch` a per-clip slow-down factor (>1 plays slower; used by beat snapping to reach a beat)."""
    n = len(durations)
    if n == 0:
        raise ValueError("no clips")
    slow = lambda i: f"setpts=PTS*{stretch[i]:.4f}," if stretch and stretch[i] > 1.0 else ""  # noqa: E731
    parts = [f"[{i}:v]{slow(i)}trim=duration={durations[i]:.3f},setpts=PTS-STARTPTS,"
             f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
             f"crop={width}:{height}{handheld_filter(handheld, width, height, i)},fps={fps},format=yuv420p,setsar=1[c{i}]"
             for i in range(n)]
    if not isinstance(dissolve, list) and dissolve <= 0:  # hard cuts
        parts.append("".join(f"[c{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[x]")
        parts.append(f"[x]{look_filter(look)},format=yuv420p[v]")
        return ";".join(parts)
    last = "c0"
    offset = durations[0]
    for i in range(1, n):
        d = _dissolve_at(durations, dissolve, i)
        offset -= d
        out = f"x{i}"
        parts.append(f"[{last}][c{i}]xfade=transition=fade:duration={d}:offset={round(offset, 3)}[{out}]")
        offset += durations[i]
        last = out
    parts.append(f"[{last}]{look_filter(look)},format=yuv420p[v]")
    return ";".join(parts)


def _dissolve_at(durations: list[float], dissolve: float | list[float], i: int) -> float:
    """Length of the dissolve into clip i (list: per-cut lengths, [i] for clip i)."""
    want = dissolve[i] if isinstance(dissolve, list) else dissolve
    return round(max(0.1, min(want, durations[i - 1] / 3, durations[i] / 3)), 3) if want > 0 else 0.0


def beat_synced(durations: list[float], dissolve: float | list[float], beats: list[float], max_trim: float = 1.0,
                bars: list[float] | None = None, min_len: float = 0.0, prefer: dict[int, float] | None = None) -> list[float]:
    """Shorten clips (never lengthen) so every cut, or the middle of every dissolve, lands on the music.

    Bar lines win over plain beats when given. Each clip may lose at most `max_trim` seconds, never more than a third
    of itself, and never drop under `min_len`; a cut with nothing that close keeps its natural place. `prefer` maps a
    cut (after clip i) to a time it should land nearest (the music's biggest rise, for the climax). The last clip is
    untouched (the music fades out over it)."""
    out = list(durations)
    base = 0.0  # where the current clip starts on the timeline
    for i in range(len(out) - 1):
        d = _dissolve_at(durations, dissolve, i + 1)
        natural = base + out[i] - d / 2  # cut point (or dissolve middle) with the clip at full length
        trim = min(max_trim, out[i] / 3, max(0.0, out[i] - min_len) if min_len else max_trim)
        hits: list[float] = []
        for grid in ([bars] if bars else []) + [beats]:
            hits = [b for b in grid if natural - trim <= b <= natural + 1e-6]
            if hits:
                break
        if hits:
            want = prefer.get(i) if prefer else None
            at = min(hits, key=lambda b: abs(b - want)) if want is not None else max(hits)
            out[i] = round(out[i] - (natural - at), 3)
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


def total_duration(durations: list[float], dissolve: float | list[float]) -> float:
    total = durations[0] if durations else 0.0
    if not isinstance(dissolve, list) and dissolve <= 0:
        return sum(durations)
    for i in range(1, len(durations)):
        total += durations[i] - _dissolve_at(durations, dissolve, i)
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


def _smooth(src: Path, dst: Path, src_fps: float, dst_fps: int, speed: float = 1.0, hq: bool = False) -> str:
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
    run_ffmpeg(["-i", str(src), "-an", "-vf", f"setpts=PTS/{speed},{polish.minterpolate_filter(dst_fps, hq)}",
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
    base = float(req.get("speed") or 1.0)
    music_edit, ambience_on, pacing = (req.get(k) is not False for k in ("musicEdit", "ambience", "pacing"))
    clips = req["clips"]
    srcs: list[Path] = []
    for n, clip in enumerate(clips):
        src = tmp / f"in{n}.mp4"
        src.write_bytes(get_bytes(clip["uri"]))
        srcs.append(src)
    if req.get("sviScenes"):  # SVI scenes: beats -> one continuous shot per scene, then the Finish as always
        srcs, clips = scenes.assemble(srcs, clips, [int(n) for n in req["sviScenes"]], req.get("sviSceneFrames"), tmp)
    track, music_name = None, None
    if req.get("musicUri"):  # the Short's own music: a piece of the owner's track, or a Lyria score
        track = tmp / "score.wav"
        track.write_bytes(get_bytes(req["musicUri"]))
        music_name = req.get("musicName") or "Lyria score"
    else:
        track = pick_track(req.get("musicDir"), dream_id)
        music_name = track.name if track else None
    # Plan the edit before smoothing: shot order (pacing) and per-shot speed (music energy) change the timing.
    expected = [media_duration(s) / base for s in srcs]
    mm = None
    if track and (music_edit or pacing):
        try:
            mm = edit.music_map(track, sum(expected) + 2)
        except Exception as e:  # noqa: BLE001 - no music map: cuts still go on the beat below
            log.warning("Music map failed (%s); plain beat sync", e)
    order = list(range(len(srcs)))
    climax_slot = None
    if pacing and not req.get("keepOrder"):  # a story told in order (picked Scout angle) keeps the writer's order
        scores = [edit.striking(s, tmp) for s in srcs]
        order = edit.order_shots(scores, expected, mm["rise"] if mm else None)
        if len(order) > 2:
            climax_slot = order.index(sorted(range(len(scores)), key=lambda i: -scores[i])[1])
    speeds = edit.speeds(order, expected, base, mm["energy"]) if music_edit and mm else [base] * len(order)
    smoothed: list[Path] = []
    how: set[str] = set()
    for k, i in enumerate(order):
        dst = tmp / f"smooth{k}.mp4"
        how.add(_smooth(srcs[i], dst, float(clips[i].get("fps") or 16), fps, speeds[k], **({"hq": True} if polish.flag(req, "hqInterp") else {})))
        smoothed.append(dst)
        _beat({"smoothed": k + 1, "total": len(order)})
    durations = [media_duration(p) for p in smoothed]
    synced = 0
    stretch: list[float] | None = None
    dis: float | list[float] = dissolve
    if dissolve > 0 and polish.flag(req, "smartTransitions") and len(smoothed) > 1:  # per cut: flow = quick, jar = full
        diffs = [polish.boundary_diff(smoothed[k], smoothed[k + 1]) for k in range(len(smoothed) - 1)]
        dis = [0.0, *polish.transition_lengths(diffs, dissolve)]
    if track:  # cut on the music: bar lines first, then beats; the climax cut nearest the music's rise
        beats = mm["beats"] if mm else track_beats(track, total_duration(durations, dis) + 2)
        prefer = {climax_slot - 1: mm["rise"]} if climax_slot and mm and mm["rise"] is not None else None
        bars = mm["bars"] if (mm and music_edit) else None
        if polish.flag(req, "beatSnap"):
            dl = dis if isinstance(dis, list) else [dissolve] * len(durations)
            new, stretch = polish.snap_cuts(durations, [_dissolve_at(durations, dl, i) if i else 0.0 for i in range(len(durations))],
                                            beats, bars=bars, min_len=edit.MIN_SHOT if pacing else 0.0, prefer=prefer)
        else:
            new = beat_synced(durations, dis, beats, bars=bars, min_len=edit.MIN_SHOT if pacing else 0.0, prefer=prefer)
        synced = sum(1 for a, b in zip(durations, new) if a != b)
        durations = new
    length = total_duration(durations, dis)
    graph = dissolve_graph(durations, dis, width, height, fps, req.get("look"), req.get("handheld"), stretch)
    args: list[str] = []
    for p in smoothed:
        args += ["-i", str(p)]
    soft = polish.flag(req, "softEndFade")
    if track:
        args += ["-stream_loop", "-1", "-i", str(track)]
        ag = (f"[{len(smoothed)}:a]atrim=0:{length:.3f},afade=t=in:d=0.3,"
              + (polish.end_fade(length, True) if soft else f"afade=t=out:st={max(0.0, length - 1.5):.3f}:d=1.5")
              + ",aresample=48000,aformat=channel_layouts=stereo[mus]")
    else:  # silent track: platforms and editors expect audio
        args += ["-f", "lavfi", "-t", f"{length:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        ag = f"[{len(smoothed)}:a]anull[mus]"
    ambiences = 0
    bed = "[mus]"
    if ambience_on:  # J-cut: each shot's own ambience starts 0.7 s before its cut
        sounds = edit.fetch_cc0([edit.ambience_query(clips[i].get("scene")) for i in order], tmp)
        for s_ in sounds:
            if s_:
                args += ["-stream_loop", "-1", "-i", str(s_)]
        parts, labels = edit.ambience_graph(len(smoothed) + 1, edit.j_cut_windows(durations, dis), [bool(s_) for s_ in sounds])
        ambiences = len(labels)
        if labels:
            ag += ";" + parts + f";[mus]{''.join(labels)}amix=inputs={len(labels) + 1}:normalize=0:duration=first[mixed]"
            bed = "[mixed]"
    voiced = False
    if req.get("voiceUri"):  # narration: the bed (music + ambience) ducks under it
        voice = tmp / "voice.wav"
        voice.write_bytes(get_bytes(req["voiceUri"]))
        args += ["-i", str(voice)]
        ag += (f";[{args.count('-i') - 1}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{length:.3f}[voice];"
               + ";".join(polish.duck_filters(bed, "[voice]", "ducked")))
        bed, voiced = "[ducked]", True
    if polish.flag(req, "twoPassLoudness") and (track or ambiences or voiced):
        tail = polish.loudness_filter(polish.measure_loudness(ag, bed, args, tmp)) + ",aresample=48000"
    else:
        tail = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
    graph += ";" + ag + f";{bed}" + (tail + "[a]" if track or ambiences or voiced else "anull[a]")
    out = tmp / "dream-short.mp4"
    video = "[v]"
    hook_ass = hook.hook_ass(req.get("hookText") or "", width, height, req.get("hookColor"))
    if hook_ass:  # the hook title card (setting is off unless the Short asked for it)
        (tmp / "hook.ass").write_text(hook_ass, encoding="utf-8")
        graph += f";[v]{hook.filter_for('hook.ass')}[vh]"
        video = "[vh]"
    end_ass = hook.end_card_ass(req.get("pathA") or "", req.get("pathB") or "", width, height, length, req.get("hookColor"))
    if end_ass:  # "Choose the path" end card over the last ~2 s (off unless the Short asked for it)
        (tmp / "end.ass").write_text(end_ass, encoding="utf-8")
        graph += f";{video}{hook.filter_for('end.ass')}[ve]"
        video = "[ve]"
    run_ffmpeg([*args, "-filter_complex", graph, "-map", video, "-map", "[a]", "-t", f"{length:.3f}",
                "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(fps),
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)], cwd=tmp)
    uri = put_file(f"videos/{dream_id}/dream-short.mp4", out, "video/mp4")
    smoothing = "rife-laptop" if how == {"rife-laptop"} else "ffmpeg" if "ffmpeg" in how else sorted(how)[0]
    log.info("Dream Short %s finished: %.1f s (%s, music: %s, %d cuts on the music, order %s, %d ambiences)", dream_id,
             length, smoothing, music_name, synced, order, ambiences)
    duration = round(media_duration(out), 3)
    starts = [sum(durations[:k]) - sum(_dissolve_at(durations, dis, j) for j in range(1, k + 1)) if dissolve > 0 else sum(durations[:k])
              for k in range(len(durations))]
    metrics = {**measure(out, tmp), "durationSec": duration, "shots": float(len(smoothed)), "cutsOnBeat": float(synced),
               "speed": base, "hookShot": float(order[0] + 1) if order else 0.0, "ambiences": float(ambiences),
               "shortestShotSec": round(min(durations), 2) if durations else 0.0}
    if climax_slot is not None and length > 0:
        metrics["climaxAtPct"] = round(100 * (starts[climax_slot] + durations[climax_slot] / 2) / length, 1)
    if mm and mm.get("rise") is not None:
        metrics["musicRiseSec"] = round(mm["rise"], 2)
    return {"uri": uri, "durationSec": duration, "width": width, "height": height, "fps": fps,
            "smoothing": smoothing, "music": music_name, "error": None, "metrics": metrics}


@activity.defn(name="dream_finish")
def dream_finish(req: dict) -> dict:
    """req: DreamFinishRequest {dreamId, look, clips:[{uri, fps}], width, height, fps, dissolveSec, musicDir, progressUrl,
             musicUri}
    -> DreamFinishResult {uri, durationSec, width, height, fps, smoothing, music, error}"""
    with workdir("dream") as d:
        return finish(req, Path(d))
