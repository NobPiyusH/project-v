"""AI Producer control side (ports of the producer tests in ProductionPersistenceTest) and MoneyCapsTest."""

import json
import uuid
from decimal import Decimal

import pytest

from studio.ledger.budget_guard import GEMINI, BudgetExceededError
from studio.media.types import NarrationLine, NarrationRequest, ProducerItemVerdict, ProducerReviewResult, Usage
from studio.media.wire import to_json
from studio.production.producer import LearnedRules, ProducerService, reviews, with_style
from studio.production.records import StoredLine, StoredScript
from studio.tests.pworld import make_deps, scalar, seed_video


@pytest.fixture
def world(scratch_db):
    db = scratch_db
    db.execute("DELETE FROM producer_decision")
    db.execute("DELETE FROM approval")
    db.execute("DELETE FROM producer_rule")
    db.execute("DELETE FROM producer_setting")
    video = seed_video(db, duration=60)
    return db, video, ProducerService(make_deps(db), "gemini-3.6-flash")


def spend(db, video, provider, inr):
    db.execute("INSERT INTO cost_entry (id, video_id, provider_id, amount_inr) VALUES (%(i)s, %(v)s, %(p)s, %(a)s)",
               {"i": uuid.uuid4(), "v": video, "p": provider, "a": Decimal(inr)})


def test_producer_listens_to_every_voiced_line_with_its_delivery_and_the_owners_feedback(world):
    db, video, producer = world
    voiced = StoredScript(2, [StoredLine(0, "A star is born.", "Act 1", "hook", True, None, "s3://b/l0.wav", 2.0, "READY", False, False, None, [],
                                         None, "hushed, awe-struck"), StoredLine(1, "Silence.", "Act 1", "beat", False)], "Aoede", "warm", "strings", None, None)
    db.execute("INSERT INTO script (id, video_id, version, beat_sheet_json) VALUES (%(i)s, %(v)s, 2, CAST(%(b)s AS jsonb))",
               {"i": uuid.uuid4(), "v": video, "b": json.dumps(to_json(voiced))})
    db.execute("INSERT INTO approval (id, video_id, stage, decision, notes, actor) VALUES (%(i)s, %(v)s, 'NARRATION', 'reject', 'voice not good', 'owner')",
               {"i": uuid.uuid4(), "v": video})
    req = producer.review_request(video, "NARRATION")
    assert len(req.items) == 1, "only spoken lines with audio"
    item = req.items[0]
    assert item.kind == "audio" and "A star is born." in item.expect and "hushed, awe-struck" in item.expect
    assert any("voice not good" in r for r in req.rules), "the owner's feedback goes into the review"
    assert req.context.endswith("Narrator: Aoede, style: warm") and req.rubric.startswith("You are the producer")
    assert producer.review_request(video, "MUSIC") is None, "stages it doesn't review: None"
    assert reviews("SCRIPT") and not reviews("MUSIC")
    assert [i.id for i in producer.script_items(video)] == ["script"] and "1. A star is born." in producer.script_items(video)[0].text


def test_shot_stage_items_skip_footage_shots(world):
    db, video, producer = world
    scene = uuid.uuid4()
    db.execute("INSERT INTO scene (id, video_id, ord, title) VALUES (%(s)s, %(v)s, 0, 'S')", {"s": scene, "v": video})
    for i in range(2):
        a = uuid.uuid4()
        db.execute("INSERT INTO asset (id, video_id, kind, uri_hot) VALUES (%(a)s, %(v)s, 'image', %(u)s)", {"a": a, "v": video, "u": f"s3://b/{i}.png"})
        db.execute("INSERT INTO shot (id, scene_id, ord, description, prompt_json, keyframe_asset_id) VALUES (%(i)s, %(s)s, %(o)s, 'desc', "
                   "CAST('{\"keyframePrompt\":\"kp\",\"motionPrompt\":\"mp\"}' AS jsonb), %(a)s)", {"i": uuid.uuid4(), "s": scene, "o": i, "a": a})
    items = producer.review_request(video, "KEYFRAMES").items
    assert [(i.id, i.expect) for i in items] == [("shot-1", "Shot 1: desc. Prompt: kp"), ("shot-2", "Shot 2: desc. Prompt: kp")]
    ids = producer.shot_ids_for(video, ["shot-2", "line-0"])
    assert len(ids) == 1 and uuid.UUID(ids[0]) and producer.shot_ids_for(video, ["line-0"]) == []
    with pytest.raises(ValueError):
        producer.shot_items(video, "id; DROP TABLE shot", "image")


def test_producer_advice_is_logged_costed_and_compared_with_the_owners_decision(world):
    db, video, producer = world
    result = ProducerReviewResult("redo", 0.8, "Line 1 sounds rushed.", "Line 1: slower, hushed.",
                                  [ProducerItemVerdict("line-0", 4, False, "rushed", "slower, hushed")], "gemini-3.6-flash", Usage(5000, 300, 0, 5300))
    advice = producer.save_review(video, "NARRATION", 1, result)
    assert advice.summary == ("AI Producer recommends REDO of 1 item (80% sure): Line 1 sounds rushed. Suggested redo: Line 1: slower, hushed.")
    assert advice.redo_notes == "Line 1: slower, hushed." and advice.weak_items == ["line-0"] and advice.verdict == "redo"
    assert scalar(db, "SELECT count(*) FROM cost_entry WHERE video_id = %(v)s AND note LIKE 'PRODUCER review%%'", v=video) >= 1, "the review is a cost entry (rule 6)"
    producer.record_owner_decision(video, "NARRATION", "reject", "yes, too fast")
    d = producer.decisions(video)[0]
    assert d["owner_decision"] == "reject" and d["agreed"] is True, "it recommended a redo and the owner rejected: agreement"
    assert producer.agreement() == {"decided": 1, "agreed": 1, "rate": 100}
    producer.mark_acted(advice.decision_id, "redo")
    assert scalar(db, "SELECT acted FROM producer_decision WHERE id = %(i)s", i=uuid.UUID(advice.decision_id)) == "redo"


def test_the_same_issue_twice_gets_one_firm_fix_then_goes_to_the_owner(world):
    db, video, producer = world

    def review():
        return ProducerReviewResult("redo", 0.7, "A visible person.", "remove the person", [ProducerItemVerdict("shot-1", 3, False, "a person is visible", "empty")],
                                    "gemini-3.6-flash", Usage(0, 0, 0, 0))

    first = producer.save_review(video, "KEYFRAMES", 1, review())
    assert first.verdict == "redo" and "RECURRING" not in first.summary
    second = producer.save_review(video, "KEYFRAMES", 2, review())
    assert second.verdict == "redo" and "RECURRING ISSUE (visible people), firm fix" in second.redo_notes
    third = producer.save_review(video, "KEYFRAMES", 3, review())
    assert third.verdict == "ask" and "even after a prompt fix" in third.summary
    assert [r["stop_rule"] for r in db.rows("SELECT stop_rule FROM producer_decision ORDER BY created_at")] == [None, "prompt_fix", "ask"]


def test_producer_spend_advice_follows_the_owners_limits(world):
    _, video, producer = world
    assert "would APPROVE" in producer.spend_advice(video, Decimal(30))
    assert "would ASK YOU" in producer.spend_advice(video, Decimal(60)), "above the Rs 50 line"


def test_producer_trust_switch_decides_when_it_may_act(world):
    _, _, producer = world
    producer.set_trust("advise")
    assert not producer.deciding()
    producer.set_trust("decide")
    assert producer.deciding()
    producer.set_trust("auto")
    assert not producer.deciding(), "auto: not before it has agreed with the owner on enough decisions"
    with pytest.raises(ValueError):
        producer.set_trust("yolo")


def test_producer_redo_limits_and_spend_decisions_follow_the_owners_limits(world):
    db, video, producer = world
    producer.set_trust("decide")
    assert producer.may_redo(video, 1)
    assert not producer.may_redo(video, 4), "at most 3 redo rounds per stage"
    assert producer.spend_decision(video, "DRAFT_GEN", Decimal(30)).approve, "inside the limits: it approves"
    refused = producer.spend_decision(video, "UPSCALE", Decimal(60))
    assert not refused.approve and "would ASK YOU" in refused.text, "above Rs 50: it asks"
    summary = producer.video_summary(video)
    assert summary["spend_approved"] == 1 and summary["asked"] == 1 and summary["trust"] == "decide" and summary["deciding"] is True
    assert summary["budgetInr"] == 100


def test_producer_rules_can_be_added_edited_switched_off_and_deleted(world):
    _, _, producer = world
    producer.add_rule("NARRATION", "no theatrical whispering")
    rule = next(r for r in producer.rules_list() if r["rule_text"] == "no theatrical whispering")
    assert "no theatrical whispering" in producer.rules("NARRATION"), "rules go into every review"
    producer.update_rule(rule["id"], None, False)
    assert "no theatrical whispering" not in producer.rules("NARRATION"), "switched off"
    producer.update_rule(rule["id"], "calm, warm documentary narration", True)
    assert "calm, warm documentary narration" in producer.rules("NARRATION")
    producer.delete_rule(rule["id"])
    assert all(r["id"] != rule["id"] for r in producer.rules_list())


def test_producer_voice_redo_uses_its_notes_as_the_reading_style():
    base = NarrationRequest("v", "tts", "check", "Aoede", "in a warm voice", "en", [NarrationLine(0, "Hi.", "hushed")])
    redo = with_style(base, "natural documentary voice, no whispering; say 'This bug' exactly")
    assert redo.style == "natural documentary voice, no whispering; say 'This bug' exactly" and redo.voice_name == "Aoede"
    assert redo.lines[0].delivery == "hushed", "same voice, only the direction changes"
    assert with_style(base, " ") == base


class FakeLlm:
    def __init__(self, rules):
        self.rules, self.calls = rules, []

    def call(self, video_id, purpose, system, user, tp):
        self.calls.append((purpose, user))
        assert tp is LearnedRules
        return LearnedRules(rules=self.rules)


def test_learning_replaces_learned_rules_keeps_owner_rules_and_needs_feedback(world):
    db, video, _ = world
    deps = make_deps(db)
    llm = FakeLlm(["  calm, no whispering  ", "", "slow down the hook"] + [f"extra {i}" for i in range(8)])
    producer = ProducerService(deps, llm=llm)
    assert producer.learn(video, "NARRATION") == [], "no feedback yet: nothing to learn"
    assert llm.calls == []
    producer.add_rule("NARRATION", "owner rule stays")
    db.execute("INSERT INTO approval (id, video_id, stage, decision, notes, actor) VALUES (%(i)s, %(v)s, 'NARRATION', 'reject', 'too fast', 'owner')",
               {"i": uuid.uuid4(), "v": video})
    learned = producer.learn(video, "NARRATION")
    assert learned[1] == "slow down the hook" and len(learned) == 6, "at most 6, blanks dropped"
    assert "calm, no whispering" in [r["rule_text"] for r in producer.rules_list()], "stored stripped"
    assert "reject: too fast" in llm.calls[0][1] and "owner rule stays" in llm.calls[0][1]
    producer.learn(video, "NARRATION")
    sources = [r["source"] for r in producer.rules_list() if r["stage"] == "NARRATION"]
    assert sources.count("learned") == 6 and sources.count("owner") == 1, "the earlier learned rules were replaced, not added to"
    assert ProducerService(deps).learn(video, "NARRATION") == [], "no LLM configured"


# ---------------------------------------------------------------------------------- MoneyCapsTest


def test_the_gemini_cap_is_a_thousand_rupees_and_refuses_anything_that_would_break_it(world):
    db, video, producer = world
    budget, pricing = producer.d.budget, producer.d.pricing
    assert pricing.gemini_monthly_cap_inr() == Decimal(1000)
    spend(db, video, GEMINI, pricing.gemini_monthly_cap_inr() - budget.gemini_spent_this_month() - 10)
    assert budget.can_spend(Decimal(10)), "exactly what is left still fits"
    assert not budget.can_spend(Decimal("10.01"))
    with pytest.raises(BudgetExceededError):
        budget.require(Decimal(11), "test")
    spend(db, video, GEMINI, 10)
    assert budget.remaining_share("gemini") == 0.0, "nothing left at the cap"


def test_the_gpu_cap_stops_modal_jobs_once_the_month_is_used_up(world):
    db, video, producer = world
    budget, pricing = producer.d.budget, producer.d.pricing
    spend(db, video, "modal_wan", pricing.gpu_monthly_cap_inr() - budget.gpu_spent_this_month() - 5)
    budget.require_gpu(Decimal(4), "a clip")
    with pytest.raises(BudgetExceededError, match="GPU cap"):
        budget.require_gpu(Decimal(6), "a clip")
    spend(db, video, "modal_zimage", 5)
    with pytest.raises(BudgetExceededError):
        budget.require_gpu(Decimal("0.5"), "a picture")
    assert budget.remaining_share("gpu") == 0.0


def test_producer_approves_up_to_fifty_and_asks_above_it_or_over_the_hundred_rupee_short_budget(world):
    db, video, producer = world
    pricing = producer.d.pricing
    assert pricing.producer_budget_inr(True) == 100 and pricing.producer_ask_above_inr() == 50
    assert "would APPROVE" in producer.spend_advice(video, Decimal(50))
    assert "would ASK YOU" in producer.spend_advice(video, Decimal("50.01")), "above 50 goes to the owner"
    spend(db, video, GEMINI, 60)
    assert "would APPROVE" in producer.spend_advice(video, Decimal(40)), "60 + 40 = exactly the 100 budget"
    assert "would ASK YOU" in producer.spend_advice(video, Decimal(41)), "over the 100 budget"


def test_producer_stops_redoing_once_the_short_has_used_its_budget(world):
    db, video, producer = world
    spend(db, video, GEMINI, 99)
    assert producer.may_redo(video, 1)
    assert not producer.may_redo(video, 99), "rounds are limited too"
    spend(db, video, GEMINI, 1)
    assert not producer.may_redo(video, 1), "100 spent: no more redos"
