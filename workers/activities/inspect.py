"""Inspector: the second, independent level of checking (owner, 16 Nov 2026: "a manager who watches everything").

Every stage already checks its own work, but a check made with the same kind of model can share the same blind
spot (Gemini's transcriber hid instructions that Gemini TTS had read aloud). The Inspector looks at the finished
files with different, mostly deterministic tools: Whisper for speech, FFmpeg for sound and picture, simple image
statistics for keyframes. It is free to run (local FFmpeg + Groq's free Whisper) and never changes anything: it only
reports findings. The control plane decides what a finding means (redo once, then ask the owner).

Finding severities: FAIL (must not pass unseen), WARN (shown, does not block), INFO.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np
from temporalio import activity

from workers.activities.voice import _whisper, judge
from workers.common import beat as _beat, download, ffmpeg_bin, ffprobe_bin, log, RateLimiter, workdir

# Groq's free Whisper tier allows 20 requests/minute; the narration stage itself also uses it.
WHISPER_LIMIT = RateLimiter(per_minute=15)

ASPECTS = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0, "4:5": 4 / 5}


def finding(severity: str, code: str, message: str, ref: str | None = None) -> dict:
    return {"severity": severity, "code": code, "message": message, "ref": ref}


# ------------------------------------------------------------------------------------------------ measurements

def _probe(path: Path) -> dict:
    out = subprocess.run([ffprobe_bin(), "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "default=nw=1", str(path)], capture_output=True, text=True, check=True).stdout
    info: dict = {"video": False, "audio": False}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        if k == "codec_type":
            info[v] = True
        elif k in ("width", "height") and v.isdigit():
            info[k] = int(v)
        elif k == "duration":
            try:
                info["duration"] = float(v)
            except ValueError:
                pass
    return info


def _ffmpeg_log(args: list[str]) -> str:
    """Runs an analysis filter and returns FFmpeg's log (where the filters print their results)."""
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", *args, "-f", "null", "-"],
                          capture_output=True, text=True, check=False)
    return proc.stderr


def loudness(path: Path) -> dict:
    """Integrated loudness (LUFS) and true peak (dBTP), EBU R128."""
    text = _ffmpeg_log(["-i", str(path), "-vn", "-af", "ebur128=peak=true"])
    summary = text[text.rfind("Summary:"):] if "Summary:" in text else text
    lufs = re.search(r"I:\s*(-?[\d.]+|-inf) LUFS", summary)
    peak = re.search(r"Peak:\s*(-?[\d.]+|-inf) dBFS", summary)
    as_float = lambda m: float(m.group(1)) if m and m.group(1) != "-inf" else float("-inf")  # noqa: E731
    return {"lufs": as_float(lufs), "truePeak": as_float(peak)}


def silences(path: Path, noise_db: float = -45, min_sec: float = 0.5) -> list[tuple[float, float]]:
    text = _ffmpeg_log(["-i", str(path), "-vn", "-af", f"silencedetect=noise={noise_db}dB:d={min_sec}"])
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", text)]
    out = []
    for i, s in enumerate(starts):
        out.append((max(0.0, s), ends[i] if i < len(ends) else float("inf")))
    return out


def black_segments(path: Path, min_sec: float = 0.5) -> list[tuple[float, float]]:
    text = _ffmpeg_log(["-i", str(path), "-an", "-vf", f"blackdetect=d={min_sec}:pix_th=0.08"])
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:(-?[\d.]+) black_end:(-?[\d.]+)", text)]


def thumb(path: Path, w: int, h: int) -> np.ndarray:
    """Grey w x h thumbnail of an image as floats 0..255 (FFmpeg decodes every format we store)."""
    raw = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-vf", f"scale={w}:{h},format=gray",
                          "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw[: w * h], dtype=np.uint8).astype(np.float32).reshape(h, w)


def dhash(path: Path) -> int:
    t = thumb(path, 9, 8)
    bits = (t[:, 1:] > t[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


# ------------------------------------------------------------------------------------------------ checks per stage

def check_narration(req: dict, tmp: Path) -> tuple[list[dict], list[str]]:
    findings, checked = [], []
    for line in req.get("lines") or []:
        ref = f"line {line['index'] + 1}"
        if not line.get("uri"):
            continue  # missing audio is reported by the control plane
        wav = download(line["uri"], tmp / f"line{line['index']}.wav")
        WHISPER_LIMIT.wait()
        heard = _whisper(wav.read_bytes())
        if heard is None:
            findings.append(finding("WARN", "SPEECH_UNCHECKED", "Whisper was not available, so the words of this line "
                                                                "were not checked independently.", ref))
        else:
            ok, why = judge(line["text"], heard)
            if not ok:
                findings.append(finding("FAIL", "SPEECH_MISMATCH", f"Spoken words don't match the script ({why}). "
                                                                   f"Heard: \u201c{heard[:200]}\u201d", ref))
        dur = _probe(wav).get("duration", 0.0)
        words = len(line["text"].split())
        if dur > 0 and words >= 4:
            rate = words / dur
            if rate > 4.2:
                findings.append(finding("WARN", "SPEECH_TOO_FAST", f"{rate:.1f} words/s: likely rushed or cut.", ref))
            elif rate < 1.4:
                findings.append(finding("WARN", "SPEECH_TOO_SLOW", f"{rate:.1f} words/s: long pauses or extra audio.",
                                        ref))
        for start, end in silences(wav, noise_db=-45, min_sec=1.2):
            if start < 0.05 and end != float("inf") and end > 1.2:
                findings.append(finding("WARN", "LEADING_SILENCE", f"{end:.1f}s of silence before the voice starts.", ref))
            elif end == float("inf") or end >= dur - 0.05:
                findings.append(finding("WARN", "TRAILING_SILENCE", f"{dur - start:.1f}s of silence after the voice.", ref))
            else:
                findings.append(finding("WARN", "GAP_IN_SPEECH", f"{end - start:.1f}s pause in the middle of the line "
                                                                 f"({start:.1f}s).", ref))
        peak = loudness(wav)["truePeak"]
        if peak > -0.3:
            findings.append(finding("WARN", "CLIPPING", f"Peak {peak:.1f} dBTP: the voice may distort.", ref))
        checked.append(ref)
        _beat(ref)
    return findings, checked


def check_music(req: dict, tmp: Path) -> tuple[list[dict], list[str]]:
    if not req.get("musicUri"):
        return [], []
    path = download(req["musicUri"], tmp / ("music" + Path(req["musicUri"]).suffix))
    findings = []
    dur = _probe(path).get("duration", 0.0)
    target = float(req.get("targetSec") or 0)
    if target and dur < target * 0.9:
        findings.append(finding("WARN", "MUSIC_TOO_SHORT", f"Music is {dur:.0f}s for a {target:.0f}s video: it will "
                                                           "need a loop or extension.", "music"))
    for start, end in silences(path, noise_db=-50, min_sec=2.0):
        if start > 1.0 and (end == float("inf") or end < dur - 1.0):
            findings.append(finding("WARN", "MUSIC_DROPOUT", f"{min(end, dur) - start:.1f}s of silence at {start:.0f}s.",
                                    "music"))
    peak = loudness(path)["truePeak"]
    if peak > -0.1:
        findings.append(finding("WARN", "CLIPPING", f"Music peaks at {peak:.1f} dBTP.", "music"))
    return findings, ["music"]


def check_images(req: dict, tmp: Path) -> tuple[list[dict], list[str]]:
    findings, checked, hashes = [], [], []
    want = ASPECTS.get(req.get("aspectRatio") or "16:9", 16 / 9)
    for n, img in enumerate(req.get("images") or []):
        ref = img["ref"]
        path = download(img["uri"], tmp / f"img{n}{Path(img['uri']).suffix}")
        info = _probe(path)
        w, h = info.get("width"), info.get("height")
        if w and h:
            got = w / h
            if (got > 1) != (want > 1) and abs(got - 1) > 0.05 and abs(want - 1) > 0.05:
                findings.append(finding("FAIL", "WRONG_ORIENTATION", f"Image is {w}x{h} but the video is "
                                                                     f"{req.get('aspectRatio')}.", ref))
            elif abs(got - want) / want > 0.12:
                findings.append(finding("WARN", "ASPECT_MISMATCH", f"Image is {w}x{h} ({got:.2f}) for a "
                                                                   f"{req.get('aspectRatio')} video: it will be cropped.",
                                        ref))
        t = thumb(path, 32, 32)
        if t.mean() < 8:
            findings.append(finding("FAIL", "BLANK_IMAGE", "Image is almost completely black.", ref))
        elif t.std() < 4:
            findings.append(finding("FAIL", "BLANK_IMAGE", "Image is one flat colour (no detail).", ref))
        hashes.append((ref, dhash(path)))
        checked.append(ref)
        _beat(ref)
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            if bin(hashes[i][1] ^ hashes[j][1]).count("1") <= 4:
                findings.append(finding("WARN", "NEAR_DUPLICATE", f"Looks almost the same as {hashes[i][0]}.",
                                        hashes[j][0]))
    return findings, checked


def check_video(req: dict, tmp: Path) -> tuple[list[dict], list[str]]:
    if not req.get("videoUri"):
        return [], []
    path = download(req["videoUri"], tmp / "video.mp4")
    findings = []
    info = _probe(path)
    w, h, dur = info.get("width"), info.get("height"), info.get("duration", 0.0)
    want = ASPECTS.get(req.get("aspectRatio") or "16:9", 16 / 9)
    if w and h and abs(w / h - want) / want > 0.02:
        findings.append(finding("FAIL", "WRONG_FRAME", f"Video is {w}x{h} but the plan is {req.get('aspectRatio')}.",
                                "video"))
    if not info.get("audio"):
        findings.append(finding("FAIL", "NO_AUDIO", "The video has no sound track.", "video"))
    if req.get("isShort") and dur > 60.05:
        findings.append(finding("FAIL", "SHORT_TOO_LONG", f"{dur:.1f}s: YouTube Shorts must be 60s or less.", "video"))
    target = float(req.get("targetSec") or 0)
    if target and not req.get("isShort") and abs(dur - target) / target > 0.15:
        findings.append(finding("WARN", "LENGTH_OFF", f"{dur:.0f}s for a planned {target:.0f}s.", "video"))
    speech_end = req.get("speechEndSec")
    if speech_end is not None and speech_end > dur + 0.05:
        findings.append(finding("FAIL", "VOICE_CUT", f"Narration runs to {speech_end:.1f}s but the video ends at "
                                                     f"{dur:.1f}s: the last words are cut.", "video"))
    for start, end in black_segments(path):
        findings.append(finding("WARN", "BLACK_FRAMES", f"{end - start:.1f}s of black picture at {start:.1f}s.", "video"))
    if info.get("audio"):
        loud = loudness(path)
        if loud["lufs"] == float("-inf") or loud["lufs"] < -24:
            findings.append(finding("WARN", "TOO_QUIET", f"Loudness {loud['lufs']:.1f} LUFS (YouTube plays at about -14).",
                                    "video"))
        elif loud["lufs"] > -9:
            findings.append(finding("WARN", "TOO_LOUD", f"Loudness {loud['lufs']:.1f} LUFS: will be turned down and may "
                                                        "sound squashed.", "video"))
        if loud["truePeak"] > -0.5:
            findings.append(finding("WARN", "CLIPPING", f"Peak {loud['truePeak']:.1f} dBTP.", "video"))
    return findings, ["video"]


def frozen_segments(path: Path, min_sec: float = 1.0) -> list[tuple[float, float]]:
    text = _ffmpeg_log(["-i", str(path), "-an", "-vf", f"freezedetect=n=0.002:d={min_sec}"])
    starts = [float(x) for x in re.findall(r"freeze_start: (-?[\d.]+)", text)]
    ends = [float(x) for x in re.findall(r"freeze_end: (-?[\d.]+)", text)]
    return [(s, ends[i] if i < len(ends) else float("inf")) for i, s in enumerate(starts)]


def brightness_jumps(path: Path, jump: float = 14.0) -> int:
    """How many frame-to-frame changes in average brightness exceed {jump} (0-255): a measure of flicker."""
    text = _ffmpeg_log(["-i", str(path), "-an", "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG"])
    values = [float(v) for v in re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", text)]
    return sum(1 for a, b in zip(values, values[1:]) if abs(b - a) > jump)


def check_clips(req: dict, tmp: Path) -> tuple[list[dict], list[str]]:
    """Moving video per shot (M2b): the clip must move, show a picture all the way through, not flicker, and have the
    right shape. Findings use "shot N" refs so the control plane can redo that shot alone."""
    findings, checked = [], []
    want = ASPECTS.get(req.get("aspectRatio") or "16:9", 16 / 9)
    for n, clip in enumerate(req.get("images") or []):
        ref = clip["ref"]
        path = download(clip["uri"], tmp / f"clip{n}.mp4")
        info = _probe(path)
        w, h, dur = info.get("width"), info.get("height"), info.get("duration", 0.0)
        if w and h and abs(w / h - want) / want > 0.05:
            findings.append(finding("FAIL", "WRONG_FRAME", f"Clip is {w}x{h} but the video is {req.get('aspectRatio')}.", ref))
        if dur < 1.5:
            findings.append(finding("FAIL", "CLIP_TOO_SHORT", f"Clip is only {dur:.1f}s long.", ref))
        frozen = sum(min(e, dur) - s for s, e in frozen_segments(path))
        if dur and frozen >= max(1.0, dur * 0.4):
            findings.append(finding("FAIL", "FROZEN", f"The picture barely moves for {frozen:.1f}s of {dur:.1f}s: "
                                                      "it's a still, not video.", ref))
        black = sum(e - s for s, e in black_segments(path, min_sec=0.3))
        if black >= 0.3:
            findings.append(finding("FAIL", "BLACK_FRAMES", f"{black:.1f}s of black picture.", ref))
        jumps = brightness_jumps(path)
        if jumps >= 3:
            findings.append(finding("WARN" if jumps < 6 else "FAIL", "FLICKER",
                                    f"Brightness jumps {jumps} times: the clip flickers.", ref))
        checked.append(ref)
        _beat(ref)
    return findings, checked


CHECKS = {"NARRATION": [check_narration], "MUSIC": [check_music], "KEYFRAMES": [check_images],
          "ANIMATIC": [check_video], "ASSEMBLY": [check_video], "DRAFT_GEN": [check_clips], "FINAL_GEN": [check_clips]}


@activity.defn(name="inspect_media")
def inspect_media(req: dict) -> dict:
    """req: {videoId, stage, aspectRatio, isShort, targetSec, lines:[{index, text, uri}], musicUri,
             images:[{ref, uri}], videoUri, speechEndSec}
    -> {findings:[{severity, code, message, ref}], checked:[str]}

    Never raises for a bad file: a check that cannot run becomes a WARN finding, so the Inspector can't block a video
    by itself breaking."""
    findings: list[dict] = []
    checked: list[str] = []
    with workdir("inspect") as tmp:
        for check in CHECKS.get(req.get("stage") or "", []):
            try:
                f, c = check(req, Path(tmp))
                findings += f
                checked += c
            except Exception as e:  # noqa: BLE001
                log.warning("Inspector check %s failed: %s", check.__name__, e)
                findings.append(finding("WARN", "CHECK_ERROR", f"{check.__name__} could not run: "
                                                               f"{type(e).__name__}: {str(e)[:200]}"))
    return {"findings": findings, "checked": checked}
