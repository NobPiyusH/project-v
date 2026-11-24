"""Tier 2 scoring on canned signalstats output."""

from tools.qualitygate import tier2
from tools.qualitygate.types import Shot, Status


def fake_log(ydif, yavg, fps=10):
    lines = []
    for i, (d, a) in enumerate(zip(ydif, yavg, strict=True)):
        lines.append(f"[Parsed_metadata_3 @ 0x1] frame:{i}    pts:{i}       pts_time:{i / fps}")
        lines.append(f"[Parsed_metadata_3 @ 0x1] lavfi.signalstats.YDIF={d}")
        lines.append(f"[Parsed_metadata_3 @ 0x1] lavfi.signalstats.YAVG={a}")
    return "\n".join(lines)


def results(ydif, yavg, sharp):
    shot = Shot("s", 0, 2.0)
    m = tier2.parse_signalstats(fake_log(ydif, yavg))
    s = tier2.parse_signalstats(fake_log([0] * len(sharp), sharp))
    return {r.name: r for r in tier2.score_shot(m, s, shot)}


def test_parse_pairs_keys_with_frames():
    fr = tier2.parse_signalstats(fake_log([0, 3.5], [100, 101]))
    assert [f.t for f in fr] == [0.0, 0.1] and fr[1].stats == {"YDIF": 3.5, "YAVG": 101.0}


def test_moving_sharp_stable_shot_passes():
    r = results([0] + [4.0] * 19, [120 + i * 0.1 for i in range(20)], [3.0] * 20)
    assert all(v.status is Status.PASS for v in r.values()) and set(r) == {"motion", "sharpness", "flicker", "colour_drift"}


def test_static_soft_flickery_shot_fails():
    r = results([0] + [0.05] * 19, [100, 110] * 10, [0.2] * 20)
    assert r["motion"].status is Status.FAIL
    assert r["sharpness"].status is Status.FAIL
    assert r["flicker"].status is Status.FAIL


def test_colour_drift_flags_a_chroma_excursion():
    def log(u):
        rows = []
        for i, x in enumerate(u):
            rows.append(f"[m @ 0x1] frame:{i}    pts:{i}       pts_time:{i / 10}")
            rows += [f"[m @ 0x1] lavfi.signalstats.{k}={v}" for k, v in (("UAVG", x), ("VAVG", 128), ("YAVG", 100), ("YDIF", 4))]
        return chr(10).join(rows)

    shot = Shot("s", 0, 3.0)
    steady = tier2.parse_signalstats(log([128] * 30))
    bump = tier2.parse_signalstats(log([128] * 10 + [130] * 8 + [128] * 12))

    def drift(fr):
        return {r.name: r for r in tier2.score_shot(fr, fr, shot)}["colour_drift"]

    assert drift(steady).status is Status.PASS and drift(bump).status is Status.WARN


def test_cut_frame_does_not_fake_motion():
    # the shot's first frame carries the big diff to the previous shot; the rest is frozen
    r = results([200.0] + [0.0] * 19, [100] * 20, [3.0] * 20)
    assert r["motion"].status is Status.FAIL


def test_second_difference_ignores_smooth_ramp():
    assert tier2.second_difference_mean([1, 2, 3, 4, 5]) == 0
    assert tier2.second_difference_mean([0, 10, 0, 10]) == 10


def test_shot_without_frames_fails():
    m = tier2.parse_signalstats(fake_log([0, 1], [1, 1]))
    out = tier2.score_shot(m, m, Shot("late", 50, 60))
    assert out[0].status is Status.FAIL
