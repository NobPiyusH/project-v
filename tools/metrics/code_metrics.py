"""Code metrics for one git ref, for old-vs-new comparisons (24 Nov 2026).

Usage: python tools/metrics/code_metrics.py REF [--ruff-config PYPROJECT] [--out FILE.json]
It exports REF with `git archive` into a temp folder (the working tree is never touched), then counts:
files and non-blank lines per language, test files and test functions, TODO/FIXME notes, ruff findings under ONE fixed rule set
(pass the same --ruff-config for both refs so the comparison is fair) and whether a CI workflow exists.
Run `uv run --group lint python tools/metrics/code_metrics.py ...` so ruff is available.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

LANGS = {
    ".py": "python",
    ".java": "java",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".ps1": "powershell",
    ".sh": "shell",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".md": "markdown",
    ".kts": "gradle",
    ".sql": "sql",
    ".json": "json",
}
SKIP_DIRS = {"reports", "research_notes", "node_modules", ".venv", "build", "logs"}


def export(ref: str, dest: Path) -> None:
    tar = dest.with_suffix(".tar")
    subprocess.run(["git", "archive", "--format=tar", "-o", str(tar), ref], check=True)
    shutil.unpack_archive(str(tar), dest, "tar")
    tar.unlink()


def count(root: Path) -> dict:
    files: dict[str, int] = {}
    lines: dict[str, int] = {}
    tests = {"python_files": 0, "python_tests": 0, "java_files": 0, "java_tests": 0}
    todo = 0
    for p in root.rglob("*"):
        if not p.is_file() or any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        lang = LANGS.get(p.suffix.lower())
        if not lang:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        n = sum(1 for ln in text.splitlines() if ln.strip())
        files[lang] = files.get(lang, 0) + 1
        lines[lang] = lines.get(lang, 0) + n
        if lang == "python" and (p.name.startswith("test_") or "/tests/" in p.as_posix()):
            tests["python_files"] += 1
            tests["python_tests"] += len(re.findall(r"^\s*(?:async\s+)?def test_", text, re.M))
        if lang == "java" and p.name.endswith("Test.java"):
            tests["java_files"] += 1
            tests["java_tests"] += len(re.findall(r"@(?:Test|ParameterizedTest)\b", text))
        if lang in ("python", "java", "typescript"):
            todo += len(re.findall(r"\b(?:TODO|FIXME|XXX)\b", text))
    return {"files": files, "lines": lines, "tests": tests, "todo_notes": todo}


def ruff_findings(root: Path, config: Path | None) -> dict:
    if config:
        shutil.copy(config, root / "pyproject.toml")  # same rule set for every ref
    r = subprocess.run(
        [sys.executable, "-m", "ruff", "check", ".", "--output-format", "json", "--no-cache"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if r.returncode not in (0, 1) or not r.stdout.strip():
        return {"error": (r.stderr or r.stdout or f"ruff exit {r.returncode}")[:300]}
    try:
        items = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"error": (r.stderr or r.stdout)[:300]}
    by_rule: dict[str, int] = {}
    for it in items:
        by_rule[it["code"]] = by_rule.get(it["code"], 0) + 1
    return {"total": len(items), "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])[:8])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("--ruff-config")
    ap.add_argument("--out")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="metrics-"))
    try:
        root = tmp / "src"
        root.mkdir()
        export(a.ref, root)
        res = {"ref": a.ref, **count(root)}
        res["has_ci_workflow"] = (root / ".github" / "workflows" / "ci.yml").exists()
        res["ruff"] = ruff_findings(root, Path(a.ruff_config) if a.ruff_config else None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    text = json.dumps(res, indent=2)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
