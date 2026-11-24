"""Turn an external render (Blender PNG sequence or a video) into a Wan 2.2 Fun VACE control video (pure functions; no ffmpeg run).

Why (25 Nov 2026): the stock control video is a zoomed still turned into an edge video (camera_presets.control_video_cmd), a FAKE path with no
parallax. A real 3D camera path from Blender gives true parallax. VACE wants the same kind of input the fake path produces: an edge video
at the generation size, 4k+1 frames, size a multiple of 16. `mode="edges"` uses the identical edgedetect settings; `mode="depth"` keeps the
grey render as it is (for a depth-style pass).
"""

from __future__ import annotations

from camera_graphs import check_size

EDGE = "edgedetect=low=0.1:high=0.3"


def control_from_render_cmd(src: str, out: str, w: int, h: int, frames: int, fps: int = 16, mode: str = "edges", image_seq: bool = True) -> list[str]:
    """ffmpeg argv that writes the VACE control video from `src` (a printf pattern such as f_%04d.png when image_seq, else a video file).
    The render is scaled to cover w x h and centre-cropped, cut to exactly `frames` frames, and written as H.264 yuv420p."""
    check_size(w, h, frames)
    if mode not in ("edges", "depth"):
        raise ValueError(f"mode must be 'edges' or 'depth', got {mode!r}")
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h}"
    if mode == "edges":
        vf += f",{EDGE}"
    vf += ",format=yuv420p"
    pre = ["-framerate", str(fps)] if image_seq else []
    return ["ffmpeg", "-y", *pre, "-i", src, "-vf", vf, "-r", str(fps), "-frames:v", str(frames), "-c:v", "libx264", "-crf", "12", out]


if __name__ == "__main__":
    import subprocess
    import sys

    folder, out = sys.argv[1], sys.argv[2]
    w, h, n = (int(x) for x in sys.argv[3:6]) if len(sys.argv) >= 6 else (720, 1280, 81)
    subprocess.run(control_from_render_cmd(folder.rstrip("/\\") + "/f_%04d.png", out, w, h, n), check=True)
