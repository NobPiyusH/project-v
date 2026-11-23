"""The quality gate as a pure module call (CTO decision 23 Nov 2026: the Python control plane calls workers/quality_gate directly).

The checks are small pure functions in workers/quality_gate; this adapter runs the gate on a finished cut in this process and reads its
JSON result back. It only touches the outside world the way the gate itself does: Postgres/S3 through its `source` module (unless a
manifest file is given), and OpenAI vision through its client (unless no_vision). Paid vision checks need OPENAI_API_KEY and are
estimated against the caps in config/quality-gate.yaml before any call (check 7).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class GateResult:
    overall: str  # PASS | FAIL
    exit_code: int
    report_md: Path | None
    report_json: Path | None
    data: dict[str, Any]

    @property
    def passed(self) -> bool:
        return self.overall == "PASS"

    def failures(self) -> list[dict[str, Any]]:
        """Every FAIL finding across the checks (what a stage would show the owner)."""
        return [f for c in self.data.get("checks", {}).values() for f in c.get("findings", []) if f.get("severity") == "FAIL"]


def run_quality_gate(cut: str | Path, out_dir: str | Path, video_id: uuid.UUID | str | None = None, *, manifest: str | Path | None = None,
                     srt: str | Path | None = None, no_vision: bool = False, no_db: bool = False, yes: bool = False) -> GateResult:
    """Runs every check on a finished cut and returns the verdict. out_dir receives quality-report.md and quality-result.json."""
    from workers.quality_gate.__main__ import main  # imported here: the workers package is optional for the API process

    out = Path(out_dir)
    argv = [str(video_id) if video_id else str(cut), "--cut", str(cut), "--out", str(out)] if video_id else [str(cut), "--out", str(out)]
    if manifest:
        argv += ["--manifest", str(manifest)]
    if srt:
        argv += ["--srt", str(srt)]
    if no_vision:
        argv.append("--no-vision")
    if no_db or manifest or not video_id:
        argv.append("--no-db")
    if yes:
        argv.append("--yes")
    code = main(argv)
    js = out / "quality-result.json"
    data = json.loads(js.read_text(encoding="utf-8")) if js.exists() else {}
    md = out / "quality-report.md"
    return GateResult(data.get("overall", "FAIL" if code else "PASS"), code, md if md.exists() else None, js if js.exists() else None, data)
