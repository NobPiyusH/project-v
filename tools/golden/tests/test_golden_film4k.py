"""Golden: film editor shot plans, AVOID/LIMIT shot rules, camera plan, loudnorm command builders. Pure, no ffmpeg."""

import sys
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "film4k"))
sys.path.insert(0, str(HERE / "golden"))
import camera  # noqa: E402
import edit  # noqa: E402
import golden  # noqa: E402
import sound  # noqa: E402

DUR = 11.354


def _film(n=8, seed=0):
    shots = []
    for i in range(n):
        shots += edit.plan_shots(i + 1, DUR, i / (n - 1), shots[-1] if shots else None, seed)
    return shots


def test_golden_edit_plan_seed0(request):
    shots = _film(8, 0)
    golden.check("edit_plan_8_scenes_seed0", {"shots": [asdict(s) for s in shots], "cuts": edit.cut_times(shots)}, request.config)


def test_golden_edit_plan_seed1_differs_from_seed0(request):
    shots = _film(8, 1)
    assert [asdict(s) for s in shots] != [asdict(s) for s in _film(8, 0)]
    golden.check("edit_plan_8_scenes_seed1", {"shots": [asdict(s) for s in shots]}, request.config)


def test_golden_segments_avoid_and_limit(request):
    out = {
        "avoid": {str(k): v for k, v in edit.AVOID.items()},
        "limit": {str(k): v for k, v in edit.LIMIT.items()},
        "seam_gaps": edit.seam_gaps(),
        "segments": {str(sid): edit.segments(DUR, scene=sid) for sid in (1, 2, 8)},
    }
    golden.check("edit_segments_avoid_limit", out, request.config)


def test_avoid_and_limit_are_obeyed_in_the_plan():
    """The rules themselves (not just the numbers): no shot touches an AVOID window, none runs past a LIMIT."""
    for s in edit.plan_shots(1, DUR, 0.0):
        for a, b in edit.AVOID[1]:
            assert s.start + s.dur <= a + 1e-6 or s.start >= b - 1e-6
    for s in edit.plan_shots(8, DUR, 1.0):
        assert s.start + s.dur <= edit.LIMIT[8] + 1e-6


def test_golden_camera_plan_scenes_1_to_15(request):
    plan = {str(i): {"move": camera.move(i), "filter": camera.camera_filter(i, 1080, 1920)} for i in range(1, 16)}
    plan["join"] = camera.join_filter(1080, 1920)
    plan["rife_target"] = camera.rife_target(camera.FRAMES)
    golden.check("camera_plan_scenes_1_15", plan, request.config, rel=0.0, abs_=0.0)


def test_camera_plan_is_deterministic():
    assert [camera.camera_filter(i) for i in range(1, 16)] == [camera.camera_filter(i) for i in range(1, 16)]


def test_golden_loudnorm_command_builders(request):
    m = {"i": -21.2, "tp": -1.4, "lra": 4.3, "thresh": -31.5, "offset": 0.2}
    out = {
        "measure": sound.loudnorm_measure_filter(),
        "apply": sound.loudnorm_apply_filter(m),
        "gain_limit_12db": sound.gain_limit_filter(12.0),
        "linear_feasible_peaky": sound.linear_feasible(m),
        "linear_feasible_quiet_peak": sound.linear_feasible({**m, "tp": -12.0}),
    }
    golden.check("loudnorm_builders", out, request.config, rel=0.0, abs_=0.0)
