"""Contract harness: record real exchanges from the Java API, replay them against the Python API, report differences.

    python -m studio.contract record  http://localhost:8080 paths.txt recordings/x.json
    python -m studio.contract compare http://localhost:8081 recordings/x.json

paths.txt: one `GET /api/...` per line (GET only: recording never changes data). Comparison ignores key order, compares
numbers by value (12.5 == 12.5000), and masks fields listed in IGNORE_KEYS (ids, timestamps) so only real drift shows.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

IGNORE_KEYS = {"ts", "createdAt", "updatedAt", "created_at", "updated_at"}


@dataclass
class Exchange:
    method: str
    path: str
    status: int
    body: Any


def record(client: httpx.Client, requests: list[tuple[str, str]]) -> list[Exchange]:
    out = []
    for method, path in requests:
        if method != "GET":
            raise ValueError(f"Only GET can be recorded, got {method} {path}")
        r = client.get(path)
        out.append(Exchange(method, path, r.status_code, _json(r)))
    return out


def _json(r: httpx.Response) -> Any:
    try:
        return r.json()
    except ValueError:
        return r.text


def diff(a: Any, b: Any, where: str = "$", ignore: set[str] = IGNORE_KEYS) -> list[str]:
    """Differences between two JSON values; [] means they match."""
    if isinstance(a, bool) or isinstance(b, bool):
        return [] if a == b and type(a) is type(b) else [f"{where}: {a!r} != {b!r}"]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return [] if abs(a - b) < 1e-9 else [f"{where}: {a!r} != {b!r}"]
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted((set(a) | set(b)) - ignore):
            if k not in a:
                out.append(f"{where}.{k}: missing in old")
            elif k not in b:
                out.append(f"{where}.{k}: missing in new")
            else:
                out += diff(a[k], b[k], f"{where}.{k}", ignore)
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{where}: length {len(a)} != {len(b)}"]
        return [d for i, (x, y) in enumerate(zip(a, b, strict=True)) for d in diff(x, y, f"{where}[{i}]", ignore)]
    return [] if a == b else [f"{where}: {a!r} != {b!r}"]


def replay(client: httpx.Client, exchanges: list[Exchange]) -> list[str]:
    """Run every recorded request against the new server; returns the list of differences."""
    problems = []
    for ex in exchanges:
        r = client.request(ex.method, ex.path)
        if r.status_code != ex.status:
            problems.append(f"{ex.method} {ex.path}: status {ex.status} != {r.status_code}")
            continue
        problems += [f"{ex.method} {ex.path} {d}" for d in diff(ex.body, _json(r))]
    return problems


def load(path: Path) -> list[Exchange]:
    return [Exchange(**e) for e in json.loads(path.read_text(encoding="utf-8"))]


def save(path: Path, exchanges: list[Exchange]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(e) for e in exchanges], indent=1), encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) == 5 and argv[1] == "record":
        reqs = [tuple(line.split(None, 1)) for line in Path(argv[3]).read_text().splitlines() if line.strip()]
        with httpx.Client(base_url=argv[2], timeout=30) as c:
            save(Path(argv[4]), record(c, reqs))
        print(f"recorded {len(reqs)} exchanges")
        return 0
    if len(argv) == 4 and argv[1] == "compare":
        with httpx.Client(base_url=argv[2], timeout=30) as c:
            problems = replay(c, load(Path(argv[3])))
        print("\n".join(problems) or "all exchanges match")
        return 1 if problems else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
