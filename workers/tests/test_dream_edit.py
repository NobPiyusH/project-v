"""Dream Short editing rules: bar lines, the music's rise, hook-first order with the climax mid-way, energy speed,
J-cut windows and ambience choice."""

import pytest

from workers.dream import edit
from workers.dream import finish as dream


def test_bar_lines_start_on_the_strongest_beat_phase():
    beats = [0.5 * k for k in range(12)]
    strength = [1, 1, 5, 1] * 3  # the third beat of every four hits hardest
    assert edit.bar_lines(beats, strength) == [1.0, 3.0, 5.0]


def test_biggest_rise_is_where_the_energy_climbs_most():
    energy = [(0.0, 0.2), (1.0, 0.3), (2.0, 0.3), (3.0, 0.9), (4.0, 0.95), (5.0, 0.5)]
    assert edit.biggest_rise(energy) == 2.0


def test_hook_opens_and_the_climax_lands_mid_way_near_the_rise():
    scores = [10, 30, 50, 20, 40]  # shot 3 is most striking (hook), shot 5 next (climax)
    durations = [5.0] * 5
    order = edit.order_shots(scores, durations, target=12.0)
    assert order[0] == 2
    slot = order.index(4)
    mid = slot * 5.0 + 2.5
    assert 0.38 * 25 <= mid <= 0.68 * 25
    assert sorted(order) == [0, 1, 2, 3, 4]


def test_a_rise_too_early_still_keeps_the_climax_inside_the_window():
    order = edit.order_shots([1, 9, 5, 2], [5.0] * 4, target=0.5)
    mid = order.index(2) * 5.0 + 2.5
    assert 0.38 * 20 <= mid <= 0.68 * 20


def test_speed_follows_the_music_energy():
    energy = [(t, 0.0) for t in range(5)] + [(t, 1.0) for t in range(5, 10)]
    quiet, loud = edit.speeds([0, 1], [5.0, 5.0], 0.7, energy)
    assert quiet < 0.7 < loud
    assert loud == pytest.approx(0.7 * (1 + edit.SPEED_SWING))


def test_each_ambience_starts_0_7_s_before_its_cut():
    assert edit.j_cut_windows([5.0, 5.0, 5.0]) == [(0.0, 5.0), (4.3, 10.0), (9.3, 15.0)]


def test_ambience_matches_the_scene():
    assert edit.ambience_query("A red phone box on a wet sandbar at sunset") == "light rain ambience"
    assert edit.ambience_query("A vending machine on a grassy cliff over the ocean") == "calm ocean waves ambience"
    assert edit.ambience_query("An arcade cabinet by a wheat field") == "meadow wind birds ambience"
    assert edit.ambience_query(None) == "quiet outdoor ambience"


def test_ambience_graph_only_places_shots_that_have_a_sound():
    parts, labels = edit.ambience_graph(4, [(0.0, 5.0), (4.3, 10.0)], [False, True])
    assert labels == ["[amb1]"] and "[4:a]" in parts and "adelay=4300|4300" in parts and "afade=t=in:d=0.7" in parts


def test_cuts_prefer_bar_lines_and_never_go_under_the_minimum():
    beats = [0.5 * k for k in range(40)]
    bars = [0.0, 4.6, 9.2, 13.8]
    out = dream.beat_synced([5.1, 5.1, 5.1], 0, beats, bars=bars, min_len=3.4)
    assert out[0] == 4.6  # the bar line at 4.6 s, not the later beat at 5.0
    assert min(out) >= 3.4


def test_the_climax_cut_lands_nearest_the_rise():
    beats = [0.5 * k for k in range(40)]
    out = dream.beat_synced([5.1, 5.1], 0, beats, prefer={0: 4.6})
    assert out[0] == 4.5


def test_the_fade_in_is_not_the_rise():
    energy = [(0.0, 0.0), (1.0, 0.8), (2.0, 0.5), (3.0, 0.5), (4.0, 0.9)]
    assert edit.biggest_rise(energy) == 2.0
