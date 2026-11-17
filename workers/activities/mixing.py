"""Shared audio mix for the animatic and the master: narration lines placed at their start times (with the voice
tempo the shot plan chose), music ducked under the voice, then loudness-normalised.

The animatic uses a quick single-pass normalise; the master uses FFmpeg's two-pass loudnorm (measure, then apply
linearly), which hits the target precisely without pumping: -14 LUFS integrated, -1 dBTP, the level YouTube and
Instagram play at.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from workers.common import download, ffmpeg_bin, run_ffmpeg


SFX_VOLUME = 0.35  # sound effects sit under the music; the whole bed ducks under the voice


def _graph(narration: list[dict], music: Path | None, total: float, tmp: Path,
           sfx: list[dict] | None = None) -> tuple[list[str], list[str], str | None]:
    inputs, filters, labels = [], [], []
    for i, line in enumerate(narration):
        p = download(line["uri"], tmp / f"n{i}.wav")
        inputs += ["-i", str(p)]
        ms = int(float(line["startSec"]) * 1000)
        tempo = float(line.get("tempo") or 1.0)
        speed = f",atempo={tempo:.3f}" if abs(tempo - 1.0) > 1e-3 else ""
        filters.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo{speed},adelay={ms}|{ms}[n{i}]")
        labels.append(f"[n{i}]")
    n_voice = len(labels)
    if n_voice:
        filters.append(f"{''.join(labels)}amix=inputs={n_voice}:normalize=0,apad,atrim=0:{total:.3f}[voice]")
    bed = []  # music and sound effects together form the background bed
    if music is not None:
        inputs += ["-i", str(music)]
        filters.append(f"[{n_voice}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{total:.3f},volume=0.9[mus]")
        bed.append("[mus]")
    for k, fx in enumerate(sfx or []):
        idx = len(inputs) // 2
        inputs += ["-stream_loop", "-1", "-i", str(fx["path"])]  # short sounds loop to fill their segment
        dur, ms = max(0.5, float(fx["durationSec"])), int(float(fx["startSec"]) * 1000)
        fade = min(0.6, dur / 4)
        filters.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{dur:.3f},"
                       f"afade=t=in:d={fade:.2f},afade=t=out:st={dur - fade:.3f}:d={fade:.2f},"
                       f"volume={SFX_VOLUME},adelay={ms}|{ms},apad,atrim=0:{total:.3f}[fx{k}]")
        bed.append(f"[fx{k}]")
    if bed:
        if len(bed) > 1:
            filters.append(f"{''.join(bed)}amix=inputs={len(bed)}:normalize=0[bed]")
        else:
            filters.append(f"{bed[0]}anull[bed]")
        if n_voice:
            filters.append("[voice]asplit=2[v1][v2]")
            filters.append("[bed][v2]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[ducked]")
            filters.append("[ducked][v1]amix=inputs=2:normalize=0[mix]")
            return inputs, filters, "[mix]"
        return inputs, filters, "[bed]"
    return inputs, filters, "[voice]" if n_voice else None


def mix_audio(narration: list[dict], music_uri: str | None, total: float, tmp: Path, out: Path, *,
              lufs: float = -16.0, true_peak: float = -1.5, two_pass: bool = False,
              sfx: list[dict] | None = None) -> Path | None:
    """Writes the mixed, normalised WAV to {out}; returns None when there is neither voice nor music.
    sfx: [{path, startSec, durationSec}] sound effects, mixed under the music and ducked under the voice."""
    music = download(music_uri, tmp / "music.wav") if music_uri else None
    inputs, filters, label = _graph(narration or [], music, total, tmp, sfx)
    if label is None:
        return None
    if not two_pass:
        graph = ";".join([*filters, f"{label}loudnorm=I={lufs}:TP={true_peak}:LRA=11[out]"])
        run_ffmpeg([*inputs, "-filter_complex", graph, "-map", "[out]", "-ar", "48000", str(out)])
        return out
    # Pass 1: render the raw mix and measure it.
    raw = tmp / "mix_raw.wav"
    run_ffmpeg([*inputs, "-filter_complex", ";".join(filters), "-map", label, "-ar", "48000", str(raw)])
    import subprocess

    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(raw), "-af",
                           f"loudnorm=I={lufs}:TP={true_peak}:LRA=11:print_format=json", "-f", "null", "-"],
                          capture_output=True, text=True, check=False)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.S)
    if not m:  # measurement failed: fall back to single pass rather than shipping an unnormalised mix
        run_ffmpeg(["-i", str(raw), "-af", f"loudnorm=I={lufs}:TP={true_peak}:LRA=11", "-ar", "48000", str(out)])
        return out
    s = json.loads(m.group(0))
    # Pass 2: apply linearly with the measured values (no dynamic "pumping").
    run_ffmpeg(["-i", str(raw), "-af",
                f"loudnorm=I={lufs}:TP={true_peak}:LRA=11:measured_I={s['input_i']}:measured_TP={s['input_tp']}:"
                f"measured_LRA={s['input_lra']}:measured_thresh={s['input_thresh']}:offset={s['target_offset']}:"
                f"linear=true", "-ar", "48000", str(out)])
    return out
