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
