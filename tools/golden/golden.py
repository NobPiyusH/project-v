"""Golden fixtures: pin current behaviour as small JSON files so an accidental change fails a test and a deliberate one is a single
command:  GOLDEN_UPDATE=1 uv run --group dev --group studio python -m pytest tools/golden -q     (or add --update-golden)
then read `git diff tools/golden/fixtures` before committing. Numbers compare with a small tolerance (ffmpeg builds differ in the
last digits); everything else compares exactly."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REL_TOL = 0.05
ABS_TOL = 0.06


def updating(config=None) -> bool:
    return os.environ.get("GOLDEN_UPDATE") == "1" or bool(config is not None and config.getoption("--update-golden", default=False))


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _diff(path: str, want, got, rel: float, abs_: float, out: list[str]) -> None:
    if _is_num(want) and _is_num(got):
        if not math.isclose(want, got, rel_tol=rel, abs_tol=abs_):
            out.append(f"{path}: {got} != golden {want}")
    elif isinstance(want, dict) and isinstance(got, dict):
        for k in sorted(set(want) | set(got)):
            if k not in want or k not in got:
                out.append(f"{path}/{k}: {'missing in golden' if k not in want else 'missing in result'}")
            else:
                _diff(f"{path}/{k}", want[k], got[k], rel, abs_, out)
    elif isinstance(want, list) and isinstance(got, list):
        if len(want) != len(got):
            out.append(f"{path}: length {len(got)} != golden {len(want)}")
        for i, (a, b) in enumerate(zip(want, got)):
            _diff(f"{path}[{i}]", a, b, rel, abs_, out)
    elif want != got:
        out.append(f"{path}: {got!r} != golden {want!r}")


def check(name: str, actual, config=None, rel: float = REL_TOL, abs_: float = ABS_TOL) -> None:
    """Compare `actual` (JSON-able) with fixtures/<name>.json; rewrite it when updating."""
    path = FIXTURES / f"{name}.json"
    actual = json.loads(json.dumps(actual))  # tuples -> lists
    if updating(config):
        FIXTURES.mkdir(exist_ok=True)
        path.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return
    assert path.exists(), f"no golden fixture {path.name}: run once with GOLDEN_UPDATE=1 and review the file"
    want = json.loads(path.read_text(encoding="utf-8"))
    diffs: list[str] = []
    _diff("", want, actual, rel, abs_, diffs)
    assert not diffs, f"{name} differs from its golden fixture (deliberate? GOLDEN_UPDATE=1):\n  " + "\n  ".join(diffs[:20])
