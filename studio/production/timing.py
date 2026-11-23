"""Pure timing functions (ports of ShotTiming and NarrationTiming): unit-tested, no I/O."""

from __future__ import annotations

from dataclasses import dataclass

from studio.agents.jfmt import fixed, jround
from studio.production.records import StoredLine, StoredScript

# ================================================================================================ ShotTiming


def _nearest(beats: list[float], t: float) -> float:
    best = beats[0]
    for b in beats:
        if abs(b - t) < abs(best - t):
            best = b
    return best


def _durations(rough: list[float | None]) -> list[float]:
    return [5.0 if v is None or v <= 0 else v for v in rough]


def fit(rough: list[float | None], total: float, beats: list[float] | None, min_shot: float) -> list[tuple[float, float]]:
    """Fits the director's rough shot durations to the exact total, then moves each cut to the nearest music beat when that keeps
    every shot at least min_shot long. Returns per shot (start, duration), summing exactly to total."""
    n = len(rough)
    d = _durations(rough)
    scale = total / sum(d)
    cuts = [0.0] * (n + 1)
    for i in range(n):
        cuts[i + 1] = cuts[i] + d[i] * scale
    cuts[n] = total
    if beats and len(beats) > 1:
        beat_len = (beats[-1] - beats[0]) / (len(beats) - 1)
        for i in range(1, n):
            nearest = _nearest(beats, cuts[i])
            close = abs(nearest - cuts[i]) <= beat_len * 0.6
            keeps_min = nearest - cuts[i - 1] >= min_shot and cuts[i + 1] - nearest >= min_shot
            if close and keeps_min:
                cuts[i] = nearest
    return [(cuts[i], cuts[i + 1] - cuts[i]) for i in range(n)]


def fit_anchored(rough: list[float | None], anchors: dict[int, float], total: float, beats: list[float] | None,
                 min_shot: float) -> list[tuple[float, float]]:
    """Like fit, but some cuts are fixed: anchors maps a shot index to the time that shot must start (so each narration line begins
    on its own shot). Rough durations are distributed proportionally between fixed points; only the free cuts are moved to beats.
    Anchors that would go backwards in time are ignored."""
    n = len(rough)
    d = _durations(rough)
    fixed_pts: dict[int, float] = {0: 0.0}  # shot 0 at 0, valid anchors in increasing time, end at total
    last_time = 0.0
    for idx in sorted(anchors):
        t = anchors[idx]
        if idx <= 0 or idx >= n or t <= last_time + 0.3 or t >= total - 0.3:
            continue
        fixed_pts[idx] = t
        last_time = t
    fixed_pts[n] = total
    cuts = [0.0] * (n + 1)
    is_fixed = [False] * (n + 1)
    prev: int | None = None
    for idx in sorted(fixed_pts):
        cuts[idx] = fixed_pts[idx]
        is_fixed[idx] = True
        if prev is not None:
            span = cuts[idx] - cuts[prev]
            total_d = sum(d[prev:idx])
            acc = cuts[prev]
            for i in range(prev, idx - 1):
                acc += d[i] / total_d * span
                cuts[i + 1] = acc
        prev = idx
    if beats and len(beats) > 1:
        beat_len = (beats[-1] - beats[0]) / (len(beats) - 1)
        for i in range(1, n):
            if is_fixed[i]:
                continue
            near = _nearest(beats, cuts[i])
            if abs(near - cuts[i]) <= beat_len * 0.6 and near - cuts[i - 1] >= min_shot and cuts[i + 1] - near >= min_shot:
                cuts[i] = near
    return [(cuts[i], cuts[i + 1] - cuts[i]) for i in range(n)]


# ================================================================================================ NarrationTiming
# Places narration on the timeline from the REAL voice lengths (owner report 16 Nov 2026: the last sentence of a Short was cut off at
# 60 s and lines drifted up to 1.7 s late, because lines were chained one after another and the video was trimmed to a fixed length).
# Every line gets a fixed start time; short breathing gaps between spoken lines; visual-only beats become music-only moments; the
# video is never shorter than the speech; a Short (max 60 s) may speed the voice up by at most 8 %.

LEAD = 0.4  # picture before the first word
GAP = 0.5  # breath between spoken lines
VISUAL_BEAT = 2.5  # music-only moment for a visual-only script line
TAIL = 1.5  # picture after the last word
SHORT_MAX = 60.0
MAX_TEMPO = 1.08
_SHORT_GAPS = (GAP, 0.35, 0.25)  # breath lengths a Short may fall back to (before speeding the voice up any further)


@dataclass
class Line:
    index: int
    spoken: bool
    duration_sec: float


@dataclass
class Result:
    """starts: script line index -> time the line starts; tempo: voice speed factor (1.0 = unchanged); speech_end: when the last word
    ends; total_sec: video length (>= target, >= speech + tail); warning: None, or a plain-language note for the owner."""

    starts: dict[int, float]
    tempo: float
    speech_end: float
    total_sec: float
    warning: str | None


@dataclass
class Placed:
    line: StoredLine
    start_sec: float
    end_sec: float


def _round3(v: float) -> float:
    return jround(v * 1000) / 1000.0


def place(script: StoredScript | None, fallback: dict[int, float]) -> list[Placed]:
    """Where each voiced line is heard (animatic and master use the same rule): the shot plan's planned start, pushed later only when
    the line before it (re-voiced longer since) is still speaking. Lines without a planned start use fallback."""
    out: list[Placed] = []
    if script is None:
        return out
    planned = script.line_starts or {}
    tempo = 1.0 if script.tempo is None else script.tempo
    prev_end = 0.0
    for line in script.lines:
        if not line.spoken or line.audio_uri is None:
            continue
        want = planned.get(str(line.index))
        start = max(want, prev_end + 0.15) if want is not None else max(fallback.get(line.index, prev_end + 0.4), prev_end + 0.25)
        end = start + (0.0 if line.duration_sec is None else line.duration_sec / tempo)
        out.append(Placed(line, _round3(start), end))
        prev_end = end
    return out


def _layout(lines: list[Line], tempo: float, gap: float) -> tuple[dict[int, float], float]:
    starts: dict[int, float] = {}
    t = LEAD
    speech_end = 0.0
    previous_spoken = False
    for line in lines:
        if line.spoken and line.duration_sec > 0:
            if previous_spoken:
                t += gap
            starts[line.index] = _round3(t)
            t += line.duration_sec / tempo
            speech_end = t
            previous_spoken = True
        else:
            starts[line.index] = _round3(t)
            t += VISUAL_BEAT
            previous_spoken = False
    return starts, _round3(speech_end)


def plan(lines: list[Line], target_sec: float, is_short: bool) -> Result:
    tempo = 1.0
    starts, speech_end = _layout(lines, tempo, GAP)
    warning = None
    if is_short and speech_end + TAIL > SHORT_MAX:
        # Smallest change first: shorter breaths at each speed, then 1 % faster, up to 8 %.
        found = False
        for step in range(jround((MAX_TEMPO - 1) * 100) + 1):
            tempo = 1.0 + step / 100.0
            for gap in _SHORT_GAPS:
                starts, speech_end = _layout(lines, tempo, gap)
                if speech_end + TAIL <= SHORT_MAX:
                    found = True
                    break
            if found:
                break
        if speech_end + TAIL > SHORT_MAX:
            warning = (f"The narration is {fixed(speech_end + TAIL, 0)}s even at {jround((tempo - 1) * 100)}% speed, too long for a Short "
                       "(60 s). Shorten some lines (Edit) or redo the Script.")
        elif tempo > 1.0:
            warning = f"Voice sped up {jround((tempo - 1) * 100)}% so the Short fits in 60 s."
    total = max(target_sec, speech_end + TAIL)
    if is_short and warning is None:
        total = min(total, SHORT_MAX)
    if total > target_sec + 0.5 and warning is None:
        warning = f"Video lengthened from {fixed(target_sec, 0)}s to {fixed(total, 0)}s so no words are cut. Redo Music if the score ends early."
    return Result(starts, tempo, speech_end, _round3(total), warning)
