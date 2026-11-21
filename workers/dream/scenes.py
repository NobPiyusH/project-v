"""SVI scenes (21 Nov 2026): the laptop joins the beats of each scene into one continuous shot before the Finish.

A scene is 1-5 beats of 81 frames @16 fps rendered by the pod in one SVI graph (workers/pod/server.py scene_graph).
Beat k > 1 starts on the anchor (frame 0, dropped) and its frames 1..12 re-draw the previous beat's last 12 frames, so
the join is a linear 12-frame cross-fade, then frames 13..80 are new. This is the paid bench's recipe
(C:\\dev\\bench\\svi_test\\kit\\assemble_shot.sh); no colour correction (a flat saturation "fix" made shots duller).
The result is cut to the scene's length (e.g. 321 frames = 20 s). Between scenes the Finish's own join applies
(the control plane sends a hard cut, or a 0.5 s dissolve).
"""

from __future__ import annotations

from pathlib import Path

from workers.common import run_ffmpeg

BEAT_FRAMES = 81
OVERLAP = 12         # frames 1..12 of beat k > 1 overlap the last 12 of beat k-1 (3 motion latents)
NEW = BEAT_FRAMES - 1 - OVERLAP  # 68 new frames per later beat


def scene_frames(beats: int) -> int:
    """Frames a scene of `beats` beats has before it is cut to length (5 beats: 353)."""
    return BEAT_FRAMES + max(0, beats - 1) * NEW if beats > 0 else 0


def beats_for(seconds: float, fps: int = 16) -> int:
    """Beats needed for a scene of `seconds` (10 s: 3, 15 s: 4, 20 s: 5)."""
    want = int(round(seconds * fps)) + 1
    n = 1
    while scene_frames(n) < want:
        n += 1
    return n


def join_graph(beats: int, frames: int | None = None) -> str:
    """filter_complex joining inputs 0..beats-1 (one per beat) into [o]: linear 12-frame cross-fades at the joins,
    anchor frames dropped, cut to `frames` (None = keep every frame)."""
    seg = lambda i, s, e, lab: f"[{i}:v]trim=start_frame={s}:end_frame={e},setpts=PTS-STARTPTS[{lab}];"  # noqa: E731
    last_body = BEAT_FRAMES - OVERLAP  # 69: where a beat's tail (the next beat's overlap) starts
    w = f"(N+1)/{OVERLAP + 1}"
    g = seg(0, 0, last_body if beats > 1 else BEAT_FRAMES, "m0")
    order = ["[m0]"]
    for k in range(1, beats):
        g += seg(k - 1, last_body, BEAT_FRAMES, f"t{k}") + seg(k, 1, OVERLAP + 1, f"h{k}")
        g += seg(k, OVERLAP + 1, last_body if k < beats - 1 else BEAT_FRAMES, f"m{k}")
        g += f"[t{k}][h{k}]blend=all_expr='A*(1-({w}))+B*({w})'[x{k}];"
        order += [f"[x{k}]", f"[m{k}]"]
    cut = f",trim=end_frame={frames}" if frames else ""
    return g + "".join(order) + f"concat=n={len(order)}:v=1:a=0{cut},setpts=PTS-STARTPTS[o]"


def assemble(srcs: list[Path], clips: list[dict], scenes: list[int], frames: list[int] | None, tmp: Path,
             fps: int = 16) -> tuple[list[Path], list[dict]]:
    """Beat files -> one file per scene, in order. `scenes`: beats per scene (sums to len(srcs)); `frames`: each
    scene's length in frames (None/0 = all). The scene keeps its first beat's description (for the ambience)."""
    if sum(scenes) != len(srcs):
        raise ValueError(f"SVI scenes add up to {sum(scenes)} beats but {len(srcs)} clips were sent")
    out_srcs, out_clips, i = [], [], 0
    for n, beats in enumerate(scenes):
        part = srcs[i:i + beats]
        cut = (frames[n] if frames and n < len(frames) and frames[n] else None)
        dst = tmp / f"scene{n}.mp4"
        args: list[str] = []
        for p in part:
            args += ["-i", str(p)]
        run_ffmpeg([*args, "-filter_complex", join_graph(beats, cut), "-map", "[o]", "-r", str(fps),
                    "-c:v", "libx264", "-crf", "10", "-preset", "slow", "-pix_fmt", "yuv420p", str(dst)], cwd=tmp)
        out_srcs.append(dst)
        out_clips.append({**clips[i], "fps": fps})
        i += beats
    return out_srcs, out_clips
