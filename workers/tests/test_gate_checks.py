import numpy as np

from workers.quality_gate import FAIL, WARN, audio, captions, config, cost, diagram, freeze, report, timing

CFG = config.load()
codes = lambda fs: {f.code for f in fs}  # noqa: E731


# ---- diagram -------------------------------------------------------------------------------------------------
def test_overlapping_labels_and_cut_off_label_are_caught():
    spec = {"elements": [{"type": "text", "id": "a", "x": 0, "y": 0, "size": 26, "text": "Electron gun"},
                         {"type": "text", "id": "b", "x": 0.3, "y": 0.05, "size": 26, "text": "Electron beam"},
                         {"type": "text", "id": "c", "x": 6.9, "y": 3.9, "size": 26, "text": "Far corner label"}]}
    c = codes(diagram.check_spec(spec, "shot 1"))
    assert {"DIAGRAM_TEXT_OVERLAP", "DIAGRAM_LABEL_CUT_OFF"} <= c


def test_label_half_on_a_circle_is_flagged_but_label_inside_a_box_is_not():
    spec = {"elements": [{"type": "circle", "id": "c", "x": 0, "y": 0, "r": 0.5},
                         {"type": "text", "id": "t", "x": 0, "y": 0.45, "size": 26, "text": "Brain Mix"},
                         {"type": "box", "id": "b", "x": 4, "y": 0, "w": 3, "h": 1.0},
                         {"type": "text", "id": "t2", "x": 4, "y": 0, "size": 20, "text": "60 Hz"}]}
    fs = diagram.check_spec(spec, "shot 2")
    assert [f.message for f in fs if "Brain Mix" in f.message] and not [f for f in fs if "60 Hz" in f.message]


def test_shape_past_frame_edge_fails():
    spec = {"elements": [{"type": "rgb_mix", "id": "mix", "x": 6.1, "y": 0, "r": 0.5}]}
    assert "DIAGRAM_SHAPE_CUT_OFF" in codes(diagram.check_spec(spec, "shot 3"))


def test_edge_ink_sees_bright_pixels_on_the_border_only():
    g = np.zeros((90, 160), np.uint8)
    assert diagram.check_frame(g, "s") == []
    g[:, -2:] = 255
    assert codes(diagram.check_frame(g, "s")) == {"DIAGRAM_INK_AT_EDGE"}
    g[:] = 0
    g[40:50, 60:100] = 255   # centre text is fine
    assert diagram.check_frame(g, "s") == []


# ---- audio ---------------------------------------------------------------------------------------------------
def test_parse_ebur128_summary():
    text = "noise\n  Summary:\n\n  Integrated loudness:\n    I:         -14.1 LUFS\n  True peak:\n    Peak:       -0.7 dBFS\n"
    assert audio.parse_ebur128(text) == {"lufs": -14.1, "truePeak": -0.7}


def _tone(freq, sec, amp, sr=audio.SR, seed=0):
    t = np.arange(int(sec * sr)) / sr
    rng = np.random.default_rng(seed)
    return amp * (np.sin(2 * np.pi * freq * t) * (1 + 0.5 * np.sin(2 * np.pi * 3 * t)) + 0.2 * rng.standard_normal(len(t)))


def test_voice_over_music_measures_the_gap_and_the_lag():
    sr = audio.SR
    voice = _tone(220, 4.0, 0.3)
    music = _tone(97, 10.0, 0.3 * 10 ** (-12 / 20), seed=5)   # music about 12 dB under the voice
    stem = audio.place(10 * sr, [(2.0, voice)])
    mix = music.copy()
    mix[int(2.3 * sr): int(2.3 * sr) + len(voice)] += voice    # voice really sits 0.3 s later than planned
    r = audio.voice_over_music(mix, stem, 2.0, 4.0)
    assert abs(r["lag_sec"] - 0.3) < 0.01 and 9 < r["gap_db"] < 15 and r["corr"] > 0.8


def test_loud_music_fails_the_line_and_true_peak_fails_the_mix():
    fs = audio.judge_lines([{"index": 3, "gap_db": 2.0, "corr": 0.9}, {"index": 4, "gap_db": 7.0, "corr": 0.9}], CFG)
    assert [(f.code, f.severity) for f in fs] == [("VOICE_TOO_QUIET_VS_MUSIC", FAIL), ("VOICE_LOW_VS_MUSIC", WARN)]
    assert codes(audio.judge_mix({"lufs": -14.1, "truePeak": -0.4}, CFG)) == {"MIX_TRUE_PEAK"}
    assert audio.judge_mix({"lufs": -14.2, "truePeak": -1.4}, CFG) == []
    assert "MIX_LOUDNESS" in codes(audio.judge_mix({"lufs": -9.0, "truePeak": -3}, CFG))


# ---- captions ------------------------------------------------------------------------------------------------
SRT = "1\n00:00:01,000 --> 00:00:02,500\nLook at your television.\n\n2\n00:00:02,500 --> 00:00:03,400\nBlack.\n\n3\n00:00:03,300 --> 00:00:05,000\nBut wait, it moves.\n"


def test_srt_parse_and_one_word_and_overlap():
    cues = captions.parse_srt(SRT)
    assert len(cues) == 3 and cues[1]["start"] == 2.5
    fs = captions.check_cards(cues, 2)
    assert codes(fs) == {"CAPTION_ONE_WORD", "CAPTION_OVERLAP"}
    assert [f.severity for f in fs if f.code == "CAPTION_ONE_WORD"] == [FAIL]


def test_caption_clash_with_low_diagram_text():
    spec = {"elements": [{"type": "text", "id": "cap", "x": 0, "y": -3.6, "size": 14, "text": "Shadow mask aligns beams"}]}
    shots = [{"n": 5, "lane": "diagram", "startSec": 0.0, "durationSec": 5.0}]
    fs = captions.check_clash(captions.parse_srt(SRT), shots, {5: spec})
    assert codes(fs) == {"CAPTION_NEAR_DIAGRAM_TEXT"}


# ---- timing --------------------------------------------------------------------------------------------------
def test_timing_flags_drift_but_not_the_opening_shot():
    shots = [{"n": 1, "startSec": 0.0, "lineIndex": 0}, {"n": 2, "startSec": 14.74, "lineIndex": 1}, {"n": 3, "startSec": 30.0, "lineIndex": 2}]
    fs = timing.check(shots, {0: 0.4, 1: 15.14, 2: 31.0})
    assert [(f.ref, f.code) for f in fs] == [("line 2", "PICTURE_SLIGHTLY_OFF_VOICE"), ("line 3", "PICTURE_OFF_VOICE")]
    assert timing.check(shots[:2], {0: 0.4, 1: 14.9}) == []


# ---- freeze --------------------------------------------------------------------------------------------------
def test_freeze_and_black_parsers_and_thresholds():
    log = "black_start:0 black_end:0.8 black_duration:0.8\nblack_start:50 black_end:53 black_duration:3\nfreeze_start: 20\nfreeze_end: 27\n"
    black, frz = freeze.parse_black(log), freeze.parse_freeze(log)
    assert black == [(0.0, 0.8), (50.0, 53.0)] and frz == [(20.0, 27.0)]
    shots = [{"n": 9, "lane": "diagram", "startSec": 19.5, "durationSec": 8.0}]
    fs = freeze.judge(black, frz, 180.0, shots, CFG)
    assert codes(fs) == {"BLACK_FRAMES", "FROZEN_PICTURE"} and all(f.severity == FAIL for f in fs)   # the opening fade is fine


# ---- cost + report -------------------------------------------------------------------------------------------
def test_preflight_message_and_cap_failure():
    msg, fs = cost.preflight("vision checks", 0.05, 0.13, CFG)
    assert "$0.050" in msg and codes(fs) == {"COST_OK"}
    _, fs = cost.preflight("vision checks", 0.4, 0.6, CFG)
    assert "OVER_RUN_CAP" in codes(fs)
    _, fs = cost.preflight("x", 0.01, 0.02, CFG, spent_day_usd=1.99)
    assert "OVER_DAY_CAP" in codes(fs)


def test_report_overall_fails_when_any_check_fails_and_skipped_is_not_a_pass():
    from workers.quality_gate import CheckResult, Finding
    ok, bad, skipped = CheckResult("a"), CheckResult("b", [Finding("b", FAIL, "X", "boom", "shot 1")]), CheckResult("c", skipped="no key")
    assert report.overall([ok, skipped]) == "PASS" and report.overall([ok, bad]) == "FAIL"
    md = report.to_markdown({"title": "T", "cutPath": "c.mp4"}, [ok, bad, skipped], {"run_usd": 0.01, "run_inr": 0.9, "calls": 2})
    assert "SKIPPED (no key)" in md and "boom" in md
