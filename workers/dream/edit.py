"""Dream Short editing rules (19 Nov 2026, owner-approved ideas; each has a per-Short switch, default on):

1. Music-led edit: the music's bars, energy and biggest rise drive the cut. Cuts land on bar lines (else beats), the
   climax shot starts on the biggest rise, and each shot plays a little faster or slower with the music's energy.
2. J-cut ambience: every shot has its own quiet real ambience (CC0 Freesound) that fades in 0.7 s before its cut, so
   the next scene's sound carries the cut. No whooshes.
3. Pacing: open on the most striking shot (the hook), no shot under 3.4 s, climax between 38% and 68% of the length.
   (Dream Shorts have no title card, so "title gone before the first cut" always holds; loudness stays -14 LUFS,
   -1.5 dBTP in the finish.)

The functions here are pure (unit-tested); finish.py does the files.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from workers.common import log

MIN_SHOT = 3.4  # seconds
CLIMAX_WINDOW = (0.38, 0.68)  # share of the length
J_CUT = 0.7  # seconds of the next shot's ambience before its cut
SPEED_SWING = 0.12  # energy moves playback speed by up to +-12% around the look's speed
AMBIENCE_GAIN = 0.22  # under the music (before the final loudness pass)


# ------------------------------------------------------------------------------------------------ music map

def music_map(path: Path, seconds: float) -> dict:
    """Beats, bar lines (downbeats), a smoothed energy curve and the biggest rise of the first `seconds` of a track."""
    import librosa
    import numpy as np

    y, sr = librosa.load(str(path), sr=22050, mono=True, duration=seconds)
    env = librosa.onset.onset_strength(y=y, sr=sr)
    _, frames = librosa.beat.beat_track(onset_envelope=env, sr=sr)
    beats = [float(t) for t in librosa.frames_to_time(frames, sr=sr)]
    strength = [float(env[min(f, len(env) - 1)]) for f in frames]
    rms = librosa.feature.rms(y=y)[0]
    times = librosa.times_like(rms, sr=sr)
    k = max(1, int(round(1.0 / (times[1] - times[0])))) if len(times) > 1 else 1  # ~1 s smoothing
    smooth = np.convolve(rms, np.ones(k) / k, mode="same")
    peak = float(smooth.max()) or 1.0
    energy = [(round(float(t), 2), round(float(v) / peak, 3)) for t, v in zip(times[::k], smooth[::k])]
    return {"beats": beats, "bars": bar_lines(beats, strength), "energy": energy, "rise": biggest_rise(energy)}


def bar_lines(beats: list[float], strength: list[float], per_bar: int = 4) -> list[float]:
    """Bar starts: every 4th beat, starting from the phase whose beats hit hardest (downbeats are usually strongest)."""
    if len(beats) < per_bar:
        return list(beats)
    phase = max(range(per_bar), key=lambda p: sum(strength[p::per_bar]) / max(1, len(strength[p::per_bar])))
    return beats[phase::per_bar]


def biggest_rise(energy: list[tuple[float, float]], window: float = 2.0, skip: float = 1.5) -> float | None:
    """Time where the energy climbs most over the next `window` seconds (the music's lift). The first `skip` seconds
    are left out: the piece's own fade-in is not a lift (19 Nov test: the "rise" came out at 0.0 s)."""
    best, at = 0.0, None
    for i, (t, v) in enumerate(energy):
        if t < skip:
            continue
        ahead = [w for (u, w) in energy[i:] if u <= t + window]
        if ahead and max(ahead) - v > best:
            best, at = max(ahead) - v, t
    return at


def energy_at(energy: list[tuple[float, float]], start: float, end: float) -> float:
    vals = [v for t, v in energy if start <= t < end]
    return sum(vals) / len(vals) if vals else 0.5


# ------------------------------------------------------------------------------------------------ pacing

def order_shots(scores: list[float], durations: list[float], target: float | None) -> list[int]:
    """Hook first (the most striking shot); the next most striking (the climax) placed so it starts nearest `target`
    (the music's rise, kept inside 38-68% of the length); the rest keep the writer's order."""
    n = len(scores)
    if n < 2:
        return list(range(n))
    ranked = sorted(range(n), key=lambda i: -scores[i])
    hook, climax = ranked[0], ranked[1]
    rest = [i for i in range(n) if i not in (hook, climax)]
    length = sum(durations)
    lo, hi = CLIMAX_WINDOW[0] * length, CLIMAX_WINDOW[1] * length
    goal = min(max(target if target is not None else 0.5 * length, lo), hi)
    best, best_gap = None, None
    for slot in range(1, n):  # never first: that's the hook
        order = [hook] + rest[:slot - 1] + [climax] + rest[slot - 1:]
        start = sum(durations[i] for i in order[:slot])
        mid = start + durations[climax] / 2
        inside = lo <= mid <= hi
        gap = abs(start - goal) + (0 if inside else 1000)
        if best_gap is None or gap < best_gap:
            best, best_gap = order, gap
    return best


def speeds(order: list[int], durations: list[float], base: float, energy: list[tuple[float, float]]) -> list[float]:
    """Playback speed per shot (in play order): the look's speed, nudged up when the music is loud, down when quiet."""
    out, t = [], 0.0
    for i in order:
        e = energy_at(energy, t, t + durations[i])
        out.append(round(base * (1 - SPEED_SWING + 2 * SPEED_SWING * e), 3))
        t += durations[i]
    return out


# ------------------------------------------------------------------------------------------------ ambience

AMBIENCE = [  # (words in the scene, Freesound query); first match wins
    (("car", "road", "highway", "dashcam", "windscreen", "driving"), "car interior driving road ambience"),
    (("rain", "puddle", "wet", "drizzle"), "light rain ambience"),
    (("sea", "ocean", "beach", "wave", "shore", "cliff", "sandbar", "tide"), "calm ocean waves ambience"),
    (("pool", "flood", "water", "lake", "river"), "gentle water lapping ambience"),
    (("hallway", "corridor", "room", "kitchen", "classroom", "lobby", "indoor", "stairwell"), "empty room tone fluorescent hum"),
    (("city", "street", "neon", "tokyo", "station", "bus stop"), "distant city night ambience"),
    (("night", "stars", "moon"), "night crickets ambience"),
    (("field", "meadow", "grass", "flower", "wheat", "prairie", "cornfield"), "meadow wind birds ambience"),
    (("cloud", "sky", "snow", "frozen"), "soft wind ambience"),
]


def ambience_query(scene: str | None) -> str:
    s = (scene or "").lower()
    for words, query in AMBIENCE:
        if any(re.search(rf"\b{re.escape(w)}", s) for w in words):
            return query
    return "quiet outdoor ambience"


def j_cut_windows(durations: list[float], dissolve: float | list[float] = 0.0) -> list[tuple[float, float]]:
    """Where each shot's ambience plays on the timeline: from J_CUT before its cut to the end of the shot."""
    out, t = [], 0.0
    for k, d in enumerate(durations):
        start = t if k == 0 else max(0.0, t - J_CUT)
        out.append((round(start, 3), round(t + d, 3)))
        nxt = dissolve[k + 1] if isinstance(dissolve, list) and k < len(durations) - 1 else dissolve
        t += d - (nxt if k < len(durations) - 1 else 0)
    return out


def fetch_cc0(queries: list[str], tmp: Path) -> list[Path | None]:
    """One public-domain (CC0) Freesound ambience per shot; a shot that repeats a query gets the next result, so no two
    shots sound the same. None where nothing was found (or no FREESOUND_API_KEY)."""
    key = os.getenv("FREESOUND_API_KEY")
    if not key:
        return [None] * len(queries)
    results: dict[str, list] = {}
    used: dict[str, int] = {}
    out: list[Path | None] = []
    for q in queries:
        try:
            if q not in results:
                p = {"query": q, "filter": 'license:"Creative Commons 0" duration:[6 TO 600]', "sort": "rating_desc",
                     "fields": "id,previews", "page_size": 8, "token": key}
                req = urllib.request.Request("https://freesound.org/apiv2/search/text/?" + urllib.parse.urlencode(p),
                                             headers={"User-Agent": "projectv-studio/1.0"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    results[q] = json.load(r).get("results") or []
            pick = used.get(q, 0)
            used[q] = pick + 1
            if not results[q]:
                out.append(None)
                continue
            snd = results[q][pick % len(results[q])]
            path = tmp / f"amb{snd['id']}.mp3"
            if not path.exists():
                with urllib.request.urlopen(urllib.request.Request(snd["previews"]["preview-hq-mp3"],
                                                                   headers={"User-Agent": "projectv-studio/1.0"}), timeout=60) as r:
                    path.write_bytes(r.read())
            out.append(path)
        except Exception as e:  # noqa: BLE001 - a missing ambience never stops the Short
            log.warning("Ambience %r unavailable (%s)", q, type(e).__name__)
            out.append(None)
    return out


def ambience_graph(first_input: int, windows: list[tuple[float, float]], present: list[bool]) -> tuple[str, list[str]]:
    """Filter parts placing each ambience input in its window (0.7 s fade-in before its cut, short fade-out), and the
    labels to mix. Inputs are numbered from `first_input`, one per shot that has a sound."""
    parts, labels, k = [], [], first_input
    for n, ((start, end), ok) in enumerate(zip(windows, present)):
        if not ok:
            continue
        dur = end - start
        fade_in = J_CUT if n > 0 else 0.3
        parts.append(f"[{k}:a]atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
                     f"afade=t=in:d={fade_in},afade=t=out:st={max(0.0, dur - 0.4):.3f}:d=0.4,volume={AMBIENCE_GAIN},"
                     f"adelay={int(start * 1000)}|{int(start * 1000)}[amb{n}]")
        labels.append(f"[amb{n}]")
        k += 1
    return ";".join(parts), labels


def striking(path: Path, tmp: Path) -> float:
    """How striking a clip is: colour strength plus contrast (luma spread), from FFmpeg on a small copy."""
    import subprocess
    from workers.common import ffmpeg_bin
    f = tmp / f"strike-{path.stem}.txt"
    subprocess.run([ffmpeg_bin(), "-hide_banner", "-y", "-i", str(path), "-an", "-vf",
                    f"scale=180:320,signalstats,metadata=print:file={f.name}", "-f", "null", "-"],
                   cwd=tmp, capture_output=True, check=False)
    text = f.read_text() if f.exists() else ""
    sat = [float(x) for x in re.findall(r"SATAVG=([0-9.]+)", text)]
    hi = [float(x) for x in re.findall(r"YHIGH=([0-9.]+)", text)]
    lo = [float(x) for x in re.findall(r"YLOW=([0-9.]+)", text)]
    if not sat:
        return 0.0
    avg = lambda xs: sum(xs) / len(xs)  # noqa: E731
    return round(avg(sat) + 0.5 * (avg(hi) - avg(lo) if hi and lo else 0), 2)

