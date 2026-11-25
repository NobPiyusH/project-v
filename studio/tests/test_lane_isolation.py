"""The Dream Shorts lane and the video lane must not depend on each other (owner, 25 Nov 2026: "make it unaffected to each other").
Shared plumbing (GPU broker, ledger, providers, database, the AI Producer, the stage mode enum) is allowed to both. The only place the two meet
is the wiring (api/wiring.py, api/video_wiring.py, worker, runtime), which this test does not police."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DREAM = re.compile(r"(^|/)(dream|autopilot)(/|_)|dream_routes|dream_activities|autopilot_workflow")
VIDEO = re.compile(r"studio/(video|production|planner)/|video_wiring|video_routes|video_production|video_stage|shot_stage")
WIRING = {"studio/api/wiring.py", "studio/api/video_wiring.py", "studio/worker.py", "studio/workflow/runtime.py", "studio/api/app.py",
          "studio/contract/writes.py"}
SHARED_FROM_VIDEO_TREE = {"studio/production/producer.py", "studio/production/stages_m2a.py", "studio/production/redo_stop_rule.py"}  # the AI Producer and shared helpers both lanes use


def _files():
    out = subprocess.run(["git", "ls-files", "studio"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    return [f for f in out if f.endswith(".py") and "/tests/" not in f]


def _imports(path):
    src = (ROOT / path).read_text(encoding="utf-8", errors="ignore")
    return re.findall(r"^\s*(?:from|import)\s+(studio[\w.]*)", src, re.M)


def _file_of(mod, files):
    parts = mod.split(".")
    while parts:
        cand = "/".join(parts) + ".py"
        if cand in files:
            return cand
        pkg = "/".join(parts) + "/__init__.py"
        if pkg in files:
            return pkg
        parts.pop()
    return None


def test_the_dream_lane_never_imports_the_video_lane_and_the_reverse():
    files = set(_files())
    bad = []
    for f in files:
        if f in WIRING:
            continue
        is_dream, is_video = bool(DREAM.search(f)), bool(VIDEO.search(f)) and f not in SHARED_FROM_VIDEO_TREE
        for mod in _imports(f):
            t = _file_of(mod, files)
            if t is None or t in WIRING or t in SHARED_FROM_VIDEO_TREE or t.endswith("__init__.py"):
                continue
            t_dream, t_video = bool(DREAM.search(t)), bool(VIDEO.search(t))
            if (is_dream and t_video) or (is_video and t_dream):
                bad.append(f"{f} -> {t}")
    assert not bad, "lanes must stay independent; move the shared piece to a neutral module instead:\n" + "\n".join(bad)
