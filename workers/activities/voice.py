"""Narration: Gemini TTS, one WAV per script line (so every line has an exact duration for the edit).

Quality gate: every generated line is transcribed and compared with the script. TTS models occasionally speak
their style instructions aloud or stop mid-sentence; such takes are regenerated automatically (owner feedback,
16 Nov 2026: "before each voice it's adding 'language English, say only this'").
"""

from __future__ import annotations

import concurrent.futures
import difflib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

from google.genai import types
from temporalio import activity

from workers import groq_whisper
from workers.common import RateLimiter, gemini, log, model_list, pcm_to_wav, put_bytes, run_ffmpeg, usage_dict, with_models

# Gemini TTS allows 10 requests/minute per model on the owner's tier; stay just under it.
TTS_LIMIT = RateLimiter(per_minute=9)
MAX_TAKES = 3


def _wav_seconds(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav)) as w:
        return w.getnframes() / float(w.getframerate())


NUMBER_WORDS = set(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "million", "billion", "trillion", "point", "percent", "and", "a"])


def _words(text: str) -> list[str]:
    """Lower-case words without punctuation and without numbers in any spelling ("23%", "twenty-three percent"),
    because scripts spell numbers out while transcripts write digits."""
    words = re.sub(r"[^\w\s]", " ", text.lower().replace("-", " ")).split()
    return [w for w in words if not any(c.isdigit() for c in w) and w not in NUMBER_WORDS]


def judge(expected: str, heard: str) -> tuple[bool, str]:
    """True if the take says the script line and nothing else.

    - extra words (instructions read aloud) are counted at word level;
    - coverage is measured on letters, so small inflections ("reigns"/"reigned") don't fail a good take."""
    exp, got = _words(expected), _words(heard)
    if not exp:
        return True, "only numbers"
    extra = len(got) - len(exp)
    if extra >= 3:
        return False, f"{extra} extra words spoken"
    a, b = " ".join(exp), " ".join(got)
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    coverage = sum(m.size for m in matcher.get_matching_blocks()) / len(a)
    if coverage < 0.85:
        return False, f"only {coverage:.0%} of the line spoken"
    return True, f"coverage {coverage:.0%}"


def _tts(req: dict, prompt: str) -> tuple[bytes, dict]:
    def call(model: str):
        TTS_LIMIT.wait()
        return gemini().models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=req.get("voiceName") or "Charon")
                    )
                ),
            ),
        )

    _, response = with_models(req["model"], call, what="TTS")
    part = response.candidates[0].content.parts[0]
    data, mime = part.inline_data.data, part.inline_data.mime_type or ""
    wav = data if "wav" in mime else pcm_to_wav(data)  # some TTS models return raw 24 kHz PCM
    return wav, usage_dict(response)


def _whisper(wav: bytes) -> str | None:
    """Literal transcript from Whisper on Groq (free tier). Gemini's transcriber was found to silently drop spoken
    style instructions ("Say in a fast-paced ... voice"), so the quality gate passed bad takes (16 Nov 2026).
    Returns None when no Groq key is set or the call fails, so the Gemini check is used instead."""
    try:
        result = groq_whisper.transcribe(wav)
        return None if result is None else (result.get("text") or "").strip()
    except Exception as e:  # never fail a take on the checker; fall back to Gemini
        log.warning("Whisper check unavailable (%s), using Gemini", type(e).__name__)
        return None


def _transcribe(req: dict, wav: bytes) -> tuple[str, dict]:
    heard = _whisper(wav)
    if heard is not None:
        return heard, {}
    _, response = with_models(req.get("checkModel"), lambda m: gemini().models.generate_content(
        model=m,
        contents=[types.Part.from_bytes(data=wav, mime_type="audio/wav"),
                  "Transcribe this audio exactly, word for word, including any words that are not part of a "
                  "sentence. Output only the transcript."],
    ), what="TTS check", default="gemini-3.6-flash")
    return (response.text or "").strip(), usage_dict(response)


def _speak(req: dict, line: dict) -> dict:
    style = (req.get("style") or "").strip().rstrip(".")
    # Take 1-2: style in a marked director's-notes block, apart from the transcript (the old "Say <style>: <text>"
    # form was read aloud on 4 of 7 lines of the night-sky Short). Last take: the bare text, no style.
    delivery = (line.get("delivery") or "").strip().rstrip(".")
    notes = f"Style: {style}" + (f"\nThis line: {delivery}" if delivery else "")  # the voice follows the story's arc
    styled = (f"### DIRECTOR'S NOTES (do not read aloud)\n{notes}\n\n### TRANSCRIPT (read only this)\n"
              f"{line['text']}") if (style or delivery) else line["text"]
    prompts = [styled] * (MAX_TAKES - 1) + [line["text"]]
    usages, checks = [], []
    wav = b""
    for take, prompt in enumerate(prompts, start=1):
        wav, usage = _tts(req, prompt)
        heard, check_usage = _transcribe(req, wav)
        ok, why = judge(line["text"], heard)
        usages += [usage, check_usage] if check_usage else [usage]
        checks.append({"take": take, "ok": ok, "why": why, "heard": heard[:300]})
        if ok:
            break
        log.warning("Narration line %s take %d rejected: %s | heard: %r", line["index"], take, why, heard[:120])
    uri = put_bytes(f"videos/{req['videoId']}/narration/line-{line['index']:03d}.wav", wav, "audio/wav")
    total = {k: sum(u.get(k, 0) for u in usages) for k in ("promptTokens", "outputTokens", "thoughtTokens", "totalTokens")}
    return {"index": line["index"], "uri": uri, "durationSec": round(_wav_seconds(wav), 3), "usage": total,
            "takes": len(checks), "passed": checks[-1]["ok"], "check": checks[-1]["why"]}


# ------------------------------------------------------------------------------------------------ ElevenLabs
# Owner's account (17 Nov 2026): Free plan, 10,000 characters a month, so credit is spent carefully:
# - one provider per video (never mixed voices): ElevenLabs only if the remaining credit covers every line plus
#   30% for retakes plus a reserve kept for later line redos; otherwise Gemini voices the whole video;
# - no style text is sent (it can't be read aloud), so 2 takes per line are enough; Whisper still checks each one;
# - a full narration that fails part-way on ElevenLabs is re-voiced entirely with Gemini.

ELEVEN_URL = "https://api.elevenlabs.io"
ELEVEN_RETRIES = 4  # tries per line when ElevenLabs answers 429
ELEVEN_TAKES = 2
ELEVEN_HEADROOM = 1.3


def _eleven_config() -> dict:
    return {"key": os.getenv("ELEVENLABS_API_KEY") or "",
            # v4 turbo: same v4 voices at half the credits (0.5 per character); owner: "consume less credits".
            "model": os.getenv("ELEVENLABS_MODEL") or "eleven_v4_turbo",
            "voiceId": os.getenv("ELEVENLABS_VOICE_ID") or "JBFqnCBsd6RMkjVDRZzb",  # George: warm storyteller
            "voiceName": os.getenv("ELEVENLABS_VOICE_NAME") or "George",
            "reserve": int(os.getenv("ELEVENLABS_RESERVE_CHARS") or 1500)}


def _eleven_get(path: str, key: str) -> dict:
    request = urllib.request.Request(ELEVEN_URL + path, headers={"xi-api-key": key, "User-Agent": "projectv"})
    with urllib.request.urlopen(request, timeout=30) as r:
        return json.load(r)


def eleven_remaining(key: str) -> int | None:
    """Characters left this month (a free, read-only call). None when it can't be checked."""
    try:
        s = _eleven_get("/v1/user/subscription", key)
        return int(s["character_limit"]) - int(s["character_count"])
    except Exception as e:  # noqa: BLE001
        log.warning("ElevenLabs credit check failed (%s)", type(e).__name__)
        return None


def eleven_multiplier(key: str, model: str) -> float:
    """Credits per character for a model (a free call); 1.0 when unknown, so the check never under-counts."""
    try:
        for m in _eleven_get("/v1/models", key):
            if m.get("model_id") == model:
                return float((m.get("model_rates") or {}).get("character_cost_multiplier") or 1.0)
    except Exception as e:  # noqa: BLE001
        log.warning("ElevenLabs model check failed (%s)", type(e).__name__)
    return 1.0


def eleven_needed(lines: list[dict], multiplier: float = 1.0) -> int:
    return int(sum(len(l["text"]) for l in lines) * multiplier * ELEVEN_HEADROOM) + 1


def choose_provider(req: dict, remaining: int | None, cfg: dict) -> str:
    """'elevenlabs' or 'gemini' for the whole request. A redo keeps the video's provider ('elevenlabs' asked)."""
    wanted = (req.get("voiceProvider") or "auto").lower()
    if wanted in ("kokoro", "chatterbox"):
        return wanted
    # Owner, 18 Nov 2026: Chatterbox is the channel voice (free, MIT, fine for monetised videos). ElevenLabs' free
    # plan is non-commercial, so it is only used automatically when ELEVENLABS_COMMERCIAL=1 (a paid plan).
    if wanted == "auto" and chatterbox_available(req.get("language")):
        return "chatterbox"
    if wanted == "auto" and os.getenv("ELEVENLABS_COMMERCIAL", "0") != "1":
        return "gemini"
    if wanted == "gemini" or not cfg["key"]:
        if wanted == "elevenlabs":
            raise RuntimeError("This video was voiced by ElevenLabs but no ELEVENLABS_API_KEY is set")
        return "gemini"
    needed = eleven_needed(req["lines"], cfg.get("multiplier", 1.0))
    if wanted == "elevenlabs":
        if remaining is not None and remaining < needed:
            raise RuntimeError(f"ElevenLabs credit is too low to re-voice in the same voice ({remaining} characters "
                               f"left, {needed} needed). Re-voice the whole narration (it will use Gemini) or wait "
                               "for the monthly reset.")
        return "elevenlabs"
    return "elevenlabs" if remaining is not None and remaining - cfg["reserve"] >= needed else "gemini"


def _eleven_tts(cfg: dict, text: str, language: str | None) -> tuple[bytes, int]:
    """One line -> (24 kHz WAV, characters billed)."""
    body = {"text": text, "model_id": cfg["model"],
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "style": 0.3, "use_speaker_boost": True}}
    if language and len(language) == 2:
        body["language_code"] = language
    request = urllib.request.Request(
        f"{ELEVEN_URL}/v1/text-to-speech/{cfg['voiceId']}?output_format=pcm_24000", json.dumps(body).encode(),
        {"xi-api-key": cfg["key"], "Content-Type": "application/json", "User-Agent": "projectv"})
    for attempt in range(1, ELEVEN_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as r:
                pcm = r.read()
                billed = int(r.headers.get("character-cost") or len(text))
            return pcm_to_wav(pcm, rate=24000), billed
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == ELEVEN_RETRIES:
                raise
            wait = min(float(e.headers.get("Retry-After") or 2 ** attempt), 30.0)  # busy, not broken: back off and retry the line
            log.warning("ElevenLabs 429 (too many requests); retry %d/%d in %.0f s", attempt, ELEVEN_RETRIES - 1, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _speak_eleven(req: dict, cfg: dict, line: dict) -> dict:
    checks, billed, wav = [], 0, b""
    for take in range(1, ELEVEN_TAKES + 1):
        wav, chars = _eleven_tts(cfg, line["text"], req.get("language"))
        billed += chars
        heard, _ = _transcribe(req, wav)
        ok, why = judge(line["text"], heard)
        checks.append({"take": take, "ok": ok, "why": why})
        if ok:
            break
        log.warning("ElevenLabs line %s take %d rejected: %s | heard: %r", line["index"], take, why, heard[:120])
    uri = put_bytes(f"videos/{req['videoId']}/narration/line-{line['index']:03d}.wav", wav, "audio/wav")
    return {"index": line["index"], "uri": uri, "durationSec": round(_wav_seconds(wav), 3), "usage": None,
            "takes": len(checks), "passed": checks[-1]["ok"], "check": checks[-1]["why"], "characters": billed}


# ------------------------------------------------------------------------------------------------ Kokoro
# Last-resort voice (18 Nov 2026): Kokoro-82M (Apache-2.0) on the laptop CPU, free and offline, so narration never
# stops a video when ElevenLabs and Gemini are both unavailable. English only; weights pinned on Hugging Face.

KOKORO_REPO = "hexgrad/Kokoro-82M"
KOKORO_REVISION = "f3ff3571791e39611d31c381e3a41a3af07b4987"
_kokoro = None
_kokoro_lock = threading.Lock()


def _kokoro_voice() -> str:
    return os.getenv("KOKORO_VOICE") or "bm_george"  # British male narrator; 'a…' voices are American


def kokoro_available(language: str | None) -> bool:
    if (language or "en")[:2].lower() != "en":
        return False
    try:
        import kokoro  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _kokoro_pipeline():
    global _kokoro
    if _kokoro is None:
        from huggingface_hub import hf_hub_download
        from kokoro import KModel, KPipeline

        get = lambda f: hf_hub_download(KOKORO_REPO, f, revision=KOKORO_REVISION)  # noqa: E731
        model = KModel(repo_id=KOKORO_REPO, config=get("config.json"), model=get("kokoro-v1_0.pth")).eval()
        voice = _kokoro_voice()
        _kokoro = (KPipeline(lang_code=voice[0], repo_id=KOKORO_REPO, model=model), get(f"voices/{voice}.pt"))
    return _kokoro


def _kokoro_tts(text: str) -> bytes:
    import numpy as np

    with _kokoro_lock:  # one model, used one line at a time
        pipe, voice = _kokoro_pipeline()
        chunks = [np.asarray(audio, dtype=np.float32) for _, _, audio in pipe(text, voice=voice) if audio is not None]
    pcm = (np.clip(np.concatenate(chunks), -1, 1) * 32767).astype("<i2").tobytes()
    return pcm_to_wav(pcm, rate=24000)


def _speak_kokoro(req: dict, line: dict) -> dict:
    wav = _kokoro_tts(line["text"])
    heard, _ = _transcribe(req, wav)
    ok, why = judge(line["text"], heard)
    uri = put_bytes(f"videos/{req['videoId']}/narration/line-{line['index']:03d}.wav", wav, "audio/wav")
    return {"index": line["index"], "uri": uri, "durationSec": round(_wav_seconds(wav), 3), "usage": None,
            "takes": 1, "passed": ok, "check": why}


def _kokoro_result(req: dict) -> dict:
    lines = _run(req, lambda line: _speak_kokoro(req, line))
    return {"lines": lines, "model": f"kokoro:{_kokoro_voice()}", "voiceName": f"Kokoro {_kokoro_voice()}",
            "characters": 0}


# ------------------------------------------------------------------------------------------------ Chatterbox
# Main voice (owner, 18 Nov 2026): Resemble AI Chatterbox (MIT, pinned weights, Perth watermark) on the laptop GPU.
# It pins torch 2.6 / numpy < 2, so it lives in its own environment (tools/chatterbox) and runs as a subprocess;
# all lines go in one call so the model loads once. CHATTERBOX_VOICE_REF (a 10-20 s recording) clones a voice
# the owner has the rights to (his own).

CHATTERBOX_DIR = Path(__file__).resolve().parents[2] / "tools" / "chatterbox"
CHATTERBOX_TAKES = 2


def chatterbox_available(language: str | None) -> bool:
    if os.getenv("PROJECTV_CHATTERBOX", "1") == "0" or (language or "en")[:2].lower() != "en":
        return False
    return (CHATTERBOX_DIR / ".venv").exists() and shutil.which("uv") is not None


def _chatterbox_run(lines: list[dict], tmp: Path) -> dict[int, Path]:
    """Voice lines in one subprocess call. Returns {line index: wav path} for the lines that were produced."""
    job = {"lines": [{"text": l["text"], "out": str(tmp / f"cb-{l['index']:03d}.wav")} for l in lines],
           "voiceRef": os.getenv("CHATTERBOX_VOICE_REF") or None,
           "exaggeration": float(os.getenv("CHATTERBOX_EXAGGERATION", "0.5")), "cfgWeight": 0.5}
    job_path = tmp / "job.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    proc = subprocess.run(["uv", "run", "--no-sync", "--project", str(CHATTERBOX_DIR), "python",
                           str(CHATTERBOX_DIR / "speak.py"), str(job_path)],
                          capture_output=True, text=True, timeout=60 * 30)
    done = {l["index"]: tmp / f"cb-{l['index']:03d}.wav" for l in lines if (tmp / f"cb-{l['index']:03d}.wav").exists()}
    if proc.returncode != 0 and not done:
        raise RuntimeError(f"Chatterbox failed: {proc.stderr[-400:]}")
    return done


def _chatterbox_result(req: dict) -> dict:
    """Every line voiced (retakes only for lines the transcript check rejects), then stored like any narration."""
    out: dict[int, dict] = {}
    todo = list(req["lines"])
    with tempfile.TemporaryDirectory(prefix="projectv-chatterbox-") as tmp:
        for take in range(1, CHATTERBOX_TAKES + 1):
            if not todo:
                break
            take_dir = Path(tmp) / f"t{take}"
            take_dir.mkdir()
            wavs = _chatterbox_run(todo, take_dir)
            retry = []
            for line in todo:
                path = wavs.get(line["index"])
                if path is None:
                    retry.append(line)
                    continue
                wav = _to_wav24(path.read_bytes())
                heard, _ = _transcribe(req, wav)
                ok, why = judge(line["text"], heard)
                out[line["index"]] = {"line": line, "wav": wav, "ok": ok, "why": why, "takes": take}
                if not ok:
                    retry.append(line)
                    log.warning("Chatterbox line %s take %d rejected: %s | heard: %r", line["index"], take, why, heard[:120])
            todo = retry
            try:
                activity.heartbeat(len(out))
            except RuntimeError:
                pass
    missing = [l["index"] for l in req["lines"] if l["index"] not in out]
    if missing:
        raise RuntimeError(f"Chatterbox produced no audio for lines {missing}")
    lines = []
    for idx in sorted(out):
        r = out[idx]
        uri = put_bytes(f"videos/{req['videoId']}/narration/line-{idx:03d}.wav", r["wav"], "audio/wav")
        lines.append({"index": idx, "uri": uri, "durationSec": round(_wav_seconds(r["wav"]), 3), "usage": None,
                      "takes": r["takes"], "passed": r["ok"], "check": r["why"]})
    voice = "cloned" if os.getenv("CHATTERBOX_VOICE_REF") else "default"
    return {"lines": lines, "model": f"chatterbox:{voice}", "voiceName": f"Chatterbox {voice}", "characters": 0}


def _to_wav24(data: bytes) -> bytes:
    """Chatterbox writes float WAVs; the edit expects 16-bit PCM like the other voices."""
    with tempfile.TemporaryDirectory() as t:
        src, dst = Path(t, "in.wav"), Path(t, "out.wav")
        src.write_bytes(data)
        run_ffmpeg(["-i", str(src), "-ac", "1", "-ar", "24000", "-sample_fmt", "s16", str(dst)])
        return dst.read_bytes()


ELEVEN_CONCURRENCY = 2  # ElevenLabs' free plan allows 2 concurrent requests: 3 workers got HTTP 429 and the whole video fell back to Gemini (22 Nov 2026)


def _run(req: dict, speak, workers: int = 3) -> list[dict]:
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(speak, line) for line in req["lines"]]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())
            activity.heartbeat(len(results))
    return sorted(results, key=lambda r: r["index"])


@activity.defn(name="synthesize_narration")
def synthesize_narration(req: dict) -> dict:
    """req: {videoId, model, checkModel, voiceName, style, language, voiceProvider (auto|elevenlabs|gemini),
             lines:[{index, text}]}
    -> {lines:[{index, uri, durationSec, usage, takes, passed, check}], model, voiceName, characters}"""
    cfg = _eleven_config()
    remaining = None
    if cfg["key"] and (req.get("voiceProvider") or "auto") != "gemini":
        remaining = eleven_remaining(cfg["key"])
        cfg["multiplier"] = eleven_multiplier(cfg["key"], cfg["model"])
    provider = choose_provider(req, remaining, cfg)
    from workers.routing_log import log_decision
    wanted = (req.get("voiceProvider") or "auto").lower()
    log_decision(req.get("videoId"), "NARRATION", "tts.narration", provider,
                 f"owner asked for {wanted}" if wanted not in ("auto", provider)
                 else "Chatterbox: the channel voice (free, local)" if provider == "chatterbox"
                 else f"{provider}: auto choice (Chatterbox unavailable for this language or turned off)",
                 [{"provider": p} for p in ("chatterbox", "elevenlabs", "gemini", "kokoro")])
    if provider == "chatterbox":
        try:
            return _chatterbox_result(req)
        except Exception as e:  # noqa: BLE001
            if (req.get("voiceProvider") or "auto") == "chatterbox":
                raise  # a redo must keep the video's voice: let the owner decide
            log.warning("Chatterbox failed (%s: %s); voicing the whole video with Gemini", type(e).__name__, e)
            provider = "gemini"
    if provider == "elevenlabs":
        try:
            lines = _run(req, lambda line: _speak_eleven(req, cfg, line), workers=ELEVEN_CONCURRENCY)
            chars = sum(l.pop("characters", 0) for l in lines)
            log.info("ElevenLabs voiced %d lines (%d characters; %s left before)", len(lines), chars, remaining)
            return {"lines": lines, "model": f"elevenlabs:{cfg['model']}", "voiceName": cfg["voiceName"],
                    "characters": chars}
        except Exception as e:  # noqa: BLE001
            if (req.get("voiceProvider") or "auto") == "elevenlabs":
                raise  # a redo must keep the video's voice: let the owner decide
            log.warning("ElevenLabs failed (%s: %s); re-voicing the whole video with Gemini", type(e).__name__, e)
    if provider == "kokoro":
        return _kokoro_result(req)
    try:
        lines = _run(req, lambda line: _speak(req, line))
    except Exception as e:  # noqa: BLE001
        if (req.get("voiceProvider") or "auto") != "auto" or not kokoro_available(req.get("language")):
            raise
        log.warning("Gemini TTS failed (%s: %s); voicing the whole video with Kokoro on the laptop", type(e).__name__, e)
        return _kokoro_result(req)
    return {"lines": lines, "model": model_list(req["model"])[0], "voiceName": req.get("voiceName") or "Charon",
            "characters": 0}
