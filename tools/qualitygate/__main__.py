"""CLI: uv run python -m tools.qualitygate <video> [--shots shots.json] [--captions file.srt] [--judge off]"""

from __future__ import annotations

import argparse
import json
import sys

from .gate import render_text, run_gate


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tools.qualitygate", description="Free quality checks on a finished video (Tier 1 + 2).")
    p.add_argument("video")
    p.add_argument("--shots", help="shots.json [{id, start, end}, ...], or 'auto' to detect cuts; default is the whole video as one shot")
    p.add_argument("--captions", help="SRT file to sanity-check against the video duration")
    p.add_argument("--judge", choices=["off", "openai"], default="off", help="Tier 3 VLM judge (default off; openai = paid calls)")
    p.add_argument("--max-usd", type=float, default=0.0, help="REQUIRED with --judge openai: per-run spend cap in USD")
    p.add_argument("--confirm-spend", action="store_true", help="actually make the paid calls; without it only the worst-case estimate is printed")
    p.add_argument("--json-only", action="store_true", help="print only the JSON report")
    args = p.parse_args(argv)
    judge = None
    if args.judge == "openai":
        if args.max_usd <= 0:
            print("--judge openai needs --max-usd (the owner's per-run cap).", file=sys.stderr)
            return 2
        from workers.quality_gate import config as gate_config

        from .config import THRESHOLDS
        from .ffprobe import probe
        from .gate import load_shots
        from .openai_judge import estimate_run, make_openai_judge

        info = probe(args.video)
        n = len(load_shots(args.shots, info.duration, args.video))
        est = estimate_run(n, int(THRESHOLDS["frames_per_shot"]), gate_config.load())
        print(f"Judge estimate (worst case): {json.dumps(est)}; cap ${args.max_usd:.2f}", file=sys.stderr)
        if not args.confirm_spend:
            print("No call made. Add --confirm-spend to run the paid judge.", file=sys.stderr)
            return 3
        judge = make_openai_judge(args.max_usd)
    report = run_gate(args.video, args.shots, args.captions, judge=judge)
    if not args.json_only:
        print(render_text(report))
        print()
    print(json.dumps(report.to_dict(), indent=2))
    return {"pass": 0, "skip": 0, "warn": 0, "fail": 1}[report.status.value]


if __name__ == "__main__":
    raise SystemExit(main())
