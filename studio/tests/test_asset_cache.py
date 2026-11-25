"""Asset cache keyed by content (studio/assets/cache.py), on the scratch Postgres with fakes: no provider, no pod, no spend."""

import pytest

from studio.assets.asset_store import AssetStore, NewAsset
from studio.assets.cache import AssetCache, content_key
from studio.ledger import CostLedger
from studio.media.types import KeyframeOutput, KeyframeResult, NarrationClip, NarrationResult, Usage
from studio.production.records import ImagePolicy
from studio.production.shot_stage import ShotStage
from studio.production.stages_m2a import VoiceStage
from studio.tests.pworld import make_deps, scalar, seed_video
from studio.tests.test_shot_stage import seeded_shots
from studio.tests.test_stages_m2a import line, script_of


@pytest.fixture
def world(scratch_db):
    scratch_db.execute("DELETE FROM cost_entry")
    video = seed_video(scratch_db, duration=30)
    cache = AssetCache(scratch_db, AssetStore(scratch_db), CostLedger(scratch_db))
    return scratch_db, video, cache


def base(**kw):
    d = dict(kind="video", provider="modal_wan", model="wan", prompt="a moon", negative="blur", seed=7, width=720, height=1280, frames=81, fps=16.0,
             camera="slow_push", inputs=["a" * 64])
    d.update(kw)
    return d


def test_key_is_stable_and_every_field_changes_it():
    k = content_key(**base())
    assert k == content_key(**base()) and len(k) == 64
    for change in ({"seed": 8}, {"prompt": "a sun"}, {"negative": None}, {"width": 480}, {"frames": 33}, {"fps": 24.0}, {"camera": None}, {"model": "w2"},
                   {"provider": "pod"}, {"inputs": ["b" * 64]}, {"revision": "2"}):
        assert content_key(**base(**change)) != k, change


def new_asset(db, video, uri="s3://b/x.png"):
    return AssetStore(db).create(NewAsset(video, "image", uri, "image/png", 768, 1344, None, "modal_zimage", "z", "p", 7, None, True))


def test_same_key_hits_with_provenance_and_a_zero_rupee_entry(world):
    db, video, cache = world
    src = new_asset(db, video)
    key = content_key(**base(kind="image"))
    assert cache.remember(src, key) is True
    new_id = cache.reuse_if_cached(video, key, "image", what="keyframe")
    assert new_id is not None and new_id != src
    assert AssetStore(db).uri_of(new_id) == "s3://b/x.png"
    row = db.rows("SELECT p.reused_from_asset_id AS src FROM provenance p WHERE p.asset_id = %(a)s", {"a": new_id})[0]
    assert row["src"] == src
    cost = db.rows("SELECT amount_inr, unit_type, note FROM cost_entry WHERE video_id = %(v)s", {"v": video})
    assert len(cost) == 1 and float(cost[0]["amount_inr"]) == 0 and cost[0]["unit_type"] == "cache_hit" and f"reused from asset {src}" in cost[0]["note"]


def test_different_seed_misses_and_a_failed_asset_is_never_reused(world):
    db, video, cache = world
    cache.remember(new_asset(db, video), content_key(**base(kind="image", seed=1)))
    assert cache.reuse_if_cached(video, content_key(**base(kind="image", seed=2)), "image") is None
    bad = new_asset(db, video, "s3://b/bad.png")
    k = content_key(**base(kind="image", seed=3))
    assert cache.remember(bad, k, passed=False) is False
    assert cache.reuse_if_cached(video, k, "image") is None
    cache.remember(bad, k)
    cache.invalidate(bad)  # a later check turned it down
    assert cache.reuse_if_cached(video, k, "image") is None


def test_bypass_and_per_video_opt_out(world):
    db, video, cache = world
    k = content_key(**base(kind="image"))
    cache.remember(new_asset(db, video), k)
    assert cache.reuse_if_cached(video, k, "image", bypass=True) is None  # an explicit Redo with a new seed
    cache.set_enabled(video, False)
    assert cache.reuse_if_cached(video, k, "image") is None  # no_cache
    cache.set_enabled(video, True)
    assert cache.reuse_if_cached(video, k, "image") is not None


def test_keyframes_are_remembered_on_save_and_reused_by_the_next_request_at_rs_0(world):
    db, video, cache = world
    ids = seeded_shots(db, video, ["standard", "standard"])
    st = ShotStage(make_deps(db, cache=cache))
    req = st.keyframe_request(video, None)
    st.save_keyframes(video, req, KeyframeResult([KeyframeOutput(j.shot_id, f"s3://b/{i}.png", "modal_zimage", "z", j.seed, 1.0, None, None, None)
                                                  for i, j in enumerate(req.jobs)], 2.0))
    assert scalar(db, "SELECT count(*) FROM asset WHERE content_key IS NOT NULL AND video_id = %(v)s", v=video) == 2
    # the same shots again with the same inputs: put the attempt counter back, as a retry of a stage that saved nothing would see it
    db.execute("UPDATE shot SET keyframe_asset_id = NULL, status = 'PENDING'")
    again = ShotStage(make_deps(db, cache=cache))
    again.next_keyframe_attempt = lambda v: req.jobs[0].attempt  # same attempt = same seed
    r2 = again.keyframe_request(video, None)
    assert r2.jobs == [] and r2.reused == 2
    assert scalar(db, "SELECT count(*) FROM shot WHERE keyframe_asset_id IS NOT NULL AND status = 'READY'") == 2
    assert scalar(db, "SELECT count(*) FROM provenance p JOIN asset a ON a.id = p.asset_id WHERE p.reused_from_asset_id IS NOT NULL AND a.video_id = %(v)s", v=video) == 2
    res = again.save_keyframes(video, r2, KeyframeResult([], 0.0))  # nothing left to render must not fail
    assert "2 reused from the cache at Rs 0" in res.summary
    # a per-shot redo (new seed) never reuses
    assert len(again.shot_keyframe_request(video, [ids[0]], None, ImagePolicy.AUTO).jobs) == 1
    db.execute("DELETE FROM shot WHERE id = ANY(%(i)s)", {"i": ids})  # the scratch database is shared: other tests count READY shots


def take(index, uri, passed=True):
    return NarrationResult([NarrationClip(index, uri, 2.0, Usage(10, 1000, 0, 1010), 1, passed, "coverage 100%")], "gemini-3.8-flash-tts", "Charon")


def test_narration_line_is_reused_when_unchanged_and_never_when_it_failed(world):
    db, video, cache = world
    v = VoiceStage(make_deps(db, cache=cache))
    v.save_narration(video, take(0, "s3://b/line0.wav", passed=False))  # an imperfect take is not stored for reuse
    assert scalar(db, "SELECT count(*) FROM asset WHERE content_key IS NOT NULL AND kind = 'audio' AND video_id = %(v)s", v=video) == 0
    assert [x.index for x in v.narration_request(video).lines] == [0]
    v.save_narration(video, take(0, "s3://b/line0-ok.wav"))
    assert scalar(db, "SELECT count(*) FROM asset WHERE content_key IS NOT NULL AND kind = 'audio' AND video_id = %(v)s", v=video) == 1
    before = scalar(db, "SELECT count(*) FROM asset WHERE kind = 'audio' AND video_id = %(v)s", v=video)
    assert v.narration_request(video).lines == []  # a second run of the stage: the checked line comes from the cache
    assert scalar(db, "SELECT count(*) FROM asset WHERE kind = 'audio' AND video_id = %(v)s", v=video) == before + 1
    assert line(db, video, 0).audio_uri == "s3://b/line0-ok.wav"
    assert scalar(db, "SELECT count(*) FROM provenance p JOIN asset a ON a.id = p.asset_id WHERE p.reused_from_asset_id IS NOT NULL AND a.video_id = %(v)s", v=video) == 1
    assert scalar(db, "SELECT count(*) FROM cost_entry WHERE unit_type = 'cache_hit' AND amount_inr = 0 AND video_id = %(v)s", v=video) == 1
    # a changed text is voiced again; so is everything when this video opted out
    db.execute("UPDATE script SET beat_sheet_json = jsonb_set(beat_sheet_json, '{lines,0,text}', '\"A star dies.\"')")
    assert [x.index for x in v.narration_request(video).lines] == [0]
    assert script_of(db, video).lines[0].text == "A star dies."
    db.execute("UPDATE script SET beat_sheet_json = jsonb_set(beat_sheet_json, '{lines,0,text}', '\"A star is born.\"')")
    cache.set_enabled(video, False)
    assert [x.index for x in v.narration_request(video).lines] == [0]
