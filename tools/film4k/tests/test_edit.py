"""edit.py and sound.py: pure planning and filter-string tests (no ffmpeg, no pods)."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import edit  # noqa: E402
import sound  # noqa: E402

DUR = 11.354


def film(n=8, seed=0):
    shots = []
    for i in range(n):
        shots += edit.plan_shots(i + 1, DUR, i / (n - 1), shots[-1] if shots else None, seed)
    return shots


def test_cross_fade_windows_are_never_inside_a_shot():
    for sid in range(1, 9):
        for s in edit.plan_shots(sid, DUR, 0.5):
            for a, b in edit.seam_gaps():
                assert s.start + s.dur <= a + 1e-6 or s.start >= b - 1e-6, (sid, s, a, b)


def test_shots_are_within_the_scene_and_not_too_short():
    for s in film():
        assert s.dur >= edit.MIN_SHOT_S - 1e-6
        assert s.start >= 0 and s.start + s.dur <= DUR + 1e-6


def test_scene_has_several_shots_and_pace_gets_faster():
    shots = film()
    per = {}
    for s in shots:
        per.setdefault(s.scene, []).append(s.dur)
    assert all(len(v) >= 3 for v in per.values())
    assert sum(per[8]) / len(per[8]) <= sum(per[1]) / len(per[1])


def test_framing_changes_on_every_cut_and_first_shot_is_wide():
    shots = film()
    assert shots[0].kind == "wide"
    for a, b in zip(shots, shots[1:]):
        assert a.kind != b.kind
    # within one scene the framing change is clearly visible (zoom ratio or sideways shift)
    for a, b in zip(shots, shots[1:]):
        if a.scene == b.scene:
            assert edit._differs(a, b)


def test_zoom_never_shrinks_and_stays_in_range():
    for s in film(seed=3):
        assert 1.0 <= s.z0 <= s.z1 <= 2.3
        assert -1.0 <= s.fx0 <= 1.0 and -1.0 <= s.fy0 <= 1.0
        f = edit.shot_filter(s)
        assert "eval=frame" in f and f.endswith("format=yuv420p")


def test_whip_shots_blur_only_their_first_frames():
    whips = [s for s in film(seed=5) if s.kind == "whip"]
    assert whips
    assert "avgblur" in edit.shot_filter(whips[0]) and "lt(n,4)" in edit.shot_filter(whips[0])
    assert "avgblur" not in edit.shot_filter([s for s in film() if s.kind == "wide"][0])


def test_plan_is_deterministic_and_seed_changes_it():
    assert film(seed=1) == film(seed=1)
    assert film(seed=1) != film(seed=2)


def test_cut_times_match_shot_boundaries():
    shots = film(2)
    cuts = edit.cut_times(shots)
    assert len(cuts) == len(shots) - 1
    assert abs(cuts[0] - shots[0].dur) < 1e-2


def test_sound_graph_has_one_hit_per_cut_at_the_right_time():
    g = sound.build_graph(30.0, [3.25, 6.5, 9.0])[1]
    assert "asplit=3" in g and "adelay=3250|3250" in g and "adelay=9000|9000" in g
    assert g.endswith("[aout]") and "alimiter" in g and "dr0" in g


def test_sound_graph_uses_music_instead_of_the_drone():
    g = sound.build_graph(30.0, [5.0], music_index=1)[1]
    assert "[1:a]" in g and "dr0" not in g
