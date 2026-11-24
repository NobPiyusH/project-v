"""Thin ffmpeg/ffprobe runners. Everything that parses their text output lives in tier1/tier2 as pure functions."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass


def _exe(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} not found on PATH")
    return path


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    fps: float
    width: int
    height: int
    has_audio: bool


def probe(video: str) -> MediaInfo:
    out = subprocess.run(
        [_exe("ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate",
         "-of", "json", video],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    data = json.loads(out)
    vs = next(s for s in data["streams"] if s["codec_type"] == "video")
    num, _, den = vs["r_frame_rate"].partition("/")
    return MediaInfo(
        duration=float(data["format"]["duration"]), fps=float(num) / float(den or 1), width=int(vs["width"]),
        height=int(vs["height"]), has_audio=any(s["codec_type"] == "audio" for s in data["streams"]),
    )  # fmt: skip


def run_filter(video: str, args: list[str]) -> str:
    """Run `ffmpeg -i video <args> -f null -` and return stderr (where the analysis filters print)."""
    cmd = [_exe("ffmpeg"), "-hide_banner", "-nostats", "-i", video, *args, "-f", "null", "-"]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stderr


def extract_frame_cmd(video: str, t: float, out: str) -> list[str]:
    """argv that writes one JPEG frame at time t (used to feed the Tier 3 judge)."""
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", "-q:v", "2", out]
