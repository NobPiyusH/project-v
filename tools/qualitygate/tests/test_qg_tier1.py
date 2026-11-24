"""Tier 1 parsers and checks on canned ffmpeg output (no ffmpeg needed)."""

from tools.qualitygate import tier1
from tools.qualitygate.types import Status

EBUR = """
  Integrated loudness:
    I:         -14.6 LUFS
    Threshold: -24.7 LUFS

  True peak:
    Peak:       -1.2 dBFS
"""


def test_loudness_pass_and_fail():
    lufs, peak = tier1.parse_ebur128(EBUR)
    assert (lufs, peak) == (-14.6, -1.2)
    assert tier1.check_loudness(lufs, peak).status is Status.PASS
    assert tier1.check_loudness(-17.5, -3).status is Status.WARN
    assert tier1.check_loudness(-27.8, -3).status is Status.FAIL
    assert tier1.check_loudness(-14.0, 1.5).status is Status.FAIL  # clipping
    assert tier1.check_loudness(None, None).status is Status.FAIL


def test_black_edges_are_fades_but_middle_is_defect():
    log = "[blackdetect @ x] black_start:0 black_end:0.6 black_duration:0.6\n[blackdetect @ x] black_start:5 black_end:5.5 black_duration:0.5\n"
    spans = tier1.parse_blackdetect(log)
    r = tier1.check_black(spans, 10.0)
    assert r.status is Status.WARN and r.where == ((5.0, 5.5),)
    assert tier1.check_black([(0.0, 0.6), (9.4, 10.0)], 10.0).status is Status.PASS
    assert tier1.check_black([(4.0, 5.5)], 10.0).status is Status.FAIL
    assert tier1.check_black([(0.0, 3.0)], 10.0).status is Status.FAIL  # an opening black longer than a fade is a defect


def test_freeze_parse_open_ended():
    err = (
        "lavfi.freezedetect.freeze_start: 3\nlavfi.freezedetect.freeze_duration: 1\nlavfi.freezedetect.freeze_end: 4\n"
        "lavfi.freezedetect.freeze_start: 8.5\n"
    )
    spans = tier1.parse_freezedetect(err, 10.0)
    assert spans == [(3.0, 4.0), (8.5, 10.0)]
    assert tier1.check_freeze(spans).status is Status.WARN  # longest 1.5 s
    assert tier1.check_freeze([(0.0, 2.5)]).status is Status.FAIL
    assert tier1.check_freeze([]).status is Status.PASS


SRT = """1
00:00:00,500 --> 00:00:02,500
Hello there, this reads fine.

2
00:00:02,400 --> 00:00:03,000
Overlaps and is short

3
00:00:09,000 --> 00:00:12,000
Past the end
"""


def by_name(results):
    return {r.name: r for r in results}


def test_captions_checks():
    cues = tier1.parse_srt(SRT)
    assert len(cues) == 3 and cues[0].text.startswith("Hello") and cues[1].start == 2.4
    r = by_name(tier1.check_captions(cues, 10.0))
    assert r["captions_range"].status is Status.FAIL and r["captions_range"].where == ((9.0, 12.0),)
    assert r["captions_overlap"].status is Status.FAIL
    assert r["captions_duration"].status is Status.WARN  # 0.6 s cue
    assert r["captions_reading_speed"].status is Status.FAIL  # 20 chars in 0.6 s


def test_good_captions_pass():
    cues = tier1.parse_srt("1\n00:00:01,000 --> 00:00:03,000\nShort line\n")
    assert all(r.status is Status.PASS for r in tier1.check_captions(cues, 10.0))
