"""Check 5: picture-vs-narration timing. The first shot of each narration line must start within 0.2 s of the voice.

`line_starts` is the voice start in the cut: measured (audio.voice_over_music lag) when available, else the plan's.
The opening shot at t = 0 is exempt for the first line (a video cannot start before its first frame).
"""
from __future__ import annotations

from . import FAIL, WARN, CheckResult, Finding


def check(shots: list[dict], line_starts: dict[int, float], max_err: float = 0.2, fail_err: float = 0.6) -> list[Finding]:
    out = []
    first: dict[int, dict] = {}
    for s in sorted((s for s in shots if s.get("startSec") is not None and s.get("lineIndex") is not None), key=lambda s: s["startSec"]):
        first.setdefault(s["lineIndex"], s)
    for idx, start in sorted(line_starts.items()):
        s = first.get(idx)
        ref = f"line {idx + 1}"
        if s is None:
            out.append(Finding("timing", WARN, "LINE_HAS_NO_SHOT", "No shot is assigned to this line.", ref))
            continue
        if idx == min(line_starts) and s["startSec"] <= 0.05 and start >= s["startSec"]:
            continue
        err = s["startSec"] - start
        if abs(err) > fail_err:
            out.append(Finding("timing", FAIL, "PICTURE_OFF_VOICE", f"First shot (shot {s['n']}) starts {err:+.2f}s from the voice (limit {max_err}s).", ref))
        elif abs(err) > max_err:
            out.append(Finding("timing", WARN, "PICTURE_SLIGHTLY_OFF_VOICE", f"First shot (shot {s['n']}) starts {err:+.2f}s from the voice (limit {max_err}s).", ref))
    return out


def run(shots: list[dict], nominal: dict[int, float], measured: dict[int, float] | None, cfg: dict) -> CheckResult:
    res = CheckResult("timing")
    starts = dict(nominal)
    if measured:
        starts.update(measured)
    res.findings = check(shots, starts, cfg["timing"]["max_start_error_sec"])
    res.stats = {"lines": len(starts), "used_measured_voice_start": bool(measured)}
    return res
