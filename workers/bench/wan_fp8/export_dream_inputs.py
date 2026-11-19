"""Export one Dream Short's clip inputs (keyframes, motion prompts, seeds, sizes) for the fp8 benchmark. Read-only.

  uv run python -m workers.bench.wan_fp8.export_dream_inputs --dream 15cc5947-4099-4e90-8f0f-4b7ce2a731f5 --out bench_inputs

Writes <out>/manifest.json, <out>/images/shotNN.png and (unless --no-baseline) the Modal baseline clips to
<out>/baseline/shotNN.mp4. Only GET requests to the local control plane; nothing in the studio changes.
The prompt and seed come from each clip asset's own record (exactly what Modal rendered), not from the shot row.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

API = "http://127.0.0.1:8080/api"
FPS = 16   # Wan's native rate (DreamService.FPS)


def get_json(url: str):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def get_bytes(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:   # follows the 302 to the local S3 store
        return r.read()


def frames_of(duration_sec: float | None, fps: int = FPS) -> int:
    """Frame count of a Wan clip from its duration, snapped to 4k+1 (81 frames = 5.06 s)."""
    if not duration_sec:
        return 81
    n = round(duration_sec * fps)
    return max(5, round((n - 1) / 4) * 4 + 1)


def build_manifest(dream: dict, clip_assets: dict) -> dict:
    clips = []
    for s in sorted(dream["shots"], key=lambda s: s["ord"]):
        a = clip_assets.get(s.get("clip_asset_id"))
        if not s.get("picture_asset_id") or not a:
            continue
        n = s["ord"] + 1
        clips.append({"id": f"shot{n:02d}", "image": f"images/shot{n:02d}.png", "prompt": a["prompt"],
                      "seed": int(a["seed"]), "width": int(a["width"]), "height": int(a["height"]),
                      "frames": frames_of(a.get("duration_sec")), "fps": FPS,
                      "shot_id": s["id"], "picture_asset_id": s["picture_asset_id"],
                      "baseline_asset_id": s["clip_asset_id"], "baseline_model": a.get("model"),
                      "baseline": f"baseline/shot{n:02d}.mp4"})
    return {"dream_id": dream["id"], "title": dream.get("title"), "quality": dream.get("quality"),
            "note": "baseline = Modal H100, Diffusers bf16 + lightx2v 4-step (fast); negative stop-rule fixes, if "
                    "any, are not recorded per asset and are not replayed",
            "clips": clips}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dream", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--api", default=API)
    p.add_argument("--no-baseline", action="store_true")
    args = p.parse_args(argv)

    dream = get_json(f"{args.api}/dream/{args.dream}")
    assets = {s["clip_asset_id"]: get_json(f"{args.api}/assets/{s['clip_asset_id']}")
              for s in dream["shots"] if s.get("clip_asset_id")}
    manifest = build_manifest(dream, assets)
    if not manifest["clips"]:
        print("no shot has both a picture and a clip")
        return 1
    (args.out / "images").mkdir(parents=True, exist_ok=True)
    for c in manifest["clips"]:
        (args.out / c["image"]).write_bytes(get_bytes(f"{args.api}/assets/{c['picture_asset_id']}/content"))
        if not args.no_baseline:
            (args.out / "baseline").mkdir(exist_ok=True)
            (args.out / c["baseline"]).write_bytes(get_bytes(f"{args.api}/assets/{c['baseline_asset_id']}/content"))
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(manifest['clips'])} clips from '{manifest['title']}' -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
