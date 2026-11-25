"""Port of RouterTest, same cases and numbers, against a scratch Postgres with the real schema."""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from studio.ledger import BudgetExceededError, BudgetGuard
from studio.routing import Candidate, Router
from studio.routing.router import MAX_COOLDOWN_SEC
from studio.tests.test_pricing import at

CAP = "video.i2v.final"


def provider(db, id, quality, budget, enabled, caps):
    db.execute(
        """
        INSERT INTO provider (id, name, kind, capabilities_json, config_json, enabled)
        VALUES (%(id)s, %(id)s, 'api', CAST(%(caps)s AS jsonb), CAST(%(cfg)s AS jsonb), %(en)s)
        ON CONFLICT (id) DO UPDATE SET capabilities_json = EXCLUDED.capabilities_json,
            config_json = EXCLUDED.config_json, enabled = EXCLUDED.enabled
        """,
        {"id": id, "caps": caps, "cfg": json.dumps({"quality": quality, "budget": budget}), "en": enabled},
    )


@pytest.fixture
def env(scratch_db):
    db = scratch_db
    pricing = at("2026-11-19")
    router = Router(db, BudgetGuard(db, pricing), pricing)
    for t in ("routing_decision", "provider_health", "cost_entry"):
        db.execute(f"DELETE FROM {t}")
    db.execute("UPDATE provider SET owner_disabled = false")
    provider(db, "modal_wan", 6, "gpu", True, '["video.i2v.draft","video.i2v.final"]')
    provider(db, "gemini_omni", 8, "gemini", True, '["video.i2v.draft","video.i2v.final"]')
    provider(db, "other_video", 9, "none", False, '["video.i2v.final"]')
    video = uuid.uuid4()
    db.execute("INSERT INTO video (id, prompt, status) VALUES (%(id)s, 'router test', 'RUNNING')", {"id": video})
    return db, router, video


def both(wan, omni):
    return [Candidate("modal_wan", Decimal(wan)), Candidate("gemini_omni", Decimal(omni))]


def logged(db, video):
    return db.rows("SELECT count(*) AS n FROM routing_decision WHERE video_id = %(v)s", {"v": video})[0]["n"]


def test_owners_preference_wins_when_it_fits_and_the_decision_is_logged(env):
    db, router, video = env
    c = router.choose(video, "FINAL_GEN", CAP, both(40, 90), "modal_wan", True)
    assert c.provider_id == "modal_wan"
    assert c.decision_id is not None and logged(db, video) == 1
    row = db.rows("SELECT chosen_provider_id FROM routing_decision WHERE id = %(i)s", {"i": c.decision_id})[0]
    assert row["chosen_provider_id"] == "modal_wan"


def test_without_a_preference_the_highest_quality_eligible_provider_wins(env):
    _, router, video = env
    assert router.choose(video, "FINAL_GEN", CAP, both(40, 90), None, True).provider_id == "gemini_omni"


def test_preferred_provider_over_its_cap_falls_back_to_the_next_provider(env):
    _, router, video = env
    c = router.choose(video, "FINAL_GEN", CAP, both(40, 5000), "gemini_omni", True)
    assert c.provider_id == "modal_wan"
    assert "gemini cap" in c.reason


def test_nothing_eligible_defers_with_reasons_and_logs_it(env):
    db, router, video = env
    with pytest.raises(BudgetExceededError) as e:
        router.choose(video, "FINAL_GEN", CAP, both(99_999, 99_999), "gemini_omni", True)
    assert "modal_wan" in str(e.value) and "gemini_omni" in str(e.value)
    n = db.rows("SELECT count(*) AS n FROM routing_decision WHERE video_id = %(v)s AND chosen_provider_id IS NULL", {"v": video})
    assert n[0]["n"] == 1, "the deferral itself is a logged decision"


def test_failing_provider_cools_down_and_success_clears_it(env):
    _, router, video = env
    router.report_failure("gemini_omni", "503 overloaded")
    assert router.choose(video, "FINAL_GEN", CAP, both(40, 90), "gemini_omni", False).provider_id == "modal_wan"
    router.report_success("gemini_omni")
    assert router.choose(video, "FINAL_GEN", CAP, both(40, 90), "gemini_omni", False).provider_id == "gemini_omni"


def test_repeated_failures_grow_the_cooldown_up_to_thirty_minutes(env):
    _, router, _ = env
    for _ in range(20):
        router.report_failure("modal_wan", "timeout")
    sec = (router.cooldown_until("modal_wan") - datetime.now(UTC)).total_seconds()
    assert 25 * 60 < sec <= MAX_COOLDOWN_SEC, sec


def test_disabled_or_incapable_providers_are_skipped(env):
    _, router, video = env
    c = router.choose(
        video, "FINAL_GEN", CAP,
        [Candidate("other_video", Decimal(1)), Candidate("modal_wan", Decimal(10)), Candidate("unknown", Decimal(1))],
        "other_video", True,
    )
    assert c.provider_id == "modal_wan"
    assert any(r.get("why") == "disabled" for r in c.considered)
    assert any(r.get("why") == "not in the provider registry" for r in c.considered)


# ---- the scoring formula (owner, 18 Nov 2026): floor, then min(quality, enough) - lambda x cost


def test_drafts_go_to_the_cheap_tool_because_extra_quality_buys_nothing(env):
    _, router, video = env
    c = router.choose(video, "DRAFT_GEN", "video.i2v.draft", both(3, 46), None, False)
    assert c.provider_id == "modal_wan", c.reason


def test_finals_go_to_the_only_provider_above_the_floor(env):
    _, router, video = env
    assert router.choose(video, "FINAL_GEN", CAP, both(46, 46), None, False).provider_id == "gemini_omni"


def test_below_the_floor_is_a_noted_fallback_when_nothing_else_fits(env):
    _, router, video = env
    c = router.choose(video, "FINAL_GEN", CAP, both(46, 5000), None, True)
    assert c.provider_id == "modal_wan" and "BELOW the quality floor" in c.reason


def test_a_budget_running_low_tips_the_choice_to_the_cheaper_good_enough_provider(env):
    db, router, video = env
    provider(db, "omni_premium", 9, "gemini", True, '["video.i2v.final"]')
    provider(db, "omni_lite", 8, "gemini", True, '["video.i2v.final"]')
    pair = [Candidate("omni_premium", Decimal(40)), Candidate("omni_lite", Decimal(5))]
    assert router.choose(video, "FINAL_GEN", CAP, pair, None, False).provider_id == "omni_premium"
    db.execute(
        "INSERT INTO cost_entry (id, video_id, provider_id, amount_inr, units, unit_type, estimated, note) "
        "VALUES (%(i)s, %(v)s, 'gemini_api', 900, 1, 'call', true, 'spent')",
        {"i": uuid.uuid4(), "v": video},
    )
    assert router.choose(video, "FINAL_GEN", CAP, pair, None, False).provider_id == "omni_lite"


def test_a_batch_where_every_job_failed_cools_the_provider_and_any_success_clears_it(env):
    _, router, video = env
    router.report_batch("gemini_omni", 0, 3, "503 overloaded")
    assert router.cooldown_until("gemini_omni") is not None
    assert router.choose(video, "FINAL_GEN", CAP, both(40, 90), None, False).provider_id == "modal_wan"
    router.report_batch("gemini_omni", 1, 2, "one failed")
    assert router.choose(video, "FINAL_GEN", CAP, both(40, 90), None, False).provider_id == "gemini_omni"


def test_worker_decisions_are_logged_too(env):
    db, router, video = env
    router.record_external(video, "NARRATION", "tts.narration", "chatterbox", "the channel voice", None)
    assert logged(db, video) == 1


def test_owner_switch_off_is_respected_and_reported(env):
    _, router, video = env
    router.set_owner_disabled("gemini_omni", True)
    c = router.choose(video, "FINAL_GEN", CAP, both(40, 90), "gemini_omni", False)
    assert c.provider_id == "modal_wan"
    assert any(r.get("why") == "switched off by the owner" for r in c.considered)
    assert any(p["id"] == "gemini_omni" and p["owner_disabled"] is True for p in router.providers())
    router.set_owner_disabled("gemini_omni", False)
    router.report_failure("gemini_omni", "x")
    router.reset_health("gemini_omni")
    assert router.choose(video, "FINAL_GEN", CAP, both(40, 90), "gemini_omni", False).provider_id == "gemini_omni"
    assert len(router.budgets()) == 3


def test_preview_decisions_are_not_logged(env):
    db, router, video = env
    router.choose(video, "FINAL_GEN", CAP, both(40, 90), None, False)
    assert logged(db, video) == 0


def test_a_provider_over_its_own_cap_is_ineligible_and_defers(env):
    from studio.provider.caps import ProviderCap, ProviderCaps

    class Spend:
        def spent(self, cap, since, scope_id):
            return Decimal("29")

    db, router, video = env
    router._caps = ProviderCaps({"gemini_omni": ProviderCap("gemini_omni", "gemini_api", daily_inr=Decimal("30"))}, Spend())
    c = router.choose(video, "FINAL_GEN", "video.i2v.final", both("5", "5"), "gemini_omni", False)
    assert c.provider_id == "modal_wan" and "daily cap" in str(c.considered)
    only = [Candidate("gemini_omni", Decimal("5"))]
    with pytest.raises(BudgetExceededError, match="daily cap"):
        router.choose(video, "FINAL_GEN", "video.i2v.final", only, "gemini_omni", False)
