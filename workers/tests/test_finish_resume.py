"""Upscale/smooth robustness (18 Nov 2026): heartbeats from their own thread, and a retry reuses finished clips."""

import time

from workers.activities import finish as F

JOBS = [{"shotId": f"s{i}", "shotNumber": i, "sourceAssetId": f"asset{i}xxxx"} for i in (1, 2, 3)]


def test_retry_reuses_clips_saved_on_an_earlier_attempt(monkeypatch):
    saved = {F._out_key("vid", "sharp", JOBS[0]), F._out_key("vid", "sharp", JOBS[2])}
    monkeypatch.setattr(F, "exists", lambda key: key in saved)
    monkeypatch.setattr(F, "_probe", lambda key: (120, 24.0))
    reused, todo = F._already_done("vid", "sharp", JOBS)
    assert [r["shotId"] for r in reused] == ["s1", "s3"] and all(r["gpuSeconds"] == 0 for r in reused)
    assert [j["shotId"] for j in todo] == ["s2"], "only the unfinished clip is rendered again"


def test_unreadable_leftover_is_redone(monkeypatch):
    monkeypatch.setattr(F, "exists", lambda key: True)

    def bad(key):
        raise ValueError("truncated file")
    monkeypatch.setattr(F, "_probe", bad)
    reused, todo = F._already_done("vid", "sharp", JOBS[:1])
    assert reused == [] and len(todo) == 1


def test_heartbeat_keeps_beating_while_the_main_loop_is_stuck(monkeypatch):
    beats = []
    monkeypatch.setattr(F, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(F, "_beat", lambda d: beats.append(dict(d)))
    with F._Heartbeat() as hb:
        hb.detail = {"done": 1, "total": 3}
        time.sleep(0.4)  # the main loop blocked on a slow network call
    n = len(beats)
    time.sleep(0.2)
    assert n >= 4 and beats[-1] == {"done": 1, "total": 3}
    assert len(beats) == n, "stops beating once the batch is over"
