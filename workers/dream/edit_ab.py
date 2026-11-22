"""Laptop-only before/after for the edit-sync polish: runs the real Dream finish on existing local clips twice
(flags off = today's edit, flags on = polish) and writes <name>-before.mp4 / <name>-after.mp4 + a metrics JSON.
No generation, no GPU, no network (ambience off). Usage:
    uv run python -m workers.dream.edit_ab OUTDIR NAME MUSIC CLIP [CLIP ...]
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from workers.common import workdir
from workers.dream import edit, finish as dream, polish


def run(out_dir: Path, name: str, music: Path, clips: list[Path], width: int = 720, height: int = 1280) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {}
    cap: dict = {}
    real_graph = dream.dissolve_graph

    def spy(durations, dissolve, *a, **k):
        cap["durations"], cap["dissolve"] = list(durations), dissolve
        return real_graph(durations, dissolve, *a, **k)

    dream.get_bytes = lambda uri: Path(uri).read_bytes()
    dream.put_file = lambda key, path, ct: str(path)
    dream.dissolve_graph = spy
    try:
        for label, on in (("before", False), ("after", True)):
            req = {"dreamId": f"{name}-{label}", "look": "lonely", "width": width, "height": height, "fps": 24,
                   "clips": [{"uri": str(c), "fps": 16} for c in clips], "musicUri": str(music), "musicName": music.name,
                   "ambience": False, "keepOrder": True, **{f: on for f in polish.FLAGS}}
            with workdir("edit-ab") as d:
                res = dream.finish(req, Path(d))
                dst = out_dir / f"{name}-{label}.mp4"
                shutil.copy(res["uri"], dst)
            dis = cap["dissolve"] if isinstance(cap["dissolve"], list) else [cap["dissolve"]] * len(cap["durations"])
            beats = edit.music_map(music, sum(cap["durations"]) + 2)
            result[label] = {**res["metrics"], "cutToBeatErrorSec": polish.grid_error(cap["durations"], dis, beats["beats"]),
                             "cutToBarErrorSec": polish.grid_error(cap["durations"], dis, beats["bars"]),
                             "dissolves": [round(x, 2) for x in dis[1:]]}
    finally:
        dream.dissolve_graph = real_graph
    (out_dir / f"{name}-metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    out, nm, mus, *cl = sys.argv[1:]
    print(json.dumps(run(Path(out), nm, Path(mus), [Path(c) for c in cl]), indent=2))
