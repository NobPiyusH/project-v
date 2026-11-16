"""M0-style benchmark: animate a few real keyframes from a video with Wan 2.2 on Modal and measure speed.

Run from the repo root:
  uv run python -m workers.bench.wan_benchmark <videoId> <outDir>
Writes one MP4 per clip, a labelled reel of all clips, and results.json (seconds per clip, settings).
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import modal

from workers.common import get_bytes, run_ffmpeg

API = "http://127.0.0.1:8080/api"


def pick_shots(shots: list[dict]) -> list[dict]:
    """Opening, ignition, supergiant and supernova-like shots: varied motion and subjects."""
    def find(*words):
        for s in shots:
            text = (s.get("description") or "").lower()
            if any(w in text for w in words):
                return s
        return None

    chosen = [shots[0], find("ignit", "fusion"), find("supergiant", "swell"), find("supernova", "explo", "blast", "detonat", "collapse", "neutron")]
    seen, out = set(), []
    for s in chosen:
        if s and s["id"] not in seen and s.get("keyframe_asset_id"):
            seen.add(s["id"])
            out.append(s)
    return out


def main(video_id: str, out_dir: str, shot_numbers: list[int] | None = None, modes: list[str] | None = None) -> None:
    """Default: 4 picked shots in fast mode + the first one in standard mode.
    With shot_numbers/modes: every listed shot (1-based) in every listed mode."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    prod = json.load(urllib.request.urlopen(f"{API}/videos/{video_id}/production"))
    if shot_numbers:
        shots = [s for s in prod["shots"] if s["ord"] + 1 in shot_numbers and s.get("keyframe_asset_id")]
    else:
        shots = pick_shots(prod["shots"])
    jobs = []
    for s in shots:
        asset = json.load(urllib.request.urlopen(f"{API}/assets/{s['keyframe_asset_id']}"))
        prompt = (f"{s['prompt_json'].get('motionPrompt') or ''} {s.get('description') or ''} "
                  "Cinematic, photorealistic space imagery, smooth slow camera motion, volumetric light.").strip()
        base = {"image": get_bytes(asset["uri_hot"]), "prompt": prompt, "width": 1280, "height": 720,
                "numFrames": 81, "seed": 42}
        for mode in modes or ["fast"]:
            jobs.append({**base, "id": f"shot{s['ord'] + 1:02d}-{mode}", "mode": mode})
    if not modes:
        # Same shot in standard (30-step) mode for a quality comparison.
        first = dict(jobs[0])
        first.update({"id": first["id"].replace("-fast", "-standard"), "mode": "standard"})
        jobs.append(first)

    wan = modal.Cls.from_name("projectv-wan", "Wan")()
    t = time.time()
    # One call per clip: parallel across GPUs, each result saved the moment it finishes.
    results = []
    for r in wan.generate_one.map(jobs, order_outputs=False):
        results.append(r)
        if r.get("mp4"):
            (out / f"{r['id']}.mp4").write_bytes(r["mp4"])
        print(f"  finished {r['id']} after {time.time() - t:.0f}s ({r['seconds']:.0f}s on GPU)", flush=True)
    total = time.time() - t
    info = wan.info.remote()

    rows = []
    for r in results:
        row = {k: v for k, v in r.items() if k != "mp4"}
        if r.get("mp4"):
            (out / f"{r['id']}.mp4").write_bytes(r["mp4"])
        rows.append(row)
        print(f"{r['id']:22} {'OK ' if r.get('mp4') else 'ERR'} {r['seconds']:6.1f}s  mode={r.get('mode')} steps={r.get('steps')}  {r.get('error') or ''}")
    summary = {"totalWallSeconds": round(total, 1), "modelLoadSeconds": info.get("load_seconds"), "clips": rows}
    (out / "results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"total wall time {total:.0f}s (includes container start + model load {info.get('load_seconds')})")

    # One labelled reel to watch everything in order.
    clips = [out / f"{r['id']}.mp4" for r in results if r.get("mp4")]
    labelled = []
    for c in clips:
        lab = out / f"lab-{c.name}"
        run_ffmpeg(["-i", str(c), "-vf", f"drawtext=fontfile='C\\:/Windows/Fonts/segoeui.ttf':text='{c.stem}':"
                    "x=24:y=24:fontsize=32:fontcolor=white:box=1:boxcolor=black@0.5:boxborderw=8",
                    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(lab)])
        labelled.append(lab)
    lst = out / "reel.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in labelled), encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out / "wan_benchmark_reel.mp4")])
    print("reel:", out / "wan_benchmark_reel.mp4")


if __name__ == "__main__":
    # usage: <videoId> <outDir> [shots e.g. 1,14] [modes e.g. fast,hybrid]
    shots_arg = [int(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else None
    modes_arg = sys.argv[4].split(",") if len(sys.argv) > 4 else None
    main(sys.argv[1], sys.argv[2], shots_arg, modes_arg)
