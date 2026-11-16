"""Animatic: keyframes + narration + music on the real timeline, to judge story and pacing BEFORE any video money is
spent. It is clearly marked as a draft (a storyboard), never as final output (CLAUDE.md rule 10)."""

from __future__ import annotations

from pathlib import Path

from temporalio import activity

from workers.activities.mixing import mix_audio
from workers.common import download, media_duration, put_file, run_ffmpeg, workdir

FPS = 30
SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}
FONT = "C\\:/Windows/Fonts/segoeui.ttf"


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")


@activity.defn(name="render_animatic")
def render_animatic(req: dict) -> dict:
    """req: {videoId, title, shots:[{index, startSec, durationSec, imageUri, overlayText}],
             narration:[{uri, startSec, tempo}], musicUri, totalSec, aspectRatio}
    -> {uri, durationSec}"""
    # The animatic matches the video's frame: a vertical Short is rendered vertical (its keyframes are vertical too).
    W, H = SIZES.get(req.get("aspectRatio") or "16:9", SIZES["16:9"])
    with workdir("animatic") as tmp:
        tmp = Path(tmp)
        shots = sorted(req["shots"], key=lambda s: s["startSec"])

        # 1. One short clip per shot: the still with a slow push-in, a shot label and any on-screen overlay.
        clips = []
        for n, shot in enumerate(shots):
            img = download(shot["imageUri"], tmp / f"k{n}{Path(shot['imageUri']).suffix}")
            dur = max(0.5, float(shot["durationSec"]))
            frames = int(round(dur * FPS))
            label = _esc(f"ANIMATIC DRAFT  |  shot {shot['index']}  |  {dur:.1f}s")
            vf = (
                f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
                f"zoompan=z='min(1+0.06*on/{frames},1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
                f"drawbox=x=0:y=ih-56:w=iw:h=56:color=black@0.55:t=fill,"
                f"drawtext=fontfile='{FONT}':text='{label}':x=24:y=h-40:fontsize=24:fontcolor=white@0.85"
            )
            if shot.get("overlayText"):
                vf += (f",drawtext=fontfile='{FONT}':text='{_esc(shot['overlayText'])}':x=(w-text_w)/2:y=h*0.12:"
                       "fontsize=56:fontcolor=white:shadowcolor=black@0.7:shadowx=2:shadowy=2")
            clip = tmp / f"c{n}.mp4"
            run_ffmpeg(["-loop", "1", "-framerate", str(FPS), "-i", str(img), "-vf", vf, "-frames:v", str(frames),
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(clip)])
            clips.append(clip)
            activity.heartbeat(f"shot {n + 1}/{len(shots)}")

        concat_list = tmp / "clips.txt"
        concat_list.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
        picture = tmp / "picture.mp4"
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(picture)])
        total = media_duration(picture)

        # 2. Audio: narration at its start times, music ducked under the voice (shared with the master).
        audio = mix_audio(req.get("narration") or [], req.get("musicUri"), total, tmp, tmp / "audio.wav")
        if audio is not None:
            final = tmp / "animatic.mp4"
            run_ffmpeg(["-i", str(picture), "-i", str(audio), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(final)])
        else:
            final = picture

        uri = put_file(f"videos/{req['videoId']}/animatic/animatic.mp4", final, "video/mp4")
        return {"uri": uri, "durationSec": round(media_duration(final), 3)}
