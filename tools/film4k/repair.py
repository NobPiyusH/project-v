"""Shot repair: fix a bad moment inside a finished clip without regenerating the whole scene (pure planning + ffmpeg argv builders).

Why (24 Nov 2026): Act 1 scene 1 had a dragon wing that turned sky-blue for about 2 s, and three scenes were nearly static. Regenerating a scene
costs about $0.30 and may break a good shot. Two repair modes:
  reroll       regenerate the whole beat with a new seed (cheap, for big or long defects);
  vace_inpaint regenerate only a box (VACE with a mask) for a short window, then composite it back with a feathered edge.
Nothing here runs ffmpeg or touches a pod: callers run the argv lists. The VACE graph is camera_graphs.vace_repair_graph.
"""

from __future__ import annotations

from dataclasses import dataclass

CONTEXT_FRAMES = 4  # frames kept before and after the window so VACE sees the motion around the defect
MAX_INPAINT_S = 4.0  # longer defects drift: reroll the beat instead
MAX_INPAINT_AREA = 0.45  # a box covering more of the frame than this is a reroll


@dataclass(frozen=True)
class Region:
    """A defect: a normalized box (x, y, w, h in 0..1 of the frame) between two times (seconds into the clip)."""

    x: float
    y: float
    w: float
    h: float
    t0: float
    t1: float

    def validate(self) -> None:
        if not (0 <= self.x < 1 and 0 <= self.y < 1 and 0 < self.w <= 1 and 0 < self.h <= 1):
            raise ValueError("box must be inside the frame (values 0..1)")
        if self.x + self.w > 1.0001 or self.y + self.h > 1.0001:
            raise ValueError("box leaves the frame")
        if not 0 <= self.t0 < self.t1:
            raise ValueError("window must satisfy 0 <= t0 < t1")


def choose_mode(region: Region) -> str:
    region.validate()
    if region.t1 - region.t0 > MAX_INPAINT_S or region.w * region.h > MAX_INPAINT_AREA:
        return "reroll"
    return "vace_inpaint"


def window_frames(region: Region, fps: float, total_frames: int) -> tuple[int, int]:
    """First frame and frame count of the segment to regenerate: the defect plus context on both sides, snapped to 4k+1 frames (VACE rule)."""
    region.validate()
    start = max(0, int(region.t0 * fps) - CONTEXT_FRAMES)
    end = min(total_frames, int(region.t1 * fps) + 1 + CONTEXT_FRAMES)
    n = end - start
    n = max(5, ((n - 1) // 4 + 1) * 4 + 1)  # round UP to 4k+1
    if start + n > total_frames:  # near the end: slide the window back instead of running past the clip
        start = max(0, total_frames - n)
        n = min(n, total_frames - start)
        n = ((n - 1) // 4) * 4 + 1  # round DOWN when the clip is too short
    return start, n


def _box_px(region: Region, w: int, h: int) -> tuple[int, int, int, int]:
    return int(region.x * w), int(region.y * h), max(2, int(region.w * w)), max(2, int(region.h * h))


def control_and_mask_cmds(
    clip: str, region: Region, fps: float, total_frames: int, w: int, h: int, out_control: str, out_mask: str
) -> list[list[str]]:
    """Two ffmpeg commands: (1) the control video = the segment at VACE size with the box painted gray (the region to regenerate),
    (2) the mask video = black with a white box. Both cover exactly `window_frames` frames."""
    start, n = window_frames(region, fps, total_frames)
    bx, by, bw, bh = _box_px(region, w, h)
    seg = ["-vf", f"select='between(n,{start},{start + n - 1})',setpts=N/{fps}/TB,scale={w}:{h}"]
    control = ["ffmpeg", "-y", "-i", clip, *seg[:1], seg[1] + f",drawbox=x={bx}:y={by}:w={bw}:h={bh}:color=gray:t=fill",
               "-frames:v", str(n), "-an", "-c:v", "libx264", "-crf", "12", out_control]  # fmt: skip
    mask = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r={fps}", "-vf",
            f"drawbox=x={bx}:y={by}:w={bw}:h={bh}:color=white:t=fill,format=gray,format=yuv420p",
            "-frames:v", str(n), "-an", "-c:v", "libx264", "-crf", "12", out_mask]  # fmt: skip
    return [control, mask]


def mask_chain(region: Region, feather: int = 25) -> str:
    """lavfi filters that turn a black frame into a feathered white box (the area the repaired pixels may replace)."""
    return f"drawbox=x=iw*{region.x}:y=ih*{region.y}:w=iw*{region.w}:h=ih*{region.h}:color=white:t=fill,gblur=sigma={feather},format=gray"


def composite_cmd(original: str, repaired: str, region: Region, fps: float, total_frames: int, orig_w: int, orig_h: int,
                  out_segment: str, feather: int = 25) -> list[str]:  # fmt: skip
    """Blend the repaired segment back over the original frames through a feathered box mask, at the ORIGINAL size.
    Frames outside the box keep the original pixels, so the good parts of the shot stay sharp."""
    start, n = window_frames(region, fps, total_frames)
    t0 = start / fps
    graph = (
        f"[1:v]scale={orig_w}:{orig_h}:flags=lanczos,format=yuv420p[rep];"
        f"[2:v]{mask_chain(region, feather)}[m];"
        "[0:v]format=yuv420p[org];"
        "[org][rep][m]maskedmerge"
    )
    return [
        "ffmpeg", "-y", "-ss", f"{t0:.4f}", "-t", f"{n / fps:.4f}", "-i", original, "-i", repaired,
        "-f", "lavfi", "-i", f"color=c=black:s={orig_w}x{orig_h}:r={fps}",
        "-filter_complex", graph, "-frames:v", str(n), "-an", "-c:v", "libx264", "-crf", "12", out_segment,
    ]  # fmt: skip


def splice_cmd(original: str, segment: str, region: Region, fps: float, total_frames: int, out: str) -> list[str]:
    """Replace frames [start, start+n) of the original with the repaired segment (video only; audio is re-attached by the editor)."""
    start, n = window_frames(region, fps, total_frames)
    end = start + n
    return [
        "ffmpeg", "-y", "-i", original, "-i", segment, "-filter_complex",
        f"[0:v]select='lt(n,{start})',setpts=N/{fps}/TB[a];"
        f"[1:v]setpts=N/{fps}/TB[b];"
        f"[0:v]select='gte(n,{end})',setpts=N/{fps}/TB[c];"
        "[a][b][c]concat=n=3:v=1:a=0",
        "-r", f"{fps}", "-c:v", "libx264", "-crf", "12", out,
    ]  # fmt: skip
