"""Stock "search and judge": after the stock lane ranks candidates, a vision model looks at the free preview picture of
the top few (never the full clips) and only a clip that really matches the shot is used. Score at least `min_score` (of 5)
keeps a clip; none keeps nothing and the shot falls back to the AI lane. Every call is a cost row under the gate caps.

Pure core: `judge_candidates` takes injected callables (fetch_thumb, ask, ledger) so tests need no network.
"""
from __future__ import annotations

import subprocess
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

from . import config, vision
from .ledger import CapExceeded, Ledger, call_cost_usd, estimate


def judge_candidates(cands: list[dict], description: str, narration: str, ask: Callable[[bytes, str], tuple[str, dict]],
                     ledger: Ledger, cfg: dict, fetch_thumb: Callable[[dict], bytes | None]) -> tuple[list[dict], list[str]]:
    """Returns (kept candidates best first, notes). Early stop on a 5/5 (nothing can beat it)."""
    sj, v = cfg["stock_judge"], cfg["vision"]
    worst = estimate(1, cfg, 640, 360)["per_call_worst_usd"]
    scored: list[tuple[int, int, dict]] = []
    notes: list[str] = []
    for rank_i, c in enumerate(cands[: sj["top_n"]]):
        label = f"{c['source']} {c['id']}"
        try:
            ledger.reserve(worst)
        except CapExceeded as e:
            notes.append(f"judge stopped (cap): {e}")
            break
        img = fetch_thumb(c)
        if not img:
            notes.append(f"judge: {label} has no preview picture")
            continue
        text, usage = ask(img, vision.build_prompt(description, narration, "", "stock"))
        usd = call_cost_usd(usage.get("input_tokens", 0), usage.get("output_tokens", 0), v["price_usd_per_1m"])
        ledger.record(usd, usage.get("input_tokens", 0) + usage.get("output_tokens", 0), f"stock judge {label} ({v['model']})")
        ans = vision.parse_answer(text)
        if ans is None:
            notes.append(f"judge: {label} answer unreadable")
            continue
        c["judgeScore"] = ans["match"]
        notes.append(f"judge: {label} scored {ans['match']}/5" + (f" ({ans['problems'][0]})" if ans["problems"] and ans["match"] < sj["min_score"] else ""))
        scored.append((ans["match"], rank_i, c))
        if ans["match"] >= 5:
            break
    kept = [c for s, _, c in sorted(scored, key=lambda t: (-t[0], t[1])) if s >= sj["min_score"]]
    return kept, notes


def thumb_bytes(c: dict, max_side: int, ffmpeg: str = "ffmpeg") -> bytes | None:
    """Downloads the preview picture (small, capped) and shrinks it to a JPEG."""
    url = c.get("thumb")
    if not url:
        return None
    req = urllib.request.Request(url, headers={"User-Agent": "ProjectV-StockLane/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read(3_000_000)
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "t"
        src.write_bytes(data)
        vf = f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'"
        return subprocess.run([ffmpeg, "-v", "error", "-i", str(src), "-vf", vf, "-frames:v", "1", "-q:v", "3", "-f", "image2pipe",
                               "-vcodec", "mjpeg", "-"], capture_output=True, check=True).stdout


def pick(ranked: list[dict], description: str, narration: str, video_id: str | None = None) -> tuple[list[dict], list[str]]:
    """The activity entry point: real OpenAI client, real ledger (JSONL always, cost_entry row when a video id is known)."""
    from . import openai_client
    cfg = config.load()
    sink = None
    if video_id:
        from . import source
        sink = source.insert_cost_entry
    ledger = Ledger(Path(cfg["stock_judge"]["ledger"]), cfg, video_id, sink)
    side = cfg["stock_judge"]["thumb_max_side_px"]
    return judge_candidates(ranked, description, narration, openai_client.make_ask(cfg), ledger, cfg, lambda c: thumb_bytes(c, side))
