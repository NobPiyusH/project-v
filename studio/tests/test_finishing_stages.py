"""Footage, assembly, smoothing and sharpening (ports of the matching tests in ProductionPersistenceTest)."""

import json
import uuid

import pytest

from studio.media.types import (
    AssemblyResult,
    FootageCandidate,
    FootageFetchResult,
    FootageSearchResponse,
    FootageSearchResult,
    SmoothOutput,
    SmoothResult,
)
from studio.production.finishing_stages import AssemblyStage, FinishStage, FootageStage, SharpenStage
from studio.production.video_stage import VideoStage
from studio.tests.pworld import make_deps, scalar, seed_video
from studio.tests.test_video_stage import shot as make_shot


@pytest.fixture
def world(scratch_db):
    scratch_db.execute("DELETE FROM cost_entry")
    return scratch_db, seed_video(scratch_db, duration=60)


def shot_with_keyframe(db, video, ord_, seconds):
    return make_shot(db, video, ord_, seconds)


def video_asset(db, video, shot, kind, uri):
    a = uuid.uuid4()
    db.execute("INSERT INTO asset (id, video_id, kind, uri_hot, shot_id, clip_kind) VALUES (%(a)s, %(v)s, 'video', %(u)s, %(s)s, %(k)s)",
               {"a": a, "v": video, "u": uri, "s": shot, "k": kind})
    return a


def set_prompt(db, shot, prompt):
    db.execute("UPDATE shot SET prompt_json = CAST(%(p)s AS jsonb) WHERE id = %(s)s", {"p": json.dumps(prompt), "s": shot})


def test_picked_nasa_footage_replaces_the_generated_clip_for_that_shot(world):
    db, video = world
    deps = make_deps(db)
    sun, other = shot_with_keyframe(db, video, 0, 3.0), shot_with_keyframe(db, video, 1, 3.0)
    set_prompt(db, sun, {"footageQuery": "solar flare SDO"})
    footage, clips = FootageStage(deps), VideoStage(deps, final_provider="wan")
    search = footage.search_request(video, None, None)
    assert [q.shot_id for q in search.queries] == [str(sun)], "only the shot the Director marked"
    assert footage.search_request(video, None, "  aurora ").queries[0].query == "aurora"
    cand = FootageCandidate("GSFC_SDO_1", "Solar flare", "d", "GSFC, Joy Ng", "2016-06-24", "t", "p")
    saved = footage.save_candidates(video, FootageSearchResponse([FootageSearchResult(str(sun), "solar flare SDO", [cand], None)]))
    assert saved.summary.startswith("Found 1 free real clip (NASA, Wikimedia Commons)")
    req = footage.fetch_request(video, sun, "GSFC_SDO_1", None)
    assert req.aspect_ratio == "9:16" and req.duration_sec == 3.0 and req.shot_number == 1
    result = footage.save_footage(video, req, FootageFetchResult("s3://b/sun.mp4", 1080, 1920, 3.0, 1920, 1080, 1.78, 35.0,
                                                                "https://images-assets.nasa.gov/x~orig.mp4"))
    assert "credit: NASA / GSFC, Joy Ng" in result.summary and "may look soft" in result.summary, "a 1.78x enlargement is flagged"
    assert scalar(db, "SELECT count(*) FROM asset a JOIN provenance p ON p.id = a.provenance_id WHERE a.shot_id = %(s)s AND a.clip_kind = 'footage' "
                      "AND p.licence_id = '00000000-0000-0000-0000-00000000a005'", s=sun) == 1
    assert [j.shot_id for j in clips.clip_request(video, "draft", None).jobs] == [str(other)], "no GPU clip for the footage shot"
    footage.clear_footage(sun)
    assert len(clips.clip_request(video, "draft", None).jobs) == 2, "back to a generated clip"
    with pytest.raises(RuntimeError, match="not part of this video"):
        footage.fetch_request(video, uuid.uuid4(), "x", None)


def test_commons_footage_keeps_its_own_credit_and_licence(world):
    db, video = world
    footage = FootageStage(make_deps(db))
    s = shot_with_keyframe(db, video, 0, 3.0)
    req = footage.fetch_request(video, s, "commons:File:Old.webm", 2.0)
    res = footage.save_footage(video, req, FootageFetchResult("s3://b/c.mp4", 1920, 1080, 3.0, 1920, 1080, 1.0, 2.0, "https://c/x"))
    assert "real Wikimedia Commons footage (3.0s, credit: Wikimedia Commons)" in res.summary and "soft" not in res.summary
    assert scalar(db, "SELECT p.licence_id FROM asset a JOIN provenance p ON p.id = a.provenance_id WHERE a.shot_id = %(s)s", s=s) == \
        uuid.UUID("00000000-0000-0000-0000-00000000a010")
    assert footage.save_candidates(video, FootageSearchResponse([])).summary == "No shots asked for real footage."


def test_the_master_uses_footage_then_final_then_draft_and_refuses_shots_without_video(world):
    db, video = world
    assembly = AssemblyStage(make_deps(db))
    s1, s2 = shot_with_keyframe(db, video, 0, 3.0), shot_with_keyframe(db, video, 1, 3.0)
    set_prompt(db, s2, {"startSec": 3.0, "lineIndex": 0})
    draft1 = video_asset(db, video, s1, "draft", "s3://b/d1.mp4")
    db.execute("UPDATE shot SET draft_clip_asset_id = %(a)s WHERE id = %(s)s", {"a": draft1, "s": s1})
    with pytest.raises(RuntimeError) as e:
        assembly.assembly_request(video)
    assert str(e.value).startswith("1 shot has no moving video yet (shot 2)")

    draft2, final2 = video_asset(db, video, s2, "draft", "s3://b/d2.mp4"), video_asset(db, video, s2, "final", "s3://b/f2.mp4")
    nasa1 = video_asset(db, video, s1, "footage", "s3://b/nasa1.mp4")
    db.execute("UPDATE shot SET draft_clip_asset_id = %(d)s, final_clip_asset_id = %(f)s WHERE id = %(s)s", {"d": draft2, "f": final2, "s": s2})
    db.execute("UPDATE shot SET footage_asset_id = %(a)s WHERE id = %(s)s", {"a": nasa1, "s": s1})
    req = assembly.assembly_request(video)
    assert [f"{x.kind}:{x.uri}" for x in req.shots] == ["footage:s3://b/nasa1.mp4", "final:s3://b/f2.mp4"]
    assert req.aspect_ratio == "9:16" and req.captions is True and req.total_sec == 6.0

    saved = assembly.save_master(video, AssemblyResult("s3://b/master.mp4", "s3://b/pkg.zip", "s3://b/c.srt", 6.0, 1080, 1920, 30,
                                                       [{"shot": 1, "fit": "trimmed"}, {"shot": 2, "fit": "slowed 1.80x (looks slow-motion)"}], 4, 1))
    assert "1 shot slowed to fill its slot (1 noticeably)" in saved.summary and saved.summary.startswith("Master ready: 6s, 1080x1920 @ 30 fps")
    assert scalar(db, "SELECT count(*) FROM video v JOIN asset m ON m.id = v.master_asset_id JOIN asset p ON p.id = v.master_package_asset_id "
                      "JOIN asset c ON c.id = v.master_captions_asset_id WHERE v.id = %(v)s AND p.kind = 'timeline' AND c.kind = 'subtitle'", v=video) == 1


def test_the_voice_running_past_the_pictures_holds_the_last_shot(world):
    db, video = world
    assembly = AssemblyStage(make_deps(db), output_height=1440)
    s1 = shot_with_keyframe(db, video, 0, 3.0)
    set_prompt(db, s1, {"startSec": 0, "lineIndex": 0, "sfxQuery": "rain"})
    db.execute("UPDATE shot SET draft_clip_asset_id = %(a)s WHERE id = %(s)s", {"a": video_asset(db, video, s1, "draft", "s3://b/d.mp4"), "s": s1})
    script = {"version": 1, "lines": [{"index": 0, "text": "Long line.", "spoken": True, "audioUri": "s3://b/l.wav", "durationSec": 6.0,
                                       "audioAssetId": str(uuid.uuid4())}], "lineStarts": {"0": 0.4}}
    db.execute("UPDATE script SET beat_sheet_json = CAST(%(b)s AS jsonb) WHERE video_id = %(v)s", {"b": json.dumps(script), "v": video})
    req = assembly.assembly_request(video)
    assert req.output_height == 1440 and req.shots[0].sfx_query == "rain"
    assert req.narration[0].start_sec == 0.4 and req.narration[0].text == "Long line."
    assert req.shots[0].duration_sec == pytest.approx(6.4 + 1.5) and req.total_sec == pytest.approx(7.9)


def test_smoothing_follows_the_clip_in_use_and_the_edit_uses_it_only_while_fresh(world):
    db, video = world
    deps = make_deps(db)
    finish, assembly = FinishStage(deps), AssemblyStage(deps)
    s1 = shot_with_keyframe(db, video, 0, 3.0)
    d1 = video_asset(db, video, s1, "draft", "s3://b/d1.mp4")
    db.execute("UPDATE shot SET draft_clip_asset_id = %(a)s, prompt_json = CAST('{\"startSec\": 0}' AS jsonb) WHERE id = %(s)s", {"a": d1, "s": s1})
    est = finish.estimate(video)
    assert est.clips == 1 and est.needs_approval and est.summary.startswith("Ready to smooth 1 clip to 24 fps on one Modal L4: about ₹")
    req = finish.request(video)
    finish.save(video, req, SmoothResult([SmoothOutput(str(s1), str(d1), "s3://b/d1-smooth.mp4", 4.0, 72, 24, None)], 1, False))
    assert finish.estimate(video).clips == 0, "already smooth from this clip"
    shots = assembly.assembly_request(video).shots
    assert f"{shots[0].kind}:{shots[0].uri}" == "draft+smooth:s3://b/d1-smooth.mp4"

    d2 = video_asset(db, video, s1, "draft", "s3://b/d2.mp4")  # the owner picks another take
    db.execute("UPDATE shot SET draft_clip_asset_id = %(a)s WHERE id = %(s)s", {"a": d2, "s": s1})
    shots = assembly.assembly_request(video).shots
    assert f"{shots[0].kind}:{shots[0].uri}" == "draft:s3://b/d2.mp4", "stale smooth clip not used"
    assert finish.estimate(video).clips == 1, "the new take needs smoothing"


def test_a_24_fps_clip_and_real_footage_are_not_smoothed_and_a_total_failure_fails(world):
    db, video = world
    finish = FinishStage(make_deps(db))
    s1 = shot_with_keyframe(db, video, 0, 3.0)
    d1 = video_asset(db, video, s1, "draft", "s3://b/d1.mp4")
    db.execute("UPDATE shot SET draft_clip_asset_id = %(a)s WHERE id = %(s)s", {"a": d1, "s": s1})
    db.execute("UPDATE asset SET fps = 24 WHERE id = %(a)s", {"a": d1})
    assert finish.estimate(video).summary == "Every clip is already smooth (or is real footage)." and finish.jobs(video) == []
    db.execute("UPDATE asset SET fps = 16 WHERE id = %(a)s", {"a": d1})
    req = finish.request(video)
    with pytest.raises(RuntimeError, match="No clips were smoothed: boom"):
        finish.save(video, req, SmoothResult([SmoothOutput(str(s1), str(d1), None, 0.0, 0, 0, "boom")], 1, False))
    ok = finish.save(video, req, SmoothResult([SmoothOutput(str(s1), str(d1), None, 0.0, 0, 0, "stopped")], 0, True))
    assert ok.summary == "0/1 clips smoothed to a true 24 fps (RIFE in-between frames) on the laptop GPU (free)."


def test_sharpening_works_on_the_smoothed_clip_and_the_edit_uses_it_only_while_fresh(world):
    db, video = world
    deps = make_deps(db)
    finish, assembly = FinishStage(deps), AssemblyStage(deps)
    sharpen = SharpenStage(deps, finish)
    s1 = shot_with_keyframe(db, video, 0, 3.0)
    d1 = video_asset(db, video, s1, "draft", "s3://b/d1.mp4")
    db.execute("UPDATE shot SET draft_clip_asset_id = %(a)s, prompt_json = CAST('{\"startSec\": 0}' AS jsonb) WHERE id = %(s)s", {"a": d1, "s": s1})
    assert sharpen.estimate(video).clips == 1, "a shot about to be smoothed is counted once"
    finish.save(video, finish.request(video), SmoothResult([SmoothOutput(str(s1), str(d1), "s3://b/d1-smooth.mp4", 4.0, 72, 24, None)], 1, False))
    req = sharpen.request(video)
    assert len(req.jobs) == 1 and req.jobs[0].uri == "s3://b/d1-smooth.mp4", "sharpening starts from the smoothed clip" and req.jobs[0].src_fps == 24.0
    smooth_id = req.jobs[0].source_asset_id
    saved = sharpen.save(video, req, SmoothResult([SmoothOutput(str(s1), smooth_id, "s3://b/d1-sharp.mp4", 95.0, 72, 24, None)], 1, False))
    assert saved.summary.startswith("1/1 clips sharpened")
    assert sharpen.estimate(video).clips == 0, "already sharp from the clip in use"
    assert scalar(db, "SELECT count(*) FROM cost_entry WHERE video_id = %(v)s AND provider_id = 'modal_h100'", v=video) == 1
    shots = assembly.assembly_request(video).shots
    assert f"{shots[0].kind}:{shots[0].uri}" == "draft+smooth+sharp:s3://b/d1-sharp.mp4"

    d2 = video_asset(db, video, s1, "draft", "s3://b/d2.mp4")  # the owner picks another take
    db.execute("UPDATE shot SET draft_clip_asset_id = %(a)s WHERE id = %(s)s", {"a": d2, "s": s1})
    shots = assembly.assembly_request(video).shots
    assert f"{shots[0].kind}:{shots[0].uri}" == "draft:s3://b/d2.mp4", "stale sharp clip not used"
    assert sharpen.estimate(video).clips == 1, "the new take needs sharpening"
    assert sharpen.gpu_seconds(0) == 0 and sharpen.gpu_seconds(9) == 90 + 9 * 60 + 2 * 30
