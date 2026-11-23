"""CLI: uv run python -m workers.quality_gate <video_id | cut.mp4> [--cut PATH] [--out DIR] [--manifest FILE] [--no-vision] [--no-db]

Runs every check on a finished cut, prints the cost estimate BEFORE any paid call, writes quality-report.md and
quality-result.json to the out folder. Exit code 1 when the overall result is FAIL (so a pipeline can stop the video).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

from . import CheckResult, audio, captions, config, cost, diagram, freeze, report, timing, vision
from .ledger import Ledger, estimate

UUID = re.compile(r"^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$")


def _probe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def _frame_jpeg(path: Path, t: float, max_side: int) -> bytes:
    vf = f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'"
    return subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-vf", vf, "-frames:v", "1", "-q:v", "3",
                           "-f", "image2pipe", "-vcodec", "mjpeg", "-"], capture_output=True, check=True).stdout


def _frame_gray(path: Path, t: float, w: int = 960) -> np.ndarray:
    h = int(w * 9 / 16)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-vf", f"scale={w}:{h},format=gray",
                          "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw[: w * h], dtype=np.uint8).reshape(h, w)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m workers.quality_gate")
    ap.add_argument("target", help="video id (uuid) or path to a finished cut")
    ap.add_argument("--cut", help="cut file when target is a video id")
    ap.add_argument("--video-id", help="video id when target is a cut path")
    ap.add_argument("--manifest", help="use this manifest JSON instead of reading Postgres")
    ap.add_argument("--srt", help="caption file (default: the stored captions of the video)")
    ap.add_argument("--out", help="output folder for the report")
    ap.add_argument("--ledger", help="cost ledger file (JSONL)")
    ap.add_argument("--no-vision", action="store_true", help="skip the paid vision checks")
    ap.add_argument("--no-db", action="store_true", help="do not write cost_entry rows to Postgres")
    ap.add_argument("--yes", action="store_true", help="do not pause on a high estimate (the caps still apply)")
    a = ap.parse_args(argv)
    cfg = config.load()

    video_id = a.target if UUID.match(a.target) else a.video_id
    cut_arg = a.cut or (None if UUID.match(a.target) else a.target)
    cut = Path(cut_arg) if cut_arg else None
    if cut is None or not cut.exists():
        print("error: a cut file is required (--cut PATH)", file=sys.stderr)
        return 2
    if a.manifest:
        m = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
        m["cutPath"] = str(cut)
    elif video_id:
        from . import source
        m = source.load_manifest(video_id, str(cut))
    else:
        m = {"videoId": None, "title": cut.name, "cutPath": str(cut), "lines": [], "shots": []}
    out = Path(a.out) if a.out else Path("E:/project-data/outputs/quality-gate") / (video_id or cut.stem)
    cache = out / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    sink = None
    if video_id and not a.no_db:
        from . import source
        sink = source.insert_cost_entry
    ledger = Ledger(Path(a.ledger) if a.ledger else out.parent / "quality-gate-ledger.jsonl", cfg, video_id, sink)
    fetch = None
    if m.get("shots") or m.get("lines"):
        from . import source
        fetch = source.fetch

    shots, lines = m.get("shots", []), m.get("lines", [])
    total = _probe_duration(cut)
    results: list[CheckResult] = []
    specs = {s["n"]: s["diagramSpec"] for s in shots if s.get("diagramSpec")}

    # ---- vision (paid): estimate first (check 7) ------------------------------------------------------------
    items: list[dict] = []
    if not a.no_vision:
        for s in shots:
            base = {"description": s["description"], "narration": s.get("narration", ""), "overlay_text": s.get("overlayText", "")}
            if s.get("lane") == "diagram":
                if s.get("startSec") is not None:
                    items.append({**base, "ref": f"shot {s['n']} (diagram)", "kind": "diagram", "frame_at": s["startSec"] + s["durationSec"] * 0.85})
                continue
            if s.get("keyframeUri"):
                items.append({**base, "ref": f"shot {s['n']} (picture)", "kind": "keyframe", "uri": s["keyframeUri"], "name": f"kf{s['n']}"})
            if s.get("footageUri"):
                items.append({**base, "ref": f"shot {s['n']} (stock clip)", "kind": "stock", "uri": s["footageUri"], "name": f"stock{s['n']}", "mid": True})
    est = estimate(len(items), cfg)
    msg, cost_findings = cost.preflight("vision checks", est["typical_usd"], est["worst_usd"], cfg, ledger.run_usd, ledger.day_usd())
    print(msg, flush=True)
    results.append(cost.result(cost_findings, {"estimate": est}))
    blocked = any(f.severity == "FAIL" for f in cost_findings)

    if a.no_vision or not items:
        results.append(CheckResult("vision", skipped="--no-vision" if a.no_vision else "no pictures in the manifest"))
    elif blocked:
        results.append(CheckResult("vision", skipped="cost estimate is over a cap"))
    else:
        from . import openai_client as oc
        mx = cfg["vision"]["max_side_px"]

        def load(it: dict) -> bytes:
            if "frame_at" in it:
                return _frame_jpeg(cut, it["frame_at"], mx)
            p = fetch(it["uri"], cache / (it["name"] + Path(it["uri"]).suffix))
            if it.get("mid"):
                return _frame_jpeg(p, max(0.0, _probe_duration(p) / 2), mx)
            return oc.shrink_jpeg(p, mx)

        results.append(vision.run(items, oc.make_ask(cfg), ledger, cfg, load))

    # ---- diagram spec + edge-of-frame (free) ----------------------------------------------------------------
    frames = {s["n"]: _frame_gray(cut, s["startSec"] + s["durationSec"] * 0.85) for s in shots
              if s.get("lane") == "diagram" and s.get("startSec") is not None and s["startSec"] < total}
    results.append(diagram.run(shots, specs, frames))

    # ---- audio ---------------------------------------------------------------------------------------------
    nominal = {ln["index"]: ln["startSec"] for ln in lines}
    measured: dict[int, float] = {}
    if lines and fetch:
        line_audio = {}
        for ln in lines:
            if ln.get("audioUri"):
                line_audio[ln["index"]] = audio.decode_mono(fetch(ln["audioUri"], cache / f"line{ln['index']}.wav"))
        ar = audio.run(cut, lines, line_audio, cfg)
        results.append(ar)
        measured = {r["index"]: nominal[r["index"]] + r["lag_sec"] for r in ar.stats["lines"] if r["corr"] >= 0.3}
    else:
        r = CheckResult("audio")
        r.findings += audio.judge_mix(audio.measure_loudness(cut), cfg)
        results.append(r)

    # ---- captions, timing, freeze --------------------------------------------------------------------------
    srt_text = None
    if a.srt:
        srt_text = Path(a.srt).read_text(encoding="utf-8")
    elif m.get("srtUri") and fetch:
        srt_text = fetch(m["srtUri"], cache / "captions.srt").read_text(encoding="utf-8")
    results.append(captions.run(srt_text, shots, specs, cfg) if srt_text else CheckResult("captions", skipped="no caption file"))
    results.append(timing.run(shots, nominal, measured, cfg) if shots and lines else CheckResult("timing", skipped="no shot plan"))
    results.append(freeze.run(cut, total, shots, cfg))

    cost_info = {"run_usd": ledger.run_usd, "run_inr": ledger.run_usd * cfg["caps"]["fx_inr_per_usd"],
                 "calls": next((len(r.stats.get("results", [])) for r in results if r.name == "vision"), 0), "day_usd": ledger.day_usd()}
    md, js = report.write(out, m, results, cost_info)
    print(f"overall: {report.overall(results)}   report: {md}   json: {js}")
    return 1 if report.overall(results) == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
