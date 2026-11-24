"""Pipeline hook: run tiers 1-2 (judge OFF, free) on a finished edit and store the report JSON next to it.

Used by the film CLI (tools/film4k/edit.py) and the studio's ASSEMBLY stage (workers/activities/gate_check.py). A fail is reported to the
caller, which holds the stage for the owner (rule 2b); warns are returned as text and never block. A crash of the gate itself is NOT a fail:
it comes back as status "error" so a broken checker never holds a video.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .config import THRESHOLDS
from .gate import run_gate
from .types import GateReport, Status

ENV_FLAG = "QUALITY_GATE_ENABLED"  # tiers 1-2 on by default; "0"/"false"/"off" disables. The paid judge (tier 3) is never started here.


def enabled(value: str | None = None) -> bool:
    raw = os.getenv(ENV_FLAG) if value is None else value
    return raw is None or raw.strip().lower() not in ("0", "false", "off", "no")


@dataclass
class HookOutcome:
    status: str  # pass | warn | fail | error
    report_path: Path | None
    summary: str
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status == "fail"


def _line(c) -> str:
    where = f" @ {c.where[0][0]:.1f}-{c.where[0][1]:.1f}s" if c.where else ""
    return f"{c.name}{'/' + c.shot if c.shot else ''}{where}: {c.message}".strip()


def summarise(report: GateReport, report_name: str) -> HookOutcome:
    fails = [_line(c) for c in report.checks if c.status is Status.FAIL]
    warns = [_line(c) for c in report.checks if c.status is Status.WARN]
    status = report.status.value if report.status is not Status.SKIP else "pass"
    text = f"Quality gate (free checks, judge off): {status.upper()}"
    if fails:
        text += f", {len(fails)} failed: " + "; ".join(fails[:3]) + (" ..." if len(fails) > 3 else "")
    if warns:
        text += f", {len(warns)} warning{'' if len(warns) == 1 else 's'}: " + "; ".join(warns[:3]) + (" ..." if len(warns) > 3 else "")
    return HookOutcome(status, None, text + f". Report: {report_name}", fails, warns)


def provenance(report: GateReport, video: Path) -> dict:
    st = video.stat()
    return {"tool": "tools.qualitygate", "tiers": [1, 2], "judge": "off", "createdAt": datetime.now(UTC).isoformat(),
            "videoBytes": st.st_size, "thresholds": THRESHOLDS, **report.to_dict()}


def run_hook(video: str | Path, report_path: str | Path | None = None, captions: str | Path | None = None) -> HookOutcome:
    """Gate `video` (tier 1+2 only) and write the JSON report to report_path (default: <video stem>.quality-gate.json next to it)."""
    video = Path(video)
    out = Path(report_path) if report_path else video.with_name(video.stem + ".quality-gate.json")
    try:
        report = run_gate(str(video), "auto", str(captions) if captions else None, judge=None)
        out.write_text(json.dumps(provenance(report, video), indent=2), encoding="utf-8")
    except Exception as e:  # the checker must never hold a video by crashing (ffmpeg missing, odd file)
        return HookOutcome("error", None, f"Quality gate could not run ({type(e).__name__}: {str(e)[:200]}); continuing without it.")
    o = summarise(report, out.name)
    o.report_path = out
    return o
