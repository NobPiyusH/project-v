"""Port of PricingTest (same numbers, same config file the Java test uses) plus checks on rounding parity."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from studio.config import REPO_ROOT
from studio.ledger import Pricing, Usage

TEST_CONFIG = REPO_ROOT / "studio" / "tests" / "fixtures" / "config"


def at(day: str) -> Pricing:
    return Pricing(TEST_CONFIG, today=lambda: date.fromisoformat(day))


def test_flash_text_and_tts_double_on_1_jan_2027():
    million = Usage(1_000_000, 1_000_000, 0, 2_000_000)
    before, after = at("2026-12-31"), at("2027-01-01")
    fx = before.fx()
    assert float(before.text_inr(million)) == pytest.approx((0.75 + 3.75) * fx * 1.5, abs=0.01)
    assert float(after.text_inr(million)) == pytest.approx((1.50 + 7.50) * fx * 1.5, abs=0.01)
    assert float(before.tts_inr(million)) == pytest.approx((0.50 + 9.00) * fx * 1.5, abs=0.01)
    assert float(after.tts_inr(million)) == pytest.approx((1.00 + 18.00) * fx * 1.5, abs=0.01)
    assert float(before.image_inr("1K")) == pytest.approx(float(after.image_inr("1K")), abs=1e-9), "only announced prices change"


def test_router_candidate_costs_carry_the_gemini_calibration():
    p = at("2026-11-19")
    assert float(p.image_inr("1K")) == pytest.approx(0.0336 * p.fx() * 1.5, abs=0.001)
    assert float(p.music_inr(1)) == pytest.approx(0.08 * p.fx() * 1.5, abs=0.001)


def test_uncalibrated_rupees_are_rounded_to_four_places():
    v = at("2026-11-19").modal_gpu_inr("L40S", 1)
    assert v == v.quantize(Decimal("0.0001"))


def test_missing_section_is_an_error(tmp_path: Path):
    (tmp_path / "providers.yaml").write_text("providers: []\n")
    with pytest.raises(RuntimeError, match="no pricing section"):
        Pricing(tmp_path)


def test_unknown_routing_job_defaults_to_0_and_10():
    assert at("2026-11-19").routing_quality("no-such-job") == (0.0, 10.0)
