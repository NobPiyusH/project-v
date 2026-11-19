"""Move the benchmark's small files to and from a pod without opening anything on the laptop.

  pack:   uv run python -m workers.bench.wan_fp8.pack pack --inputs bench_inputs --out bench_bundle
          -> bench_bundle/bundle.tgz (manifest, the original keyframes, the pod script; the pod resizes each keyframe
             with PIL LANCZOS exactly as the Modal app does) and bench_bundle/exec_chunks/NNN.json: argv lists for RunPod exec-pod that append
             the bundle's base64 to /workspace/bundle.b64 (about 1 MB per call). Then on the pod:
             base64 -d /workspace/bundle.b64 > /workspace/bundle.tgz && tar -xzf /workspace/bundle.tgz -C /workspace
  join:   uv run python -m workers.bench.wan_fp8.pack join --parts dir_of_base64_parts --dest bench_out/shot01.mp4
          (fallback for getting clips back when runpodctl is not available: see the runbook)

No network access; local files only.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import tarfile
from pathlib import Path

ARG_CHARS = 4000     # exec-pod: at most 4096 characters per argument
ARGS_PER_CALL = 250  # exec-pod: at most 256 arguments per call
APPEND = 'for a; do printf %s "$a"; done >> /workspace/bundle.b64'


def exec_chunks(b64: str) -> list[list[str]]:
    pieces = [b64[i:i + ARG_CHARS] for i in range(0, len(b64), ARG_CHARS)]
    return [["sh", "-c", APPEND, "append"] + pieces[i:i + ARGS_PER_CALL] for i in range(0, len(pieces), ARGS_PER_CALL)]


def pack(inputs: Path, out: Path) -> Path:
    manifest = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(f"bench_in/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

        for c in manifest["clips"]:
            add(c["image"], (inputs / c["image"]).read_bytes())
        add("manifest.json", json.dumps(manifest, indent=1, ensure_ascii=False).encode("utf-8"))
        add("bench_wan_fp8.py", (Path(__file__).parent / "bench_wan_fp8.py").read_bytes())
    out.mkdir(parents=True, exist_ok=True)
    (out / "bundle.tgz").write_bytes(buf.getvalue())
    chunks = out / "exec_chunks"
    chunks.mkdir(exist_ok=True)
    for old in chunks.glob("*.json"):
        old.unlink()
    for i, argv in enumerate(exec_chunks(base64.b64encode(buf.getvalue()).decode("ascii"))):
        (chunks / f"{i:03d}.json").write_text(json.dumps(argv))
    return out / "bundle.tgz"


def join(parts: Path, dest: Path) -> None:
    """Concatenate base64 parts (part files sorted by name) and decode to dest."""
    b64 = "".join(p.read_text().strip() for p in sorted(parts.iterdir()) if p.is_file())
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(base64.b64decode(b64))


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("pack")
    a.add_argument("--inputs", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    j = sub.add_parser("join")
    j.add_argument("--parts", type=Path, required=True)
    j.add_argument("--dest", type=Path, required=True)
    args = p.parse_args(argv)
    if args.cmd == "pack":
        bundle = pack(args.inputs, args.out)
        n = len(list((args.out / "exec_chunks").glob("*.json")))
        print(f"{bundle} ({bundle.stat().st_size / 1e6:.2f} MB), {n} exec-pod call(s) in {args.out / 'exec_chunks'}")
    else:
        join(args.parts, args.dest)
        print(f"wrote {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
