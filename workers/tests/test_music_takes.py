"""ACE-Step take picking: no clipping, best match to the shot energy plan."""

import numpy as np
import pytest

from workers.activities import music as M

SR = 8000


def _tone(env: np.ndarray) -> np.ndarray:
    """A 220 Hz tone whose loudness follows env (one value per second)."""
    t = np.arange(len(env) * SR) / SR
    amp = np.repeat(env, SR)
    return np.stack([0.5 * amp * np.sin(2 * np.pi * 220 * t)] * 2, axis=1)


def test_clipping_is_a_flat_top_not_a_single_peak():
    x = np.zeros((1000, 2))
    x[10, 0] = 1.0                      # one full-scale sample: a peak, not clipping
    assert not M.clipped(x)
    x[500:504, 1] = -1.0                # four in a row: clipped
    assert M.clipped(x)


def test_clipping_survives_rescaling_below_full_scale():
    x = np.zeros((1000, 2))
    x[500:504, 0] = 1.0
    assert M.clipped(x * 0.8913)        # ACE-Step's -1 dBFS rescale keeps the flat top
    assert not M.clipped(np.zeros((1000, 2)))


def test_energy_match_prefers_music_that_follows_the_plan():
    plan = [0.2] * 5 + [0.9] * 5 + [0.2] * 5
    follows = _tone(np.array([0.2] * 5 + [1.0] * 5 + [0.2] * 5))
    opposite = _tone(np.array([1.0] * 5 + [0.2] * 5 + [1.0] * 5))
    assert M.energy_match(follows, SR, plan) > 0.8
    assert M.energy_match(opposite, SR, plan) < -0.5
    assert M.energy_match(follows, SR, []) == 0.0


def test_pick_take_skips_clipped_takes():
    takes = [{"seed": 1, "clipped": True, "energyMatch": 0.9},
             {"seed": 2, "clipped": False, "energyMatch": 0.4},
             {"seed": 3, "clipped": False, "energyMatch": 0.6}]
    assert M.pick_take(takes)["seed"] == 3
    with pytest.raises(RuntimeError):
        M.pick_take([{"seed": 1, "clipped": True, "energyMatch": 0.9}])
