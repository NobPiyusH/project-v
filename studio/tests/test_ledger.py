from decimal import Decimal

import pytest

from studio.ledger import BudgetExceededError, BudgetGuard, CostLedger
from studio.tests.fakes import FakeDb
from studio.tests.test_pricing import at


def guard(spent: str) -> BudgetGuard:
    return BudgetGuard(FakeDb({"starts_with": [{"spent": Decimal(spent)}]}), at("2026-11-19"))


def test_gemini_cap_blocks_spend_that_does_not_fit():
    g = guard("990")  # cap is 1000 in the test config
    assert g.can_spend(Decimal("10"))
    assert not g.can_spend(Decimal("10.0001"))
    with pytest.raises(BudgetExceededError, match="Monthly Gemini cap of ₹1000.0 would be exceeded by images"):
        g.require(Decimal("20"), "images")


def test_gpu_refusal_message_uses_whole_rupees():
    g = guard("0")
    cap = g.gpu_remaining()
    with pytest.raises(BudgetExceededError, match="Monthly GPU cap of ₹"):
        g.require_gpu(cap + 1, "a Wan batch")


def test_remaining_share_is_clamped_and_unbudgeted_is_one():
    assert guard("0").remaining_share("gemini") == 1.0
    assert guard("5000").remaining_share("gemini") == 0.0
    assert guard("500").remaining_share("gemini") == pytest.approx(0.5)
    assert guard("0").remaining_share("whatever") == 1.0


def test_aicredits_spend_is_all_time():
    g = BudgetGuard(FakeDb({"provider_id = ": [{"spent": Decimal("149")}]}), at("2026-11-19"))
    assert g.can_spend_aicredits(Decimal("1"))
    assert not g.can_spend_aicredits(Decimal("1.5"))


def test_record_writes_entry_and_video_total_in_one_transaction():
    db = FakeDb()
    CostLedger(db).record(None, "gemini_api", Decimal("1.5"), Decimal("3"), "tokens", False, "n")
    assert db.transactions == 1
    assert "INSERT INTO cost_entry" in db.executed[0][0]
    assert "UPDATE video SET spent_inr" in db.executed[1][0]
    assert db.executed[0][1]["amount"] == Decimal("1.5")
