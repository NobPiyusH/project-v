"""Narration: Gemini TTS, one WAV per script line (so every line has an exact duration for the edit).

Quality gate: every generated line is transcribed and compared with the script. TTS models occasionally speak
their style instructions aloud or stop mid-sentence; such takes are regenerated automatically (owner feedback,
16 Nov 2026: "before each voice it's adding 'language English, say only this'").
"""

from __future__ import annotations

import concurrent.futures
import difflib
import io
import re
import wave

from google.genai import types
from temporalio import activity

from workers.common import RateLimiter, gemini, log, model_list, pcm_to_wav, put_bytes, usage_dict, with_models

# Gemini TTS allows 10 requests/minute per model on the owner's tier; stay just under it.
TTS_LIMIT = RateLimiter(per_minute=9)
MAX_TAKES = 3


def _wav_seconds(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav)) as w:
        return w.getnframes() / float(w.getframerate())


NUMBER_WORDS = set("""zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen
sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million
billion trillion point percent and a""".split())


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


def _transcribe(req: dict, wav: bytes) -> tuple[str, dict]:
    _, response = with_models(req.get("checkModel"), lambda m: gemini().models.generate_content(
        model=m,
        contents=[types.Part.from_bytes(data=wav, mime_type="audio/wav"),
                  "Transcribe this audio exactly, word for word, including any words that are not part of a "
                  "sentence. Output only the transcript."],
    ), what="TTS check", default="gemini-3.6-flash")
    return (response.text or "").strip(), usage_dict(response)


def _speak(req: dict, line: dict) -> dict:
    style = (req.get("style") or "").strip().rstrip(".")
    # Take 1-2: short, officially supported "Say in <style>: <text>" form. Last take: the bare text, no style.
    prompts = [f"Say {style}: {line['text']}" if style else line["text"]] * (MAX_TAKES - 1) + [line["text"]]
    usages, checks = [], []
    wav = b""
    for take, prompt in enumerate(prompts, start=1):
        wav, usage = _tts(req, prompt)
        heard, check_usage = _transcribe(req, wav)
        ok, why = judge(line["text"], heard)
        usages += [usage, check_usage]
        checks.append({"take": take, "ok": ok, "why": why, "heard": heard[:300]})
        if ok:
            break
        log.warning("Narration line %s take %d rejected: %s | heard: %r", line["index"], take, why, heard[:120])
    uri = put_bytes(f"videos/{req['videoId']}/narration/line-{line['index']:03d}.wav", wav, "audio/wav")
    total = {k: sum(u.get(k, 0) for u in usages) for k in ("promptTokens", "outputTokens", "thoughtTokens", "totalTokens")}
    return {"index": line["index"], "uri": uri, "durationSec": round(_wav_seconds(wav), 3), "usage": total,
            "takes": len(checks), "passed": checks[-1]["ok"], "check": checks[-1]["why"]}


@activity.defn(name="synthesize_narration")
def synthesize_narration(req: dict) -> dict:
    """req: {videoId, model, checkModel, voiceName, style, language, lines:[{index, text}]}
    -> {lines:[{index, uri, durationSec, usage, takes, passed, check}], model, voiceName}"""
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_speak, req, line) for line in req["lines"]]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())
            activity.heartbeat(len(results))
    results.sort(key=lambda r: r["index"])
    return {"lines": results, "model": model_list(req["model"])[0], "voiceName": req.get("voiceName") or "Charon"}
