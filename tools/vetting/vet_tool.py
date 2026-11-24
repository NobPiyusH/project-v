"""Tool vetting lab: read a third-party tool's files and report what it could do, BEFORE it joins the pipeline.

It never runs, imports or installs the target: it only reads text and hashes files (rule: downloaded files are untrusted data).
A LOW verdict means "nothing alarming found by reading", never "safe". Prebuilt binaries cannot be vetted by reading and are flagged.

Usage: python tools/vetting/vet_tool.py PATH [--out report.md] [--json report.json]
PATH is a folder or a single file that you already downloaded into its own new, empty directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

TEXT_EXT = {
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".sh",
    ".ps1",
    ".bat",
    ".cmd",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".txt",
    ".md",
    ".cfg",
    ".ini",
    ".html",
    ".pth",
}
BINARY_EXT = {".exe", ".dll", ".so", ".dylib", ".pyd", ".bin", ".node", ".msi", ".jar", ".whl", ".apk"}
MODEL_EXT = {".pt", ".pth", ".ckpt", ".pkl", ".pickle"}  # pickle-based weights can run code when loaded
MAX_TEXT = 1_000_000
TRUSTED_DOMAINS = {
    "github.com",
    "githubusercontent.com",
    "huggingface.co",
    "hf.co",
    "pypi.org",
    "pythonhosted.org",
    "python.org",
    "pytorch.org",
    "arxiv.org",
    "creativecommons.org",
    "apache.org",
    "opensource.org",
    "w3.org",
    "gnu.org",
    "nvidia.com",
    "ffmpeg.org",
    "readthedocs.io",
    "wikipedia.org",
    "mozilla.org",
    "npmjs.com",
}

# (id, severity, regex, meaning)
PATTERNS = [
    (
        "dynamic-exec",
        "MEDIUM",
        r"\b(eval|exec)\s*\(|\bmarshal\.loads|\bpickle\.loads?\(",
        "runs code built at run time or unpickles data",
    ),
    (
        "unsafe-torch-load",
        "MEDIUM",
        r"torch\.load\((?![^)]*weights_only\s*=\s*True)",
        "torch.load without weights_only=True can run code",
    ),
    (
        "obfuscation",
        "HIGH",
        r"base64\.b64decode\([^)]*\)[^\n]*\b(exec|eval)\b|[A-Za-z0-9+/=]{300,}",
        "long encoded blobs or decode-then-run",
    ),
    (
        "download-execute",
        "HIGH",
        r"(curl|wget|Invoke-WebRequest|urlretrieve)[^\n]*\|\s*(sh|bash|python)|\biex\s*\(",
        "downloads and runs code in one step",
    ),
    (
        "persistence",
        "MEDIUM",
        r"\bcrontab\b|schtasks|Register-ScheduledTask|HKEY_|\bwinreg\b|systemctl\s+enable",
        "installs itself to start later",
    ),
    (
        "secrets-access",
        "MEDIUM",
        r"id_rsa|\.ssh[/\\]|HF_TOKEN|RUNPOD_API_KEY|AWS_SECRET|API_KEY|credentials\.json|\.env\b|keychain",
        "touches keys or credential files",
    ),
    ("subprocess", "INFO", r"\bsubprocess\.|os\.system|os\.popen|child_process", "starts other programs"),
    (
        "network",
        "INFO",
        r"\brequests\.(get|post)|urllib\.request|http\.client|\bsocket\.|aiohttp|httpx\.|\bfetch\(|XMLHttpRequest|WebSocket",
        "talks to the network",
    ),
]
URL_RE = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
LICENCES = [
    ("MIT", r"MIT License|Permission is hereby granted, free of charge"),
    ("Apache-2.0", r"Apache License"),
    ("GPL", r"GNU (Affero )?General Public License"),
    ("BSD", r"BSD \d?-?Clause|Redistribution and use in source"),
    ("non-commercial", r"non-?commercial|NonCommercial|research use only|not for commercial"),
]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(p: Path) -> str:
    try:
        if p.stat().st_size > MAX_TEXT:
            return ""
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def magic(p: Path) -> bytes:
    try:
        with p.open("rb") as f:
            return f.read(4)
    except OSError:
        return b""


def domain_ok(d: str) -> bool:
    d = d.lower()
    return any(d == t or d.endswith("." + t) for t in TRUSTED_DOMAINS)


def install_hooks(p: Path, text: str) -> list[str]:
    out = []
    if p.name == "package.json":
        try:
            scripts = json.loads(text).get("scripts", {})
        except json.JSONDecodeError:
            scripts = {}
        out += [
            f"package.json runs `{k}` at install: {v[:80]}"
            for k, v in scripts.items()
            if k in ("preinstall", "install", "postinstall")
        ]
    if p.name == "setup.py" and "cmdclass" in text:
        out.append("setup.py overrides install commands (cmdclass)")
    if p.suffix == ".pth" and re.search(r"^import\s", text, re.M):
        out.append(".pth file with an import line runs at every Python start")
    if p.name.startswith("requirements") and re.search(r"git\+|https?://", text):
        out.append("requirements pull code from a URL or git, not a pinned release")
    return out


def scan(root: Path) -> dict:
    root = Path(root)
    files = [root] if root.is_file() else sorted(x for x in root.rglob("*") if x.is_file() and ".git" not in x.parts)
    base = root.parent if root.is_file() else root
    rep: dict = {
        "target": str(root),
        "files": len(files),
        "licence": [],
        "binaries": [],
        "models": [],
        "hooks": [],
        "findings": [],
        "domains": {},
        "ext": Counter(x.suffix.lower() for x in files).most_common(8),
    }
    domains: Counter = Counter()
    for p in files:
        rel = p.relative_to(base).as_posix()
        ext = p.suffix.lower()
        head = magic(p)
        if ext in BINARY_EXT or head[:2] == b"MZ" or head[:4] == b"\x7fELF":
            rep["binaries"].append({"file": rel, "sha256": sha256(p), "bytes": p.stat().st_size})
            continue
        if ext in MODEL_EXT:
            rep["models"].append(rel)
        if re.match(r"(LICEN[CS]E|COPYING|NOTICE)", p.name, re.I) or p.name.lower() == "readme.md":
            t = read_text(p)
            for name, rx in LICENCES:
                if re.search(rx, t, re.I) and name not in rep["licence"]:
                    rep["licence"].append(name)
        if ext not in TEXT_EXT and p.name not in ("Dockerfile", "Makefile"):
            continue
        text = read_text(p)
        if not text:
            continue
        rep["hooks"] += [f"{rel}: {h}" for h in install_hooks(p, text)]
        for d in URL_RE.findall(text):
            domains[d.lower()] += 1
        for pid, sev, rx, why in PATTERNS:
            m = re.search(rx, text)
            if m:
                line = text.count("\n", 0, m.start()) + 1
                if ext == ".md" and sev != "INFO":  # install instructions in docs are noted, not treated as the tool's behaviour
                    sev, why = "INFO", why + " (in documentation)"
                rep["findings"].append({"id": pid, "severity": sev, "file": rel, "line": line, "why": why})
    rep["domains"] = {d: n for d, n in domains.most_common(40) if not domain_ok(d)}
    rep["verdict"], rep["reasons"] = verdict(rep)
    return rep


def verdict(rep: dict) -> tuple[str, list[str]]:
    reasons: list[str] = []
    by_file: dict[str, set[str]] = {}
    for f in rep["findings"]:
        by_file.setdefault(f["file"], set()).add(f["id"])
    high = [f for f in rep["findings"] if f["severity"] == "HIGH"]
    combo = [fn for fn, ids in by_file.items() if "secrets-access" in ids and "network" in ids]
    mediums = [f for f in rep["findings"] if f["severity"] == "MEDIUM"]
    if high:
        reasons.append(f"{len(high)} high-risk pattern(s): " + ", ".join(sorted({f["id"] for f in high})))
    if combo:
        reasons.append("reads keys AND uses the network in the same file: " + ", ".join(combo[:3]))
    if rep["hooks"]:
        reasons.append(f"{len(rep['hooks'])} install-time hook(s)")
    if rep["binaries"]:
        reasons.append(f"{len(rep['binaries'])} prebuilt binary file(s): cannot be vetted by reading")
    if rep["models"]:
        reasons.append(f"{len(rep['models'])} pickle-style weight file(s): load only with weights_only=True or safetensors")
    if rep["domains"]:
        reasons.append(f"{len(rep['domains'])} unfamiliar web domain(s) in the code")
    if "non-commercial" in rep["licence"]:
        reasons.append("licence mentions non-commercial/research use: not for a monetised channel")
    if not rep["licence"]:
        reasons.append("no licence found: not cleared for use")
    level = "LOW"
    if high or combo or rep["hooks"] or "non-commercial" in rep["licence"]:
        level = "HIGH"
    elif len(mediums) >= 3 or rep["binaries"] or rep["domains"] or not rep["licence"]:
        level = "MEDIUM"
    return level, reasons


def render(rep: dict) -> str:
    lines = [
        f"# Vetting report: {rep['target']}",
        "",
        f"**Verdict: {rep['verdict']}** (read-only scan of {rep['files']} files; LOW means nothing alarming found by reading, not proof of safety)",
        "",
        "## Why",
    ]
    lines += [f"- {r}" for r in rep["reasons"]] or ["- nothing flagged"]
    lines += ["", f"**Licence found:** {', '.join(rep['licence']) or 'none'}", "", "## Findings (file:line)"]
    sev = {"HIGH": 0, "MEDIUM": 1, "INFO": 2}
    for f in sorted(rep["findings"], key=lambda x: (sev[x["severity"]], x["file"]))[:60]:
        lines.append(f"- [{f['severity']}] {f['id']}: {f['file']}:{f['line']}: {f['why']}")
    if rep["hooks"]:
        lines += ["", "## Install-time hooks"] + [f"- {h}" for h in rep["hooks"]]
    if rep["binaries"]:
        lines += ["", "## Prebuilt binaries (hash them, get them only from the official release)"]
        lines += [f"- {b['file']} ({b['bytes']} bytes) sha256 {b['sha256']}" for b in rep["binaries"][:20]]
    if rep["domains"]:
        lines += ["", "## Unfamiliar domains in the code"] + [f"- {d} ({n}x)" for d, n in list(rep["domains"].items())[:20]]
    lines += [
        "",
        "## Before adopting (checklist in docs/vetting-lab.md)",
        "- Pin the exact commit or release hash.",
        "- First run on a throwaway pod with NO keys in its environment and no saved credentials.",
        "- Review the network calls above; block or allow each domain on purpose.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--out")
    ap.add_argument("--json")
    a = ap.parse_args()
    rep = scan(Path(a.path))
    text = render(rep)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=2, default=list), encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
