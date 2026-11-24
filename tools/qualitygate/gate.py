"""Run the tiers against one video and collect a GateReport."""

from __future__ import annotations

import json
from pathlib import Path

from . import tier1, tier2
from .config import THRESHOLDS
from .ffprobe import probe, run_filter
from .tier3 import Judge, judge_shot
from .types import CheckResult, GateReport, Shot, Status


def load_shots(path: str | None, duration: float) -> list[Shot]:
    """shots.json: [{"id": "s1", "start": 0, "end": 4.5}, ...]. Without a file the whole video is one shot."""
    if not path:
        return [Shot("all", 0.0, duration)]
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Shot(str(s.get("id", i + 1)), float(s["start"]), float(s["end"])) for i, s in enumerate(items)]


def run_gate(video: str, shots_path: str | None = None, captions_path: str | None = None, judge: Judge | None = None,
             cfg: dict[str, float] = THRESHOLDS, intent: str = "") -> GateReport:  # fmt: skip
    info = probe(video)
    report = GateReport(video, info.duration)
    add = report.checks.append

    # Tier 1
    if info.has_audio:
        lufs, peak = tier1.parse_ebur128(run_filter(video, tier1.loudness_args()))
        add(tier1.check_loudness(lufs, peak, cfg))
    else:
        add(CheckResult("loudness", Status.SKIP, message="no audio stream (silent clip)"))
    add(tier1.check_black(tier1.parse_blackdetect(run_filter(video, tier1.black_args(cfg))), info.duration, cfg))
    add(tier1.check_freeze(tier1.parse_freezedetect(run_filter(video, tier1.freeze_args(cfg)), info.duration), cfg))
    if captions_path:
        cues = tier1.parse_srt(Path(captions_path).read_text(encoding="utf-8-sig"))
        report.checks.extend(tier1.check_captions(cues, info.duration, cfg))

    # Tier 2
    shots = load_shots(shots_path, info.duration)
    motion = tier2.parse_signalstats(run_filter(video, tier2.motion_args(cfg)))
    sharp = tier2.parse_signalstats(run_filter(video, tier2.sharpness_args(cfg)))
    for shot in shots:
        report.checks.extend(tier2.score_shot(motion, sharp, shot, cfg))

    # Tier 3 (off unless a judge is injected)
    if judge is not None:
        report.judge = [judge_shot(video, s, judge, intent=intent, cfg=cfg).to_dict() for s in shots]
    return report


def render_text(report: GateReport) -> str:
    lines = [f"Quality gate: {report.video}", f"duration {report.duration:.2f}s, overall {report.status.value.upper()}"]
    for c in report.checks:
        val = "" if c.value is None else f" {c.value:g}{(' ' + c.unit) if c.unit else ''}"
        where = ""
        if c.where and not c.shot:
            where = " @ " + ", ".join(f"{a:.1f}-{b:.1f}s" for a, b in c.where[:3])
        lines.append(f"  [{c.status.value:4}] {c.name}{'/' + c.shot if c.shot else ''}:{val}{where}  {c.message}")
    if not report.judge:
        lines.append("  Tier 3 judge: off")
    for j in report.judge:
        d = j["decision"]
        lines.append(f"  [judge] {j['shot']}: {d['action']} ({d['reason']})")
    return "\n".join(lines)
