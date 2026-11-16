"""Media activities (FFmpeg, OTIO, TTS, captions...). M1 only proves the Java -> Python hop.

Activity names must match the Java MediaActivities interface (@ActivityMethod(name=...)).
"""

import shutil
import subprocess

from temporalio import activity


def _first_line(cmd: list[str]) -> str | None:
    if shutil.which(cmd[0]) is None:
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (out.stdout or out.stderr).strip()
    return text.splitlines()[0] if text else None


@activity.defn(name="tool_versions")
def tool_versions() -> str:
    ffmpeg = _first_line(["ffmpeg", "-version"])
    ffprobe = _first_line(["ffprobe", "-version"])
    parts = [
        f"ffmpeg: {ffmpeg.split(' Copyright')[0] if ffmpeg else 'NOT FOUND'}",
        f"ffprobe: {'ok' if ffprobe else 'NOT FOUND'}",
    ]
    return "; ".join(parts)
