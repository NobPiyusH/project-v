"""Blind side-by-side for the owner: baseline (Modal bf16) and fp8 clips placed left/right at random, labels hidden.

  uv run python -m workers.bench.wan_fp8.blind_ab --inputs bench_inputs --fp8 bench_out --out bench_ab

Writes <out>/shotNN_ab.mp4 (A left, B right) and <out>/key.json (which side is fp8). Open key.json only after judging.
Needs ffmpeg on PATH. Local files only.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
from pathlib import Path


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--inputs", type=Path, required=True)
    p.add_argument("--fp8", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    manifest = json.loads((args.inputs / "manifest.json").read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    key = {}
    for c in manifest["clips"]:
        base, fp8 = args.inputs / c["baseline"], args.fp8 / f"{c['id']}.mp4"
        if not base.is_file() or not fp8.is_file():
            print(f"{c['id']}: missing {'baseline' if not base.is_file() else 'fp8'} clip, skipped")
            continue
        left, right, key[c["id"]] = (fp8, base, "A") if random.random() < 0.5 else (base, fp8, "B")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(left), "-i", str(right), "-filter_complex",
                        "[0:v]setpts=PTS-STARTPTS[a];[1:v]setpts=PTS-STARTPTS[b];[a][b]hstack=inputs=2",
                        "-c:v", "libx264", "-crf", "12", "-preset", "slow", "-pix_fmt", "yuv420p",
                        str(args.out / f"{c['id']}_ab.mp4")], check=True)
    (args.out / "key.json").write_text(json.dumps({"fp8_side": key}, indent=1))
    print(f"{len(key)} side-by-sides in {args.out}; key.json says which side is fp8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
