"""Port of CreativeDirectorTest (the scoring rules) plus the database side on a scratch Postgres."""

import json
import uuid
from decimal import Decimal

import pytest

from studio.agents.types import ActCritique, CreativeReview, ShotCritique, VideoCritique
from studio.media.types import ImageReviewResult, Usage
from studio.planner.plan import VideoPlan
from studio.planner.presets import StylePresets
from studio.production.creative_director import CreativeDirector, findings
from studio.production.support import ProductionDeps
from studio.tests.test_pricing import TEST_CONFIG, at

SHOTS = {1: {"id": "s1", "ord": 0}, 2: {"id": "s2", "ord": 1}, 3: {"id": "s3", "ord": 2}}
GOOD_VIDEO = VideoCritique(arc=4, hook=5, ending=4, variety=4, summary="strong", problem="")


def shot(n, meaning, literal, fix):
    return ShotCritique(shot=n, meaning=meaning, accuracy=5, continuity=5, literal=literal, writing=False, problem=f"problem {n}", fixPrompt=fix)


def sev(f):
    return [f"{x.severity}:{x.code}:{x.ref}" for x in f]


def test_generated_lettering_fails_even_when_everything_else_scores_well():
    garbled = ShotCritique(shot=3, meaning=5, accuracy=5, continuity=5, literal=False, writing=True, problem="the title reads NO KO",
                           fixPrompt="a lone figure in a sea of stars...")
    f = findings(CreativeReview(video=GOOD_VIDEO, acts=[], shots=[garbled, shot(1, 5, False, "")]), SHOTS, True, set())
    assert sev(f) == ["FAIL:CD_SHOT:shot 3"] and f[0].target_id == "s3" and "generated lettering" in f[0].message


def test_a_literal_keyword_image_fails_and_targets_its_shot_at_the_image_stage():
    f = findings(CreativeReview(video=GOOD_VIDEO, acts=[], shots=[shot(2, 3, True, "light leaving a galaxy..."), shot(1, 5, False, "")]),
                 SHOTS, True, set())
    assert sev(f) == ["FAIL:CD_SHOT:shot 2"] and f[0].target_id == "s2" and "shows one word instead of the idea" in f[0].message


def test_weak_shot_prompts_are_rewritten_at_the_shot_plan_and_only_reported_as_info():
    review = CreativeReview(video=GOOD_VIDEO, acts=[], shots=[shot(3, 2, False, "better prompt")])
    assert sev(findings(review, SHOTS, False, {3})) == ["INFO:CD_PROMPT_IMPROVED:shot 3"]
    assert sev(findings(review, SHOTS, False, set())) == ["FAIL:CD_SHOT:shot 3"] and findings(review, SHOTS, False, set())[0].target_id is None


def test_a_weak_paragraph_fails_the_shot_plan_unless_its_shots_were_all_rewritten():
    act = ActCritique(act="The Speed Limit of Time", flow=2, keyMoment=3, continuity=4, mood=4, summary="",
                      problem="the reveal has no strong image", shotsToFix=[2, 3])
    review = CreativeReview(video=GOOD_VIDEO, acts=[act], shots=[])
    assert sev(findings(review, SHOTS, False, {2})) == ["FAIL:CD_PARAGRAPH:paragraph “The Speed Limit of Time”"]
    fixed = findings(review, SHOTS, False, {2, 3})
    assert sev(fixed) == ["INFO:CD_PARAGRAPH_FIXED:paragraph “The Speed Limit of Time”"] and "(fixed by rewriting shots [2, 3])" in fixed[0].message


def test_a_weak_paragraph_of_images_targets_the_shots_that_fix_it():
    act = ActCritique(act="Act", flow=4, keyMoment=2, continuity=4, mood=4, summary="", problem="key moment is a filler shot", shotsToFix=[3])
    f = findings(CreativeReview(video=GOOD_VIDEO, acts=[act], shots=[]), SHOTS, True, set())
    assert sev(f) == ["FAIL:CD_PARAGRAPH:shot 3"] and f[0].target_id == "s3"


def test_a_weak_arc_fails_the_plan_but_only_warns_at_the_images():
    review = CreativeReview(video=VideoCritique(arc=2, hook=4, ending=3, variety=4, summary="", problem="the ending doesn't answer the hook"),
                            acts=[], shots=[])
    assert sev(findings(review, SHOTS, False, set())) == ["FAIL:CD_VIDEO:whole video"]
    images = findings(review, SHOTS, True, set())
    assert sev(images) == ["WARN:CD_VIDEO:whole video"] and images[0].target_id is None
    assert images[0].message.startswith("arc 2/5, hook 4/5, ending 3/5, variety 4/5: the ending")


def test_good_scores_produce_no_findings():
    review = CreativeReview(video=GOOD_VIDEO, acts=[ActCritique(act="A", flow=4, keyMoment=5, continuity=4, mood=4, summary="ok", problem="")],
                            shots=[shot(1, 4, False, ""), shot(2, 5, False, "")])
    assert findings(review, SHOTS, True, set()) == []


def test_a_weak_shot_without_a_fix_prompt_only_warns_at_the_image_stage():
    f = findings(CreativeReview(video=GOOD_VIDEO, acts=[], shots=[shot(1, 2, False, "")]), SHOTS, True, set())
    assert sev(f) == ["WARN:CD_SHOT:shot 1"] and f[0].target_id is None


# ---- database side


class FakeAgent:
    def __init__(self, review):
        self.review, self.contexts = review, []

    def review_shot_plan(self, video_id, context):
        self.contexts.append(context)
        return self.review


class FakeBudget:
    def __init__(self):
        self.required = []

    def require(self, amount, what):
        self.required.append((amount, what))


class FakeLedger:
    def __init__(self):
        self.rows = []

    def record(self, *a):
        self.rows.append(a)


@pytest.fixture
def world(scratch_db):
    db = scratch_db
    video, scene = uuid.uuid4(), uuid.uuid4()
    plan = VideoPlan(title="Light", format="short", durationSec=30, aspectRatio="9:16", stylePreset="shorts_punchy", needsResearch=False)
    db.execute("INSERT INTO video (id, prompt, status, plan_json) VALUES (%(i)s, 'x', 'RUNNING', CAST(%(p)s AS jsonb))",
               {"i": video, "p": json.dumps({"plan": plan.model_dump()})})
    db.execute("INSERT INTO scene (id, video_id, ord, title) VALUES (%(s)s, %(v)s, 0, 'Scene 1')", {"s": scene, "v": video})
    ids = []
    for i, owner_edited in enumerate([False, True]):
        sid = uuid.uuid4()
        ids.append(sid)
        prompt = {"keyframePrompt": f"old prompt {i}", "lineIndex": i, "startSec": 1.5 * i}
        if owner_edited:
            prompt["editedByOwner"] = True
        db.execute("INSERT INTO shot (id, scene_id, ord, duration_target_sec, description, prompt_json, priority) "
                   "VALUES (%(i)s, %(s)s, %(o)s, 4, 'what we see', CAST(%(p)s AS jsonb), 'hero')",
                   {"i": sid, "s": scene, "o": i, "p": json.dumps(prompt)})
    script = {"version": 1, "lines": [{"index": 0, "text": "Light is fast.", "act": "A1", "spoken": True},
                                      {"index": 1, "text": "(pause)", "act": "A1", "spoken": False}], "musicBrief": "calm",
              "lineStarts": {"0": 0.4}}
    db.execute("INSERT INTO script (id, video_id, version, beat_sheet_json, narration_md, word_count) "
               "VALUES (%(id)s, %(v)s, 1, CAST(%(b)s AS jsonb), '', 0)", {"id": uuid.uuid4(), "v": video, "b": json.dumps(script)})
    return db, video, ids


def director(db, review=None):
    deps = ProductionDeps(db, None, None, None, FakeLedger(), at("2026-11-19"), FakeBudget(), StylePresets(TEST_CONFIG),
                          text_model="gemini-3.6-flash,gemini-3.5-flash")
    return CreativeDirector(deps, FakeAgent(review), "gemini-3.6-flash"), deps


def test_shot_plan_review_rewrites_weak_prompts_but_never_the_owners_own(world):
    db, video, ids = world
    review = CreativeReview(video=GOOD_VIDEO, acts=[], shots=[shot(1, 2, False, "  better one  "), shot(2, 2, False, "better two")])
    cd, _ = director(db, review)
    f = cd.review_shot_plan(video, 1)
    assert sev(f) == ["INFO:CD_PROMPT_IMPROVED:shot 1", "FAIL:CD_SHOT:shot 2"], "the owner-edited shot 2 is not rewritten"
    p1 = json.loads(db.rows("SELECT prompt_json::text AS p FROM shot WHERE id = %(i)s", {"i": ids[0]})[0]["p"])
    assert p1["keyframePrompt"] == "better one" and p1["originalPrompt"] == "old prompt 0" and p1["revisedBy"] == "creative_director"
    p2 = json.loads(db.rows("SELECT prompt_json::text AS p FROM shot WHERE id = %(i)s", {"i": ids[1]})[0]["p"])
    assert p2["keyframePrompt"] == "old prompt 1"
    stored = cd.reports(video)
    assert stored[0]["stage"] == "SHOT_PLAN" and stored[0]["model"] == "gemini-3.6-flash" and stored[0]["review"]["shots"][0]["shot"] == 1
    ctx = cd._agent.contexts[0]
    assert ctx.startswith("Video: Light (short, 30s, 9:16, style: ") and "Narration by paragraph" in ctx and "## A1\n" in ctx
    assert "0 | 0.4s | Light is fast." in ctx and "1 | 0.0s | [visual beat] (pause)" in ctx
    assert "1 | 0 | 0.0 | " in ctx and "| hero | " in ctx and "| old prompt" not in ctx.split("Shots in order")[0]


def test_image_review_keeps_suggestions_until_a_targeted_redo_applies_them(world):
    db, video, ids = world
    db.execute("UPDATE shot SET keyframe_asset_id = NULL")
    cd, deps = director(db)
    review = CreativeReview(video=GOOD_VIDEO, acts=[], shots=[shot(1, 2, False, "suggested one")])
    f = cd.save_image_review(video, 1, ImageReviewResult(review, "gemini-3.6-flash", Usage(1000, 500, 0, 1500), 5))
    assert sev(f) == ["FAIL:CD_SHOT:shot 1"] and f[0].target_id == str(ids[0])
    assert deps.ledger.rows[0][1] == "gemini_api" and deps.ledger.rows[0][5] is True and "5 images" in deps.ledger.rows[0][6]
    p = json.loads(db.rows("SELECT prompt_json::text AS p FROM shot WHERE id = %(i)s", {"i": ids[0]})[0]["p"])
    assert p["suggestedPrompt"] == "suggested one" and p["keyframePrompt"] == "old prompt 0"
    cd.apply_suggested_prompts(video, [ids[0]])
    p = json.loads(db.rows("SELECT prompt_json::text AS p FROM shot WHERE id = %(i)s", {"i": ids[0]})[0]["p"])
    assert p["keyframePrompt"] == "suggested one" and "suggestedPrompt" not in p and p["originalPrompt"] == "old prompt 0"
    unavailable = cd.save_image_review(video, 2, None)
    assert [x.code for x in unavailable] == ["CREATIVE_REVIEW_UNAVAILABLE"]


def test_image_review_request_carries_lines_shots_and_checks_the_budget(world):
    db, video, ids = world
    db.execute("INSERT INTO asset (id, video_id, kind, uri_hot) VALUES (%(a)s, %(v)s, 'image', 's3://b/k1.png')",
               {"a": (a := uuid.uuid4()), "v": video})
    db.execute("UPDATE shot SET keyframe_asset_id = %(a)s WHERE id = %(i)s", {"a": a, "i": ids[0]})
    cd, deps = director(db)
    r = cd.image_review_request(video)
    assert deps.budget.required == [(Decimal(5), "CREATIVE_REVIEW images")]
    assert (r.model, r.title, r.aspect_ratio, r.music_brief) == ("gemini-3.6-flash", "Light", "9:16", "calm")
    assert [(x.index, x.act, x.start_sec) for x in r.lines] == [(0, "A1", 0.4), (1, "A1", 0.0)]
    assert len(r.shots) == 1 and (r.shots[0].n, r.shots[0].line_index, r.shots[0].act, r.shots[0].image_uri) == (1, 0, "A1", "s3://b/k1.png")
    assert r.shots[0].keyframe_prompt == "old prompt 0" and r.shots[0].priority == "hero" and r.rubric.startswith("Judge MEANING")
