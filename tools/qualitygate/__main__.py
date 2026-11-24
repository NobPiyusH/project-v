"""CLI: uv run python -m tools.qualitygate <video> [--shots shots.json] [--captions file.srt] [--judge off]"""

from __future__ import annotations

import argparse
import json
import sys

from .gate import render_text, run_gate


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tools.qualitygate", description="Free quality checks on a finished video (Tier 1 + 2).")
    p.add_argument("video")
    p.add_argument("--shots", help="shots.json: [{id, start, end}, ...]; default is the whole video as one shot")
    p.add_argument("--captions", help="SRT file to sanity-check against the video duration")
    p.add_argument("--judge", choices=["off", "on"], default="off", help="Tier 3 VLM judge. 'on' needs a backend injected from code.")
    p.add_argument("--json-only", action="store_true", help="print only the JSON report")
    args = p.parse_args(argv)
    if args.judge == "on":
        print("Tier 3 judge: no model backend is wired into the CLI. Call tools.qualitygate.gate.run_gate(judge=...) from code.",
              file=sys.stderr)
        return 2
    report = run_gate(args.video, args.shots, args.captions)
    if not args.json_only:
        print(render_text(report))
        print()
    print(json.dumps(report.to_dict(), indent=2))
    return {"pass": 0, "skip": 0, "warn": 0, "fail": 1}[report.status.value]


if __name__ == "__main__":
    raise SystemExit(main())
