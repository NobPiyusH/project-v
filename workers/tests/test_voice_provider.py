"""ElevenLabs credit rules: one provider per video, a reserve kept for redos, redos keep the video's voice."""

import pytest

from workers.activities import voice as V

CFG = {"key": "k", "model": "eleven_v4_turbo", "voiceId": "v", "voiceName": "George", "reserve": 1500,
       "multiplier": 0.5}
LINES = [{"index": i, "text": "x" * 300} for i in range(3)]  # 900 characters -> 900 * 0.5 * 1.3 = 585 credits


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
