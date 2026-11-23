"""The rest of DreamBrokerRouteTest (batch order, shared pod ends at empty, SVI beats on the pod, FLUX provenance) and DreamStudioCancelTest.
Uses the same fake provider and scratch Postgres as test_dream_gpu.py: nothing is rented."""

import json
import uuid

import pytest

from studio.dream import svi
from studio.media.types import ClipOutput, ClipResult, KeyframeOutput, KeyframeResult
from studio.tests.dworld import count, make_world, seed_short
from studio.tests.test_dream_gpu import g  # noqa: F401  (fixture)


def test_batch_order_counts_only_working_shorts_ahead(g):  # noqa: F811
    db = g.db
    me, others = None, []
    for step, status in [("CLIPS", "RUNNING"), ("PICTURES", "RUNNING"), ("WRITE", "RUNNING"), ("WRITE", "WAITING"), ("MUSIC", "RUNNING")]:
        d = uuid.uuid4()
        me = me or d  # the first row is this Short, at its clips
        others.append(d)
        db.execute("INSERT INTO dream_short (id, idea, step, status) VALUES (%(id)s, 'x', %(s)s, %(st)s)", {"id": d, "s": step, "st": status})
    try:
        assert g.dreams.gpu_batch_ahead(me, "CLIPS") == 2, "pictures + writing Shorts go first; waiting/music ones never hold it up"
        assert g.dreams.gpu_batch_ahead(me, "PICTURES") == 1, "no pod on yet: wait for the Short still writing"
        assert g.dreams.gpu_batch_ahead(me, "MUSIC") == 0
    finally:
        db.execute("DELETE FROM dream_short WHERE id = ANY(%(ids)s)", {"ids": others})


def test_batch_order_is_always_zero_off_the_broker_route(scratch_db):
    w = make_world(scratch_db)
    d, _ = seed_short(scratch_db)
    assert w.dreams.gpu_batch_ahead(d, "CLIPS") == 0


def test_the_shared_pod_ends_as_soon_as_no_short_waits_for_gpu_work(g):  # noqa: F811
    db = g.db
    a, b = (seed_short(db, shots=1)[0] for _ in range(2))
    try:
        g.dreams.status(a, "PICTURES", "RUNNING", None)
        g.dreams.status(b, "PICTURES", "RUNNING", None)
        g.dreams.picture_request(a, None, None)
        g.dreams.picture_request(b, None, None)
        ended = len(g.fake.terminated)
        g.dreams.status(a, "CLIPS", "WAITING_REVIEW", None)  # a's clips are done; b is still at work
        assert len(g.fake.terminated) == ended
        g.dreams.status(b, "PICTURES", "WAITING_REVIEW", None)  # goes on to clips by itself: keep the pod
        assert len(g.fake.terminated) == ended
        g.dreams.status(b, "CLIPS", "WAITING_COST", None)  # the owner's cost OK: the pod waits up to 1 min
        assert len(g.fake.terminated) == ended
        db.execute("UPDATE dream_short SET updated_at = now() - interval '2 minutes' WHERE id = %(id)s", {"id": b})
        g.dreams.release_gpu_if_nobody_waits()  # the 30 s check after the grace
        assert len(g.fake.terminated) == ended + 1
        assert db.rows("SELECT count(*) AS n FROM gpu_pod_lease WHERE released_at IS NULL")[0]["n"] == 0, "the lease is released with the pod"
    finally:
        for i in (a, b):
            db.execute("UPDATE dream_short SET status = 'CANCELLED' WHERE id = %(id)s", {"id": i})


def test_svi_scenes_render_beats_on_the_pod_and_redo_from_a_beat_keeps_the_earlier_ones(g):  # noqa: F811
    db = g.db
    dream = uuid.uuid4()
    db.execute("INSERT INTO dream_short (id, idea, look, status, svi_scenes, svi_scene_seconds, svi_between) VALUES (%(id)s, %(idea)s, 'unfiltered', "
               "'RUNNING', 2, 10, 'cut')", {"id": dream, "idea": "pink pier at dawn\nwalk to the end\n\na red door opens"})
    assert "2 scenes, 3 beats" in g.dreams.write_shots(dream, None)
    shots = g.dreams.shots(dream)
    assert [svi.scene_of(s) for s in shots] == [1, 1, 2] and [svi.beat_of(s) for s in shots] == [1, 2, 1]
    assert shots[1]["motion_prompt"] == "walk to the end", "Unfiltered: the words exactly"
    b11, b12, b21 = (str(s["id"]) for s in shots)

    pics = g.dreams.picture_request(dream, None, None)
    assert [j.shot_id for j in pics.jobs] == [b11, b21], "one picture per scene"
    g.dreams.save_pictures(dream, pics, KeyframeResult([KeyframeOutput(b11, "s3://b/s1.png", "gpu_pod", "z", 1, 5.0, None, None, []),
                                                        KeyframeOutput(b21, "s3://b/s2.png", "gpu_pod", "z", 2, 5.0, None, None, [])], 0, 10))
    assert "3 SVI beats in 2 scene(s)" in g.dreams.clip_estimate(dream, None).summary
    req = g.dreams.clip_request(dream, None)
    assert req.svi and not req.chain and [j.beat for j in req.jobs] == [1, 2, 1]
    assert [j.image_uri for j in req.jobs] == ["s3://b/s1.png", "s3://b/s1.png", "s3://b/s2.png"], "every beat is anchored on its scene's picture"
    assert all(j.num_frames == 81 and j.width == 480 and j.height == 832 for j in req.jobs)
    assert count(db, "SELECT count(*) FROM routing_decision WHERE dream_short_id = :d AND stage = 'DREAM_CLIPS_SVI'", d=dream) == 1
    g.dreams.save_clips(dream, req, ClipResult([ClipOutput(b11, 1, "dream", "s3://b/c11.mp4", 40, 81, 16, "fast", None, 0),
                                                ClipOutput(b12, 1, "dream", "s3://b/c12.mp4", 40, 81, 16, "fast", None, 0),
                                                ClipOutput(b21, 1, "dream", None, 0, 0, 0, "fast", "ComfyUI error", 0)], 1, False))
    assert count(db, "SELECT count(*) FROM asset a JOIN provenance p ON p.id = a.provenance_id WHERE a.dream_short_id = :d "
                     "AND p.model LIKE '%%svi2.0-pro scene%%'", d=dream) == 2, "provenance per beat (two made, one failed)"
    retry = g.dreams.clip_request(dream, None)
    assert [j.shot_id for j in retry.jobs] == [b21], "Retry: only the failed scene"
    redo = g.dreams.clip_request(dream, [b12])
    assert [j.shot_id for j in redo.jobs] == [b12], "redo beat 2: beat 1 is kept"
    assert redo.jobs[0].chain_from_uri == "s3://b/c11.mp4" and redo.jobs[0].take == 2, "it continues from beat 1"
    assert [j.shot_id for j in g.dreams.clip_request(dream, [b11]).jobs] == [b11, b12], "redo beat 1: later beats follow"
    g.dreams.save_clips(dream, retry, ClipResult([ClipOutput(b21, 1, "dream", "s3://b/c21.mp4", 40, 81, 16, "fast", None, 0)], 0, False))
    fin = g.dreams.finish_request(dream)
    assert fin.svi_scenes == [2, 1] and fin.svi_scene_frames == [0, 0], "Unfiltered keeps every frame"
    assert fin.dissolve_sec == 0.0 and fin.keep_order, "hard cut between scenes by default; scene order is the story"
    again = g.dreams.picture_request(dream, [b11], "warmer")
    g.dreams.save_pictures(dream, again, KeyframeResult([KeyframeOutput(b11, "s3://b/s1b.png", "gpu_pod", "z", 3, 5.0, None, None, [])], 0, 10))
    assert [j.shot_id for j in g.dreams.clip_request(dream, None).jobs] == [b11, b12], "a new scene picture makes every beat of that scene stale"
    g.dreams.status(dream, "MUSIC", "RUNNING", None)


def test_flux_pictures_on_the_pod_carry_flux_provenance_and_zimage_stays_per_short(g):  # noqa: F811
    db = g.db
    flux, (fs,) = seed_short(db, shots=1, picture_model="flux2-klein")
    z, (zs,) = seed_short(db, shots=1, picture_model="zimage")
    fp, zp = g.dreams.picture_request(flux, None, None), g.dreams.picture_request(z, None, None)
    assert fp.jobs[0].pod_model == "flux2-klein" and zp.jobs[0].pod_model == "zimage"
    said = g.dreams.save_pictures(flux, fp, KeyframeResult([KeyframeOutput(str(fs), "s3://b/f.png", "gpu_pod", "FLUX.2 klein 4B", 1, 5.0, None, None, [])], 0, 10))
    assert "FLUX.2 klein 4B" in said
    g.dreams.save_pictures(z, zp, KeyframeResult([KeyframeOutput(str(zs), "s3://b/z.png", "gpu_pod", "Tongyi-MAI/Z-Image-Turbo", 1, 5.0, None, None, [])], 0, 10))
    sql = ("SELECT p.model || ' | ' || l.name AS m FROM dream_shot s JOIN asset a ON a.id = s.picture_asset_id JOIN provenance p ON p.id = a.provenance_id "
           "JOIN licence l ON l.id = p.licence_id WHERE s.id = %(s)s")
    assert db.rows(sql, {"s": fs})[0]["m"] == "FLUX.2 klein 4B | FLUX.2 klein 4B (Apache-2.0)"
    assert db.rows(sql, {"s": zs})[0]["m"].startswith("Tongyi-MAI/Z-Image-Turbo | ")
    g.dreams.status(flux, "MUSIC", "RUNNING", None)
    g.dreams.status(z, "MUSIC", "RUNNING", None)


@pytest.mark.parametrize(("step", "status", "ended", "data"), [("CLIPS", "RUNNING", True, {"alreadyEnded": True}),
                                                               ("CLIPS", "RUNNING", False, {"afterClip": True}),
                                                               ("WRITE", "WAITING", False, None)])
def test_cancel_says_what_will_happen(scratch_db, step, status, ended, data):
    """DreamStudioCancelTest: cancel of an ended workflow is not an error; during clips it applies after the current clip."""
    w = make_world(scratch_db)
    dream, _ = seed_short(scratch_db, step=step, status=status)
    if ended:
        w.gateway.gone.add(f"dream-{dream}")
    w.studio.cancel(dream)  # no exception, even for a workflow that has already ended
    ev = [e for e in w.dreams.evidence.events(dream) if e["kind"] == "cancel"]
    assert len(ev) == 1 and ev[0]["step"] == step
    raw = ev[0]["data"]
    assert (None if raw is None else json.loads(raw)) == data
    assert w.dreams.clip_progress.stop_requested(dream), "the Stop is raised so clips in flight end after the current one"
    if ended:
        assert w.dreams.dream(dream)["status"] == "CANCELLED"
