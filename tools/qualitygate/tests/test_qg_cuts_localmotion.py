"""Cut detection (pure) and independent-motion score (numpy synthetic frames, no ffmpeg)."""

import pytest

from tools.qualitygate import cuts, localmotion
from tools.qualitygate.types import Shot, Status


def test_parse_cuts_and_shots():
    log = (
        "[Parsed_metadata_2 @ 0x1] frame:0    pts:48      pts_time:3\n[Parsed_metadata_2 @ 0x1] lavfi.scene_score=0.91\n"
        "[Parsed_metadata_2 @ 0x1] frame:1    pts:49      pts_time:3.2\n[Parsed_metadata_2 @ 0x1] lavfi.scene_score=0.5\n"
        "[Parsed_metadata_2 @ 0x1] frame:2    pts:90      pts_time:9.9\n"
    )
    found = cuts.parse_cuts(log)
    assert found == [3.0, 3.2, 9.9]
    shots = cuts.shots_from_cuts(found, 10.0)
    # 3.2 is too close to the cut at 3.0, 9.9 too close to the end
    assert [(s.start, s.end) for s in shots] == [(0.0, 3.0), (3.0, 10.0)]
    assert [s.id for s in shots] == ["shot01", "shot02"]
    assert cuts.shots_from_cuts([], 4.0)[0].end == 4.0


np = pytest.importorskip("numpy")


def texture(seed=0, h=96, w=64):
    rng = np.random.default_rng(seed)
    from_noise = rng.random((h + 40, w + 40)).astype(np.float32)
    k = np.ones(9, np.float32) / 9  # blur so phase correlation has smooth structure to lock onto
    sm = np.apply_along_axis(lambda r: np.convolve(r, k, "same"), 1, from_noise)
    sm = np.apply_along_axis(lambda c: np.convolve(c, k, "same"), 0, sm)
    return (sm - sm.min()) / (sm.max() - sm.min()) * 255


def test_slid_still_leaves_little_residual_but_new_content_leaves_a_lot():
    base = texture(1)
    a, b = base[10:106, 10:74], base[10:106, 13:77]  # whole image slides 3 px
    slid = localmotion.residual_after_camera(a, b)
    changed = localmotion.residual_after_camera(a, texture(2)[10:106, 10:74])
    assert slid < 0.4 * changed
    assert localmotion.residual_after_camera(a, a) < 0.5


def test_independent_motion_thresholds_and_short_shot():
    shot = Shot("s", 0, 2)
    assert localmotion.check_independent_motion(0.8, shot).status is Status.FAIL
    assert localmotion.check_independent_motion(3.0, shot).status is Status.WARN
    assert localmotion.check_independent_motion(9.0, shot).status is Status.PASS
    assert localmotion.check_independent_motion(None, shot).status is Status.SKIP


def test_shot_independent_motion_uses_only_pairs_inside_the_shot():
    frames = np.stack([texture(1)[10:106, 10:74]] * 8 + [texture(2)[10:106, 10:74]] * 8)  # a cut at frame 8 (2 s at 4 fps)
    inside_first = localmotion.shot_independent_motion(frames, 4.0, Shot("a", 0, 2.0))
    assert inside_first is not None and inside_first < 0.5
    assert localmotion.shot_independent_motion(frames, 4.0, Shot("tiny", 0, 0.5)) is None
