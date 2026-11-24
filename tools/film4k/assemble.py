"""Assemble scenes on the laptop: cross-fade 3 beats, post camera move, ONE RIFE pass to 48 fps at 1.25x speed, grade, HEVC; then concat.

Usage: python tools/film4k/assemble.py --dir E:/project-data/outputs/game-4k --name act1 --scenes 1 2 3 4 5 6 7 8 [--rife PATH]
Reads beats4k/sNN_bK.mp4 (falls back to beats720 for a beat that has no 4K file), writes scenes/sceneNN.mp4 (kept, so a rerun resumes),
<name>_4k_48fps.mp4 and <name>_720p_preview.mp4. Runs on the laptop GPU (RIFE ncnn/Vulkan, about 8 min per scene at 4K).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from camera import camera_filter, join_filter, rife_target  # noqa: E402

DEFAULT_RIFE = r"E:\tools\rife\rife-ncnn-vulkan-20221029-windows\rife-ncnn-vulkan.exe"
GRADE = "eq=saturation=0.92:contrast=1.04,noise=alls=5:allf=t,format=yuv420p"


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", *args], check=True)


def beat_path(base: Path, sid: int, k: int) -> Path:
    p4 = base / "beats4k" / f"s{sid:02d}_b{k}.mp4"
    return p4 if p4.exists() else base / "beats720" / f"s{sid:02d}_b{k}.mp4"


def frame_count(path: Path) -> int:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return int(out.strip())


def build_scene(base: Path, sid: int, rife: str) -> Path:
    out = base / "scenes" / f"scene{sid:02d}.mp4"
    if out.exists():
        return out
    tmp = base / "tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "in").mkdir(parents=True)
    (tmp / "out").mkdir()
    ins = [a for k in (1, 2, 3) for a in ("-i", str(beat_path(base, sid, k)))]
    ff(
        *ins,
        "-filter_complex",
        join_filter() + "," + camera_filter(sid),
        "-r",
        "16",
        "-c:v",
        "libx264",
        "-crf",
        "12",
        "-preset",
        "veryfast",
        str(tmp / "scene16.mp4"),
        "-y",
    )
    n = frame_count(tmp / "scene16.mp4")
    ff("-i", str(tmp / "scene16.mp4"), str(tmp / "in" / "%06d.png"), "-y")
    subprocess.run(
        [rife, "-i", str(tmp / "in"), "-o", str(tmp / "out"), "-m", "rife-v4.6", "-n", str(rife_target(n)), "-j", "1:1:1"],
        check=True,
        capture_output=True,
    )
    out.parent.mkdir(exist_ok=True)
    ff(
        "-framerate",
        "48",
        "-i",
        str(tmp / "out" / "%08d.png"),
        "-vf",
        GRADE,
        "-c:v",
        "libx265",
        "-crf",
        "17",
        "-preset",
        "fast",
        "-tag:v",
        "hvc1",
        str(out),
        "-y",
    )
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"scene {sid:02d} done ({n} -> {rife_target(n)} frames)", flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--scenes", nargs="+", type=int, required=True)
    ap.add_argument("--rife", default=DEFAULT_RIFE)
    a = ap.parse_args()
    base = Path(a.dir)
    parts = [build_scene(base, sid, a.rife) for sid in a.scenes]
    lst = base / "concat_list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    final = base / f"{a.name}_4k_48fps.mp4"
    ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(final), "-y")
    ff(
        "-i",
        str(final),
        "-vf",
        "scale=720:-2",
        "-c:v",
        "libx264",
        "-crf",
        "24",
        "-pix_fmt",
        "yuv420p",
        str(base / f"{a.name}_720p_preview.mp4"),
        "-y",
    )
    print("ASSEMBLE DONE", a.name, flush=True)


if __name__ == "__main__":
    main()
