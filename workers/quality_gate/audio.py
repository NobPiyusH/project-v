"""Check 3: voice-vs-music level per line, and whole-mix loudness / true peak (EBU R128 via ffmpeg ebur128, like
verify_v7.py did).

The cut's audio is one mixed track, so the music level cannot be read directly. Instead each narration line (its own
file, placed at its start time) is projected onto the mix: gain g = <mix, voice> / <voice, voice> over the line's
window gives the voice part (g * voice); what is left is music + effects. voice-over-music = 10 log10(|g v|^2 / |rest|^2).
The projection also finds the line's true start in the cut (cross-correlation lag), which check 5 uses.
"""
from __future__ import annotations

import re
import subprocess

import numpy as np

from . import FAIL, WARN, CheckResult, Finding

SR = 16000


def parse_ebur128(text: str) -> dict:
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else text
    lufs = re.search(r"I:\s*(-?[\d.]+|-inf) LUFS", summary)
    peak = re.search(r"Peak:\s*(-?[\d.]+|-inf) dBFS", summary)
    num = lambda m: float(m.group(1)) if m and m.group(1) != "-inf" else float("-inf")  # noqa: E731
    return {"lufs": num(lufs), "truePeak": num(peak)}


def measure_loudness(path, ffmpeg: str = "ffmpeg") -> dict:
    p = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, check=False)
    return parse_ebur128(p.stderr)


def decode_mono(path, ffmpeg: str = "ffmpeg", sr: int = SR) -> np.ndarray:
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def place(stem_len: int, clips: list[tuple[float, np.ndarray]], sr: int = SR) -> np.ndarray:
    """Voice stem: each (start_sec, samples) laid on a silent track."""
    track = np.zeros(stem_len)
    for start, x in clips:
        i = int(round(start * sr))
        n = max(0, min(len(x), stem_len - i))
        if n:
            track[i:i + n] += x[:n]
    return track


def voice_over_music(mix: np.ndarray, voice: np.ndarray, start: float, dur: float, sr: int = SR, max_lag: float = 0.6) -> dict | None:
    """Voice-to-rest gap (dB) for one line window, plus the lag (seconds) at which the voice really sits in the mix."""
    a, b = int(start * sr), int((start + dur) * sr)
    v = voice[a:b]
    if len(v) < sr // 2 or np.sqrt(np.mean(v ** 2)) < 1e-5:
        return None
    pad = int(max_lag * sr)
    lo, hi = max(0, a - pad), min(len(mix), b + pad)
    seg = mix[lo:hi]
    # lag search by cross-correlation (FFT)
    n = 1 << (len(seg) + len(v)).bit_length()
    cc = np.fft.irfft(np.fft.rfft(seg, n) * np.conj(np.fft.rfft(v, n)), n)
    k = int(np.argmax(np.abs(cc[: len(seg) - len(v) + 1])))
    lag_samples = k - (a - lo)
    m = mix[a + lag_samples: a + lag_samples + len(v)]
    if a + lag_samples < 0 or len(m) < len(v):
        return None
    active = _active(v, sr)
    vv, mm = v[active], m[active]
    g = float(np.dot(mm, vv) / np.dot(vv, vv))
    rest = mm - g * vv
    e_voice, e_rest = float(np.mean((g * vv) ** 2)), float(np.mean(rest ** 2))
    if e_voice <= 0 or e_rest <= 0:
        return None
    return {"gap_db": 10 * np.log10(e_voice / e_rest), "lag_sec": lag_samples / sr, "gain": g,
            "corr": float(np.dot(mm, vv) / (np.linalg.norm(mm) * np.linalg.norm(vv) + 1e-12))}


def _active(v: np.ndarray, sr: int, win: float = 0.05) -> np.ndarray:
    """Boolean mask of samples inside 50 ms windows where the voice is audible (not a pause)."""
    w = int(win * sr)
    n = len(v) // w * w
    rms = np.sqrt(np.mean(v[:n].reshape(-1, w) ** 2, axis=1))
    on = np.repeat(rms > 0.15 * np.sqrt(np.mean(v ** 2)), w)
    mask = np.zeros(len(v), bool)
    mask[:n] = on
    return mask if mask.sum() > sr // 4 else np.ones(len(v), bool)


def judge_lines(per_line: list[dict], cfg: dict) -> list[Finding]:
    a = cfg["audio"]
    out = []
    for r in per_line:
        ref = f"line {r['index'] + 1}"
        if r["gap_db"] < a["voice_over_music_min_db"]:
            out.append(Finding("audio", FAIL, "VOICE_TOO_QUIET_VS_MUSIC",
                               f"Voice is only {r['gap_db']:.1f} dB above the music (target about {a['voice_over_music_db']:.0f} dB).", ref))
        elif r["gap_db"] < a["voice_over_music_db"] - 2:
            out.append(Finding("audio", WARN, "VOICE_LOW_VS_MUSIC", f"Voice is {r['gap_db']:.1f} dB above the music (target about {a['voice_over_music_db']:.0f} dB).", ref))
        if r.get("corr", 1) < 0.3:
            out.append(Finding("audio", WARN, "VOICE_NOT_FOUND", "The line's voice could not be matched to the mix (low correlation); level not trusted.", ref))
    return out


def judge_mix(m: dict, cfg: dict) -> list[Finding]:
    a = cfg["audio"]
    out = []
    if abs(m["lufs"] - a["lufs_target"]) > a["lufs_tolerance"]:
        sev = FAIL if abs(m["lufs"] - a["lufs_target"]) > 3 else WARN
        out.append(Finding("audio", sev, "MIX_LOUDNESS", f"Whole mix is {m['lufs']:.1f} LUFS (target {a['lufs_target']:.0f} \u00b1 {a['lufs_tolerance']}).", "mix"))
    if m["truePeak"] > a["true_peak_max_dbtp"]:
        out.append(Finding("audio", FAIL, "MIX_TRUE_PEAK", f"True peak {m['truePeak']:.1f} dBTP is above {a['true_peak_max_dbtp']:.0f} dBTP.", "mix"))
    return out


def run(mix_path, lines: list[dict], line_audio: dict[int, np.ndarray], cfg: dict, ffmpeg: str = "ffmpeg") -> CheckResult:
    """lines: manifest lines with startSec/durationSec; line_audio: decoded mono 16 kHz voice per line index."""
    res = CheckResult("audio")
    mix_stats = measure_loudness(mix_path, ffmpeg)
    res.findings += judge_mix(mix_stats, cfg)
    mix = decode_mono(mix_path, ffmpeg)
    per_line = []
    for ln in lines:
        x = line_audio.get(ln["index"])
        if x is None:
            continue
        stem = place(len(mix), [(ln["startSec"], x)])
        r = voice_over_music(mix, stem, ln["startSec"], len(x) / SR)
        if r:
            per_line.append({"index": ln["index"], **r})
    res.findings += judge_lines(per_line, cfg)
    res.stats = {"mix": mix_stats, "lines": per_line}
    if not per_line:
        res.findings.append(Finding("audio", WARN, "NO_LINE_LEVELS", "No line could be measured against the mix.", ""))
    return res
