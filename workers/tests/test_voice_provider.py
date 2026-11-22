"""ElevenLabs credit rules: one provider per video, a reserve kept for redos, redos keep the video's voice."""

import pytest

from workers.activities import voice as V

CFG = {"key": "k", "model": "eleven_v4_turbo", "voiceId": "v", "voiceName": "George", "reserve": 1500,
       "multiplier": 0.5}
LINES = [{"index": i, "text": "x" * 300} for i in range(3)]  # 900 characters -> 900 * 0.5 * 1.3 = 585 credits


@pytest.fixture(autouse=True)
def _paid_elevenlabs_no_chatterbox(monkeypatch):
    """The ElevenLabs rules below apply to a paid (commercial) plan; Chatterbox is checked separately."""
    monkeypatch.setenv("ELEVENLABS_COMMERCIAL", "1")
    monkeypatch.setattr(V, "chatterbox_available", lambda language: False)


def test_chatterbox_is_the_main_voice_and_free_elevenlabs_is_never_used_automatically(monkeypatch):
    monkeypatch.setattr(V, "chatterbox_available", lambda language: True)
    assert V.choose_provider({"lines": LINES, "language": "en"}, 9000, CFG) == "chatterbox"
    monkeypatch.setattr(V, "chatterbox_available", lambda language: False)
    monkeypatch.setenv("ELEVENLABS_COMMERCIAL", "0")
    assert V.choose_provider({"lines": LINES}, 9000, CFG) == "gemini", "free ElevenLabs plan is non-commercial"
    assert V.choose_provider({"lines": LINES, "voiceProvider": "chatterbox"}, None, CFG) == "chatterbox"


def test_when_chatterbox_fails_gemini_voices_the_whole_video(monkeypatch):
    monkeypatch.setattr(V, "chatterbox_available", lambda language: True)
    monkeypatch.setattr(V, "_chatterbox_result", lambda req: (_ for _ in ()).throw(RuntimeError("CUDA out of memory")))
    monkeypatch.setattr(V, "_speak", lambda req, line: {"index": line["index"], "uri": "u", "durationSec": 1.0,
                                                        "usage": None, "takes": 1, "passed": True, "check": "ok"})
    monkeypatch.setattr(V.activity, "heartbeat", lambda *a: None)
    out = V.synthesize_narration({"videoId": "v", "model": "gemini-tts", "language": "en", "lines": LINES})
    assert out["model"] == "gemini-tts" and len(out["lines"]) == 3


def test_elevenlabs_only_when_the_whole_video_fits_with_headroom_and_reserve():
    assert V.eleven_needed(LINES, 0.5) == 586
    assert V.choose_provider({"lines": LINES}, 2100, CFG) == "elevenlabs"   # 2100 - 1500 >= 586
    assert V.choose_provider({"lines": LINES}, 2000, CFG) == "gemini"       # would eat into the redo reserve
    assert V.choose_provider({"lines": LINES}, None, CFG) == "gemini"       # credit unknown: don't risk it


def test_no_key_or_gemini_asked_means_gemini():
    assert V.choose_provider({"lines": LINES}, 9000, {**CFG, "key": ""}) == "gemini"
    assert V.choose_provider({"lines": LINES, "voiceProvider": "gemini"}, 9000, CFG) == "gemini"


def test_a_redo_keeps_the_videos_voice_and_says_so_when_credit_is_short():
    redo = {"lines": LINES[:1], "voiceProvider": "elevenlabs"}
    assert V.choose_provider(redo, 300, CFG) == "elevenlabs"  # the reserve exists for exactly this
    with pytest.raises(RuntimeError, match="too low to re-voice in the same voice"):
        V.choose_provider(redo, 100, CFG)


def test_a_kokoro_video_keeps_kokoro_for_redos():
    assert V.choose_provider({"lines": LINES, "voiceProvider": "kokoro"}, 9000, CFG) == "kokoro"


def test_when_gemini_tts_fails_kokoro_voices_the_whole_video(monkeypatch):
    calls = []
    monkeypatch.setattr(V, "_eleven_config", lambda: {**CFG, "key": ""})
    monkeypatch.setattr(V, "_speak", lambda req, line: (_ for _ in ()).throw(RuntimeError("503 overloaded")))
    monkeypatch.setattr(V, "kokoro_available", lambda language: True)
    monkeypatch.setattr(V, "_kokoro_result", lambda req: calls.append(len(req["lines"])) or {"model": "kokoro:bm_george"})
    monkeypatch.setattr(V.activity, "heartbeat", lambda *a: None)
    out = V.synthesize_narration({"videoId": "v", "model": "tts", "language": "en", "lines": LINES})
    assert out["model"] == "kokoro:bm_george" and calls == [3]


def test_line_delivery_goes_into_the_directors_notes_not_the_transcript(monkeypatch):
    """Owner, 18 Nov 2026: the voice follows the story line by line (per-line delivery from the writer)."""
    from workers.activities import voice as V
    seen = []

    def fake_tts(req, prompt):
        seen.append(prompt)
        return b"RIFF", {}
    monkeypatch.setattr(V, "_tts", fake_tts)
    monkeypatch.setattr(V, "_transcribe", lambda req, wav: ("Protect the dark.", None))
    monkeypatch.setattr(V, "put_bytes", lambda *a: "s3://b/x.wav")
    monkeypatch.setattr(V, "_wav_seconds", lambda wav: 2.0)
    V._speak({"videoId": "v", "style": "in a warm voice"}, {"index": 0, "text": "Protect the dark.", "delivery": "slow, tender"})
    first = seen[0]
    assert "This line: slow, tender" in first.split("### TRANSCRIPT")[0]
    assert first.split("### TRANSCRIPT (read only this)\n")[1] == "Protect the dark."


def test_elevenlabs_429_is_retried_and_uses_two_workers(monkeypatch):
    import io
    import urllib.error

    calls = {"n": 0}

    class _Resp:
        headers = {"character-cost": "5"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"\x00\x00" * 100

    def fake_urlopen(request, timeout=0):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError("u", 429, "Too Many Requests", {"Retry-After": "0"}, io.BytesIO(b""))
        return _Resp()

    monkeypatch.setattr(V.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(V.time, "sleep", lambda s: None)
    wav, billed = V._eleven_tts({"key": "k", "voiceId": "v", "model": "m"}, "hello", None)
    assert calls["n"] == 3 and billed == 5 and wav
    assert V.ELEVEN_CONCURRENCY == 2
