"""Assembly (M2c part 1): the edit. Every shot's chosen clip (owner-picked real footage, else the final, else the draft)
is fitted to the shot's slot on the narration timeline, the voice is placed and the music ducked under it, the mix is
mastered to -14 LUFS / -1 dBTP, word-timed captions are burned in (and exported as SRT), and a Premiere package
(FCP7 XML via OpenTimelineIO + the media + captions) is written so the owner can fine-tune by hand.

Frame rate: Wan renders 16 fps. Part 1 conforms to 30 fps by repeating frames; part 2 (finishing on a GPU) replaces
that with RIFE interpolation and SeedVR2 upscaling. Nothing here is ever a panned still: a shot without a clip fails
the stage before rendering (CLAUDE.md rule 10).
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import uuid
import zipfile
from pathlib import Path

from temporalio import activity

from workers.activities import sfx
from workers.activities.mixing import mix_audio
from workers.common import beat as _beat, download, drawtext_escape as _esc, log, media_duration, put_file, run_ffmpeg, workdir

FPS = 24  # cinematic; part 2a smooths Wan's 16 fps clips to a true 24 fps with RIFE
SIZES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350)}
FONT = "C\\:/Windows/Fonts/segoeui.ttf"
FONTS_DIR = "C\\:/Windows/Fonts"
MAX_STRETCH = 1.6  # slowing a clip more than this looks like slow motion: reported as a warning


# ------------------------------------------------------------------------------------------------ word timings

def _whisper_words(wav: bytes, language: str | None) -> list[dict] | None:
    """Word start/end times from Whisper on Groq (free). None when unavailable."""
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return None
    b = uuid.uuid4().hex
    crlf = "\r\n"
    fields = [("model", "whisper-large-v3"), ("response_format", "verbose_json"), ("timestamp_granularities[]", "word")]
    if language:
        fields.append(("language", language))
    head = "".join(f"--{b}{crlf}Content-Disposition: form-data; name=\"{k}\"{crlf}{crlf}{v}{crlf}" for k, v in fields)
    head += f'--{b}{crlf}Content-Disposition: form-data; name="file"; filename="line.wav"{crlf}Content-Type: audio/wav{crlf}{crlf}'
    body = head.encode() + wav + f"{crlf}--{b}--{crlf}".encode()
    req = urllib.request.Request("https://api.groq.com/openai/v1/audio/transcriptions", body, {
        "Authorization": "Bearer " + key, "Content-Type": f"multipart/form-data; boundary={b}", "User-Agent": "projectv"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            words = json.load(r).get("words") or []
        return [{"word": w["word"].strip(), "start": float(w["start"]), "end": float(w["end"])} for w in words if w.get("word")]
    except Exception as e:  # noqa: BLE001
        log.warning("Whisper word timings unavailable (%s); spacing caption words evenly", type(e).__name__)
        return None


def line_words(text: str, heard: list[dict] | None, duration: float) -> list[dict]:
    """Caption words for one line, with times relative to the line's start. The SCRIPT's words are shown (correct
    spelling and numbers); Whisper only supplies timing. Words are matched in order; when the counts differ or Whisper
    is unavailable, times are spread by word length over the spoken duration."""
    words = text.split()
    if not words:
        return []
    if heard and len(heard) == len(words):
        return [{"word": w, "start": h["start"], "end": h["end"]} for w, h in zip(words, heard)]
    start = heard[0]["start"] if heard else 0.0
    end = heard[-1]["end"] if heard else duration
    weights = [max(1, len(w)) for w in words]
    total, t, out = sum(weights), start, []
    for w, n in zip(words, weights):
        d = (end - start) * n / total
        out.append({"word": w, "start": t, "end": t + d})
        t += d
    return out


# ------------------------------------------------------------------------------------------------ captions

def caption_chunks(words: list[dict], max_words: int, max_gap: float = 0.6) -> list[dict]:
    """Groups timed words into short on-screen captions; a pause or sentence end starts a new one."""
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w["start"] - cur[-1]["end"] > max_gap or re.search(r"[.!?]$", cur[-1]["word"])):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return [{"text": " ".join(x["word"] for x in c), "start": c[0]["start"], "end": c[-1]["end"]} for c in chunks]


def _ts(t: float, ass: bool) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}" if ass else f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)):03d}"


def write_srt(chunks: list[dict], path: Path) -> None:
    path.write_text("".join(f"{i}\n{_ts(c['start'], False)} --> {_ts(c['end'], False)}\n{c['text']}\n\n"
                            for i, c in enumerate(chunks, 1)), encoding="utf-8")


def write_ass(chunks: list[dict], path: Path, w: int, h: int, vertical: bool) -> None:
    """Bold white captions with a dark outline; vertical videos place them in the lower-middle (clear of the Shorts
    and Reels UI), landscape at the bottom."""
    size = round(h * (0.044 if vertical else 0.05))
    margin_v = round(h * (0.27 if vertical else 0.07))
    style = (f"Style: Main,Segoe UI,{size},&H00FFFFFF,&H00FFFFFF,&H00101010,&H64000000,-1,0,0,0,100,100,0,0,1,"
             f"{max(3, size // 14)},2,2,{round(w * 0.08)},{round(w * 0.08)},{margin_v},1")
    # Each caption stays a moment after its last word but never overlaps the next: one caption on screen at a time.
    ends = [min(c["end"] + 0.25, chunks[i + 1]["start"]) if i + 1 < len(chunks) else c["end"] + 0.25
            for i, c in enumerate(chunks)]
    events = "".join(f"Dialogue: 0,{_ts(c['start'], True)},{_ts(ends[i], True)},Main,,0,0,0,,"
                     f"{c['text'].replace('{', '(').replace('}', ')')}\n" for i, c in enumerate(chunks))
    path.write_text(f"[Script Info]\nScriptType: v4.00+\nPlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\n\n"
                    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                    "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
                    f"Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n{style}\n\n[Events]\n"
                    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" + events,
                    encoding="utf-8")


# ------------------------------------------------------------------------------------------------ picture

def fit_plan(clip_sec: float, slot_sec: float) -> tuple[float, str]:
    """How a clip fills its slot: (speed factor applied to PTS, note). Longer clips are trimmed; shorter ones are
    slowed (never frozen, never looped)."""
    if clip_sec <= 0:
        return 1.0, "unknown clip length"
    if clip_sec >= slot_sec:
        return 1.0, "trimmed"
    f = slot_sec / clip_sec
    return f, "slowed %.2fx%s" % (f, " (looks slow-motion)" if f > MAX_STRETCH else "")


def _shot_clip(shot: dict, src: Path, out: Path, w: int, h: int) -> dict:
    clip_sec = media_duration(src)
    slot = float(shot["durationSec"])
    factor, note = fit_plan(clip_sec, slot)
    vf = [f"setpts={factor:.5f}*PTS" if factor != 1.0 else "setpts=PTS",
          f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={w}:{h}", f"fps={FPS}",
          "format=yuv420p"]
    if shot.get("overlayText"):
        vf.append(f"drawtext=fontfile='{FONT}':text='{_esc(shot['overlayText'])}':expansion=none:x=(w-text_w)/2:y=h*0.12:"
                  f"fontsize={round(h * 0.035)}:fontcolor=white:shadowcolor=black@0.7:shadowx=2:shadowy=2")
    run_ffmpeg(["-i", str(src), "-an", "-vf", ",".join(vf), "-t", f"{slot:.3f}", "-c:v", "libx264",
                "-preset", "medium", "-crf", "14", str(out)])
    return {"shot": shot["n"], "kind": shot["kind"], "clipSec": round(clip_sec, 2), "fit": note}


# ------------------------------------------------------------------------------------------------ Premiere package

def write_premiere_package(shots: list[dict], clip_files: list[Path], audio: Path | None, srt: Path | None,
                           master: Path, out_zip: Path, title: str, w: int, h: int) -> None:
    """FCP7 XML (Premiere imports it) through OpenTimelineIO, zipped with the conformed clips, the mix and the
    captions. Transitions/effects are baked into the master; this is a conform timeline for hand fine-tuning."""
    import opentimelineio as otio

    rate = FPS
    tl = otio.schema.Timeline(name=title)
    vtrack = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    cursor = 0.0
    for shot, f in zip(shots, clip_files):
        start = float(shot["startSec"])
        if start > cursor + 1e-3:
            vtrack.append(otio.schema.Gap(source_range=otio.opentime.TimeRange(
                duration=otio.opentime.RationalTime(round((start - cursor) * rate), rate))))
        dur = otio.opentime.RationalTime(round(float(shot["durationSec"]) * rate), rate)
        ref = otio.schema.ExternalReference(target_url=f"media/{f.name}", available_range=otio.opentime.TimeRange(
            duration=dur))
        vtrack.append(otio.schema.Clip(name=f"Shot {shot['n']} ({shot['kind']})", media_reference=ref,
                                       source_range=otio.opentime.TimeRange(duration=dur)))
        cursor = start + float(shot["durationSec"])
    tl.tracks.append(vtrack)
    if audio is not None:
        atrack = otio.schema.Track(name="A1 mix", kind=otio.schema.TrackKind.Audio)
        adur = otio.opentime.RationalTime(round(media_duration(audio) * rate), rate)
        atrack.append(otio.schema.Clip(name="Mix (voice + music, -14 LUFS)", media_reference=otio.schema.ExternalReference(
            target_url=f"media/{audio.name}", available_range=otio.opentime.TimeRange(duration=adur)),
            source_range=otio.opentime.TimeRange(duration=adur)))
        tl.tracks.append(atrack)
    xml = out_zip.with_suffix(".xml")
    otio.adapters.write_to_file(tl, str(xml), adapter_name="fcp_xml")
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(xml, "project.xml")
        z.writestr("README.txt", (f"{title}\n\nPremiere Pro: File > Import > project.xml (keep the media folder next to "
                                  f"it). Sequence {w}x{h} @ {FPS} fps. The finished master is master.mp4; captions are "
                                  "captions.srt.\n"))
        for f in clip_files:
            z.write(f, f"media/{f.name}", compress_type=zipfile.ZIP_STORED)
        if audio is not None:
            z.write(audio, f"media/{audio.name}")
        if srt is not None:
            z.write(srt, "captions.srt")
        z.write(master, "master.mp4", compress_type=zipfile.ZIP_STORED)


# ------------------------------------------------------------------------------------------------ activity

@activity.defn(name="assemble_video")
def assemble_video(req: dict) -> dict:
    """req: {videoId, title, aspectRatio, language, captions,
             shots:[{n, uri, kind, startSec, durationSec, overlayText}],
             narration:[{uri, startSec, tempo, text, durationSec}], musicUri, totalSec}
    -> {masterUri, packageUri, srtUri, durationSec, width, height, fps, fits:[...], captionWords, wordTimings}"""
    w, h = SIZES.get(req.get("aspectRatio") or "16:9", SIZES["16:9"])
    vertical = h > w
    shots = sorted(req["shots"], key=lambda s: s["startSec"])
    with workdir("assemble") as tmp:
        tmp = Path(tmp)
        clip_files, fits = [], []
        for s in shots:
            src = download(s["uri"], tmp / f"src{s['n']:03d}.mp4")
            out = tmp / f"shot{s['n']:03d}.mp4"
            fits.append(_shot_clip(s, src, out, w, h))
            clip_files.append(out)
            _beat(f"shot {s['n']}")
        listing = tmp / "clips.txt"
        listing.write_text("".join(f"file '{c.as_posix()}'\n" for c in clip_files), encoding="utf-8")
        picture = tmp / "picture.mp4"
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(picture)])
        total = media_duration(picture)

        # Sound effects (Freesound, CC0 / CC BY) for shots with an sfxQuery; none without a key.
        fx, sfx_credits = sfx.fetch(sfx.segments(shots), tmp)
        mix = mix_audio(req.get("narration") or [], req.get("musicUri"), total, tmp, tmp / "mix.wav",
                        lufs=-14.0, true_peak=-1.0, two_pass=True, sfx=fx)
        _beat("mix")

        # Captions: the script's words, timed by Whisper per line.
        all_words, timed = [], 0
        if req.get("captions", True):
            for i, line in enumerate(req.get("narration") or []):
                wav = download(line["uri"], tmp / f"cap{i}.wav")
                heard = _whisper_words(wav.read_bytes(), req.get("language"))
                timed += 1 if heard else 0
                tempo = float(line.get("tempo") or 1.0)
                for wd in line_words(line.get("text") or "", heard, float(line.get("durationSec") or 0)):
                    all_words.append({"word": wd["word"], "start": float(line["startSec"]) + wd["start"] / tempo,
                                      "end": float(line["startSec"]) + wd["end"] / tempo})
        chunks = caption_chunks(all_words, max_words=3 if vertical else 7)
        srt = ass = None
        if chunks:
            srt, ass = tmp / "captions.srt", tmp / "captions.ass"
            write_srt(chunks, srt)
            write_ass(chunks, ass, w, h, vertical)

        master = tmp / "master.mp4"
        args = ["-i", str(picture)]
        if mix is not None:
            args += ["-i", str(mix)]
        vf = f"subtitles=filename='{ass.as_posix().replace(':', chr(92) + ':')}':fontsdir='{FONTS_DIR}'" if ass else "null"
        args += ["-vf", vf, "-map", "0:v"] + (["-map", "1:a", "-c:a", "aac", "-b:a", "256k", "-ar", "48000"] if mix else [])
        # Cut exactly at the planned length: per-shot frame rounding must never push a Short past 60 s.
        if req.get("totalSec"):
            args += ["-t", f"{min(float(req['totalSec']), total):.3f}"]
        args += ["-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(FPS),
                 "-movflags", "+faststart", "-shortest", str(master)]
        run_ffmpeg(args)
        _beat("master")

        package = tmp / "premiere_package.zip"
        write_premiere_package(shots, clip_files, mix, srt, master, package, req.get("title") or "Project V", w, h)
        base = f"videos/{req['videoId']}/master"
        master_uri = put_file(f"{base}/master.mp4", master, "video/mp4")
        package_uri = put_file(f"{base}/premiere_package.zip", package, "application/zip")
        srt_uri = put_file(f"{base}/captions.srt", srt, "application/x-subrip") if srt else None
        duration = media_duration(master)
    log.info("Master for %s: %.1fs %dx%d, %d shots, %d caption chunks", req["videoId"], duration, w, h, len(shots), len(chunks))
    return {"masterUri": master_uri, "packageUri": package_uri, "srtUri": srt_uri, "durationSec": round(duration, 3),
            "width": w, "height": h, "fps": FPS, "fits": fits, "captionChunks": len(chunks),
            "linesTimedByWhisper": timed, "sfxCredits": sfx_credits}
