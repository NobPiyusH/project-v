"""A/B: does faster attention keep the quality? Renders the SAME shots with the SAME seed and settings three ways
(native = exact reference, fa3 = FlashAttention 3, sage = SageAttention) in ONE Modal container, then writes the clips,
a labelled side-by-side reel and the timings. Quality first: the owner judges the reel before any default changes.

usage: uv run python -m workers.bench.attention_ab <videoId> <outDir> [shots e.g. 1,2] [mode fast|hybrid] [frames 33]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import modal
from dotenv import load_dotenv

from workers.common import get_bytes, run_ffmpeg

VARIANTS = ["native", "fa3", "sage"]


def main(video_id: str, out_dir: str, shots: list[int], mode: str, frames: int) -> None:
    load_dotenv()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    prod = json.load(urllib.request.urlopen(f"http://127.0.0.1:8080/api/videos/{video_id}/production"))
    plan = json.load(urllib.request.urlopen(f"http://127.0.0.1:8080/api/videos/{video_id}"))
    vertical = '"9:16"' in (plan.get("plan_json") or "")
    w, h = (720, 1280) if vertical else (1280, 720)
    jobs = []
    for n in shots:
        s = prod["shots"][n - 1]
        asset = json.load(urllib.request.urlopen(f"http://127.0.0.1:8080/api/assets/{s['keyframe_asset_id']}"))
        prompt_json = s["prompt_json"] if isinstance(s["prompt_json"], dict) else json.loads(s["prompt_json"])
        prompt = f"{prompt_json.get('motionPrompt') or ''}. {s.get('description') or ''}".strip(". ")
        base = {"image": get_bytes(asset["uri_hot"]), "prompt": prompt, "width": w, "height": h, "numFrames": frames,
                "seed": 1234 + n, "mode": mode}
        for v in VARIANTS:
            jobs.append({**base, "id": f"ab-shot{n:02d}-{v}", "attention": v})

    wan = modal.Cls.from_name("projectv-wan", "Wan")()
    t = time.time()
    rows = []
    for r in wan.generate_stream.remote_gen(jobs, None):
        path = out / f"{r['id']}.mp4"
        if r.get("mp4"):
            path.write_bytes(r["mp4"])
        rows.append({"id": r["id"], "seconds": round(r.get("seconds") or 0, 1), "attention": r.get("attention"),
                     "fallback": r.get("attentionFallback"), "error": r.get("error")})
        print(f"{r['id']:24} {r.get('seconds') or 0:6.1f}s  used={r.get('attention')}  "
              f"{r.get('attentionFallback') or ''} {r.get('error') or ''}", flush=True)
    total = time.time() - t
    (out / "timings.json").write_text(json.dumps({"totalWallSeconds": round(total, 1), "clips": rows}, indent=2))

    # One row per shot: native | fa3 | sage, labelled, for the owner to compare.
    for n in shots:
        inputs, labels = [], []
        for i, v in enumerate(VARIANTS):
            p = out / f"ab-shot{n:02d}-{v}.mp4"
            if p.exists():
                inputs += ["-i", str(p)]
                secs = next((x["seconds"] for x in rows if x["id"] == f"ab-shot{n:02d}-{v}"), 0)
                used = next((x["attention"] for x in rows if x["id"] == f"ab-shot{n:02d}-{v}"), v)
                labels.append(f"[{len(labels)}:v]drawtext=fontfile='C\:/Windows/Fonts/segoeui.ttf':"
                              f"text='{used} {secs:.0f}s':x=20:y=20:fontsize=36:"
                              f"fontcolor=white:box=1:boxcolor=black@0.6[v{len(labels)}]")
        if len(labels) > 1:
            stack = "".join(f"[v{i}]" for i in range(len(labels)))
            run_ffmpeg([*inputs, "-filter_complex", ";".join(labels) + f";{stack}hstack=inputs={len(labels)}[out]",
                        "-map", "[out]", "-c:v", "libx264", "-crf", "14", str(out / f"compare-shot{n:02d}.mp4")])
    print(f"total wall time {total:.0f}s; reels in {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], [int(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "1,2").split(",")],
         sys.argv[4] if len(sys.argv) > 4 else "fast", int(sys.argv[5]) if len(sys.argv) > 5 else 33)
