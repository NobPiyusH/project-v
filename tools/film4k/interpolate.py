"""Local frame interpolation of a finished master with RIFE (rife-ncnn-vulkan, Vulkan, runs on the owner's RTX 3050 4 GB laptop). Pure functions: they build
argv lists and numbers; nothing runs here except `main`, the by-hand entry point (the studio's INTERPOLATE stage uses the same builders).

Why (25 Nov 2026): 24 fps video from the Wan route looks choppy next to the 48 fps references. Measured on the laptop: RIFE rife-v4.6 doubles
1080x1920 from 24 to 48 fps at about 13 s per 2 s of video (docs/film4k.md). Real-ESRGAN ncnn at 4 GB is too slow (about 18 s per frame) and worse than
a plain resize, so it is NOT part of this.

The recipe is the one proven on the game film (E:/project-data/outputs/game-cinematic/rife_run.sh): extract the frames to PNG, `rife-ncnn-vulkan -i in -o out
-m rife-v4.6 -n <2 x frames> -j 1:1:1`, re-encode at 2x fps with libx264 crf 16 yuv420p. The audio track is COPIED (never re-encoded) from the source, so
it stays sample-identical; the video is padded with a repeated last frame if RIFE returns one frame fewer than expected, so both streams keep their length.

By hand:  python tools/film4k/interpolate.py in.mp4 out.mp4 [--factor 2] [--work E:/project-data/work/interpolate] [--dry]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_EXE = r"E:\tools\rife\rife-ncnn-vulkan-20221029-windows\rife-ncnn-vulkan.exe"
DEFAULT_MODEL = "rife-v4.6"
DEFAULT_WORKDIR = r"E:\project-data\work\interpolate"
FRAME_PATTERN = "%08d.png"  # RIFE writes 8-digit names; we use the same for the input so one pattern fits both directories
CRF = 16


def out_frame_count(in_frames: int, factor: int = 2) -> int:
    """How many frames RIFE is asked for (-n): `factor` x the input count, so duration is unchanged at `factor` x fps."""
    if in_frames < 2:
        raise ValueError("need at least 2 frames to interpolate")
    if factor < 2:
        raise ValueError("factor must be 2 or more")
    return in_frames * factor


def extract_cmd(ffmpeg: str, src: str, frames_dir: str) -> list[str]:
    """Every video frame of `src` as a numbered PNG (no audio, no frame dropping or duplication)."""
    return [ffmpeg, "-hide_banner", "-v", "error", "-y", "-i", src, "-an", "-fps_mode", "passthrough", "-start_number", "1", str(Path(frames_dir) / FRAME_PATTERN)]


def rife_cmd(exe: str, in_dir: str, out_dir: str, n_out: int, model: str = DEFAULT_MODEL) -> list[str]:
    """The proven recipe: -j 1:1:1 (one load / proc / save thread) is what fits a 4 GB card."""
    return [exe, "-i", str(in_dir), "-o", str(out_dir), "-m", model, "-n", str(n_out), "-j", "1:1:1"]


def encode_cmd(ffmpeg: str, frames_dir: str, src: str, out: str, fps_in: float, factor: int = 2, pad_frames: int = 0, has_audio: bool = True) -> list[str]:
    """The interpolated frames at `factor` x fps (libx264 crf 16, yuv420p) plus the source's audio stream copied bit for bit.

    pad_frames: repeat the last frame that many times (RIFE returned fewer frames than asked), so the video is as long as the audio."""
    fps_out = fps_in * factor
    cmd = [ffmpeg, "-hide_banner", "-v", "error", "-y", "-framerate", _num(fps_out), "-i", str(Path(frames_dir) / FRAME_PATTERN)]
    if has_audio:
        cmd += ["-i", src]
    cmd += ["-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "1:a:0"]
    if pad_frames > 0:
        cmd += ["-vf", f"tpad=stop_mode=clone:stop={int(pad_frames)}"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(CRF), "-pix_fmt", "yuv420p", "-r", _num(fps_out)]
    if has_audio:
        cmd += ["-c:a", "copy"]
    cmd += ["-movflags", "+faststart", str(out)]
    return cmd


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.6f}".rstrip("0")


def disk_needed_bytes(width: int, height: int, in_frames: int, factor: int = 2) -> int:
    """A generous estimate of the PNG work files (about 1.2 bytes per pixel per frame, input and output): used to refuse a job that cannot fit."""
    return int(width * height * 1.2 * in_frames * (1 + factor))


def probe(ffprobe: str, path: str) -> dict:
    """fps (exact fraction as a float), frame count, size and audio presence of the first video stream."""
    out = subprocess.run([ffprobe, "-v", "error", "-count_frames", "-show_entries", "stream=codec_type,width,height,r_frame_rate,nb_read_frames", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    streams = json.loads(out.stdout)["streams"]
    v = next(s for s in streams if s["codec_type"] == "video")
    num, _, den = v["r_frame_rate"].partition("/")
    return {"fps": float(num) / float(den or 1), "frames": int(v["nb_read_frames"]), "width": int(v["width"]), "height": int(v["height"]),
            "audio": any(s["codec_type"] == "audio" for s in streams)}


def interpolate(src: str, out: str, ffmpeg: str, ffprobe: str, exe: str, work_root: str, model: str = DEFAULT_MODEL, factor: int = 2) -> dict:
    """Run the whole recipe (extract, RIFE, encode) in a scratch folder under `work_root`, removed afterwards. Returns the numbers of the run."""
    info = probe(ffprobe, src)
    n_out = out_frame_count(info["frames"], factor)
    Path(work_root).mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(work_root).free
    need = disk_needed_bytes(info["width"], info["height"], info["frames"], factor)
    if free < need:
        raise RuntimeError(f"Not enough free space for the interpolation work files in {work_root}: need about {need / 1e9:.1f} GB, free {free / 1e9:.1f} GB.")
    tmp = Path(tempfile.mkdtemp(prefix="rife-", dir=work_root))
    try:
        fin, fout = tmp / "in", tmp / "out"
        fin.mkdir()
        fout.mkdir()
        _run(extract_cmd(ffmpeg, src, str(fin)), "frame extract")
        _run(rife_cmd(exe, str(fin), str(fout), n_out, model), "RIFE")
        got = len(list(fout.glob("*.png")))
        if got < n_out - factor or got > n_out:
            raise RuntimeError(f"RIFE returned {got} frames, expected about {n_out}.")
        _run(encode_cmd(ffmpeg, str(fout), src, out, info["fps"], factor, pad_frames=n_out - got, has_audio=info["audio"]), "re-encode")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {**info, "fpsIn": info["fps"], "fpsOut": info["fps"] * factor, "framesIn": info["frames"], "framesOut": n_out, "model": model}


def _run(cmd: list[str], what: str) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{what} failed (exit {proc.returncode}): {(proc.stderr or proc.stdout)[-1500:]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Interpolate a video with local RIFE (2x by default), audio copied untouched.")
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--factor", type=int, default=2)
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--work", default=DEFAULT_WORKDIR)
    ap.add_argument("--dry", action="store_true", help="print the three commands and exit")
    a = ap.parse_args(argv)
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    if a.dry:
        info = probe(ffprobe, a.src)
        n = out_frame_count(info["frames"], a.factor)
        for c in (extract_cmd(ffmpeg, a.src, "IN"), rife_cmd(a.exe, "IN", "OUT", n, a.model), encode_cmd(ffmpeg, "OUT", a.src, a.out, info["fps"], a.factor, has_audio=info["audio"])):
            print(" ".join(c))
        return 0
    print(json.dumps(interpolate(a.src, a.out, ffmpeg, ffprobe, a.exe, a.work, a.model, a.factor)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
