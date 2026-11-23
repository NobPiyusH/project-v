"""One-page quality report (markdown) and the JSON result: pass/fail per check."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from . import CheckResult


def overall(results: list[CheckResult]) -> str:
    return "FAIL" if any(r.status == "FAIL" for r in results) else "PASS"


def to_json(video: dict, results: list[CheckResult], cost: dict) -> dict:
    return {"videoId": video.get("videoId"), "cut": video.get("cutPath"), "generatedAt": datetime.now(UTC).isoformat(), "overall": overall(results),
            "cost": cost, "checks": {r.name: {"status": r.status, "skipped": r.skipped, "stats": r.stats,
                                             "findings": [f.to_dict() for f in r.findings]} for r in results}}


def to_markdown(video: dict, results: list[CheckResult], cost: dict) -> str:
    L = [f"# Quality report: {video.get('title') or video.get('videoId')}", "",
         f"**Overall: {overall(results)}**  |  cut: `{video.get('cutPath')}`  |  {datetime.now(UTC):%Y-%m-%d %H:%M} UTC", "",
         f"Paid checks this run: ${cost.get('run_usd', 0):.4f} (Rs {cost.get('run_inr', 0):.2f}), {cost.get('calls', 0)} calls.", "",
         "| Check | Result | FAIL | WARN |", "|---|---|---|---|"]
    for r in results:
        n = lambda s: sum(1 for f in r.findings if f.severity == s)  # noqa: E731
        L.append(f"| {r.name} | {r.status}{' (' + r.skipped + ')' if r.skipped else ''} | {n('FAIL')} | {n('WARN')} |")
    for r in results:
        shown = [f for f in r.findings if f.severity != "INFO"]
        if not shown:
            continue
        L += ["", f"## {r.name}"]
        L += [f"- **{f.severity}** {f.ref}: {f.message}" for f in shown]
    return "\n".join(L) + "\n"


def write(out_dir, video: dict, results: list[CheckResult], cost: dict) -> tuple:
    out_dir.mkdir(parents=True, exist_ok=True)
    md, js = out_dir / "quality-report.md", out_dir / "quality-result.json"
    md.write_text(to_markdown(video, results, cost), encoding="utf-8")
    js.write_text(json.dumps(to_json(video, results, cost), indent=1), encoding="utf-8")
    return md, js
