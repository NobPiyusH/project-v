"""Provider guard: retries with backoff, circuit breaker (fake clock) and per-provider caps (fake ledger). No network, no spend."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from studio.ledger.budget_guard import BudgetExceededError
from studio.provider import caps as C
from studio.provider.resilience import (
    BreakerPolicy,
    CircuitOpenError,
    ProviderCaller,
    RetryPolicy,
    is_transient,
)

CONFIG = Path(__file__).resolve().parents[2] / "config"


class HttpErr(Exception):
    def __init__(self, code):
        super().__init__(f"http {code}")
        self.code = code


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def caller(clock, **kw):
    return ProviderCaller(RetryPolicy(max_attempts=4, base_delay=1, max_delay=8, max_total_sec=30, jitter=0.5),
                          BreakerPolicy(failure_threshold=2, cooldown_sec=100), clock=clock, sleep=clock.sleep, rnd=lambda: 0.0, **kw)


def flaky(failures, then="ok"):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] <= len(failures):
            raise failures[calls["n"] - 1]
        return then

    return fn, calls


# ---- classification

def test_transient_classification():
    assert is_transient(TimeoutError()) and is_transient(ConnectionResetError())
    assert all(is_transient(HttpErr(c)) for c in (429, 500, 503, 504))
    assert not any(is_transient(HttpErr(c)) for c in (400, 401, 403, 404))
    assert not is_transient(ValueError("bad input"))


# ---- retry

def test_transient_failures_then_success_backs_off_exponentially():
    clk = Clock()
    fn, calls = flaky([HttpErr(503), TimeoutError(), HttpErr(429)])
    assert caller(clk).call("p", fn, idempotent=True) == "ok"
    assert calls["n"] == 4 and clk.slept == [1.0, 2.0, 4.0]


def test_jitter_shrinks_the_delay():
    clk = Clock()
    c = ProviderCaller(RetryPolicy(jitter=0.5), clock=clk, sleep=clk.sleep, rnd=lambda: 1.0)
    fn, _ = flaky([HttpErr(500)])
    c.call("p", fn, idempotent=True)
    assert clk.slept == [0.5]


def test_attempts_are_capped():
    clk = Clock()
    fn, calls = flaky([HttpErr(503)] * 10)
    with pytest.raises(HttpErr):
        caller(clk).call("p", fn, idempotent=True)
    assert calls["n"] == 4


def test_total_time_is_capped():
    clk = Clock()
    c = ProviderCaller(RetryPolicy(max_attempts=10, base_delay=10, max_delay=10, max_total_sec=15, jitter=0), clock=clk, sleep=clk.sleep, rnd=lambda: 0)
    fn, calls = flaky([HttpErr(503)] * 10)
    with pytest.raises(HttpErr):
        c.call("p", fn, idempotent=True)
    assert calls["n"] == 2 and clk.slept == [10.0]  # the second sleep would pass 15 s


def test_non_idempotent_paid_call_is_never_retried():
    clk = Clock()
    fn, calls = flaky([HttpErr(503)])
    with pytest.raises(HttpErr):
        caller(clk).call("lyria", fn, idempotent=False)
    assert calls["n"] == 1 and clk.slept == []


def test_permanent_errors_are_not_retried_and_do_not_trip_the_breaker():
    clk = Clock()
    c = caller(clk)
    for _ in range(5):
        fn, calls = flaky([HttpErr(400)])
        with pytest.raises(HttpErr):
            c.call("p", fn, idempotent=True)
        assert calls["n"] == 1
    assert c.breaker("p").state == "closed"


# ---- breaker

def test_breaker_opens_then_recovers_through_half_open():
    clk = Clock()
    opened = []
    c = caller(clk, on_open=lambda p, why: opened.append((p, why)))
    for _ in range(2):
        with pytest.raises(HttpErr):
            c.call("p", flaky([HttpErr(503)])[0], idempotent=False)
    assert c.breaker("p").state == "open" and opened == [("p", "HttpErr/503")]
    fn, calls = flaky([])
    with pytest.raises(CircuitOpenError) as ei:
        c.call("p", fn)
    assert isinstance(ei.value, BudgetExceededError) and calls["n"] == 0  # fails fast, stage holds, provider untouched
    clk.t += 101
    assert c.breaker("p").state == "half_open"
    assert c.call("p", fn) == "ok"
    assert c.breaker("p").state == "closed"


def test_failed_trial_reopens_for_another_cooldown():
    clk = Clock()
    c = caller(clk)
    for _ in range(2):
        with pytest.raises(HttpErr):
            c.call("p", flaky([HttpErr(500)])[0])
    clk.t += 101
    with pytest.raises(HttpErr):
        c.call("p", flaky([HttpErr(500)])[0])
    with pytest.raises(CircuitOpenError):
        c.call("p", lambda: "x")


def test_only_one_trial_call_in_half_open():
    clk = Clock()
    c = caller(clk)
    for _ in range(2):
        with pytest.raises(HttpErr):
            c.call("p", flaky([HttpErr(500)])[0])
    clk.t += 101
    inner = []

    def trial():
        try:
            c.call("p", lambda: "second")
        except CircuitOpenError:
            inner.append("blocked")
        return "first"

    assert c.call("p", trial) == "first" and inner == ["blocked"]


def test_breakers_are_per_provider():
    clk = Clock()
    c = caller(clk)
    for _ in range(2):
        with pytest.raises(HttpErr):
            c.call("a", flaky([HttpErr(500)])[0])
    assert c.call("b", lambda: "fine") == "fine"


def test_logs_carry_no_payload_or_key(caplog):
    clk = Clock()
    with caplog.at_level("INFO", logger="projectv.provider.resilience"), pytest.raises(HttpErr):
        caller(clk).call("p", flaky([HttpErr(500)])[0])
    assert "SECRETKEY" not in caplog.text and "HttpErr/500" in caplog.text


# ---- caps

class FakeSpend:
    def __init__(self, daily=0, video=0):
        self.daily, self.video, self.seen = Decimal(daily), Decimal(video), []

    def spent(self, cap, since, scope_id):
        self.seen.append((cap.provider, since, scope_id))
        return self.video if scope_id is not None else self.daily


NOW = datetime(2026, 11, 25, 15, 30, tzinfo=UTC)


def caps_with(daily=0, video=0):
    spend = FakeSpend(daily, video)
    return C.ProviderCaps(C.load_caps(CONFIG), spend, clock=lambda: NOW), spend


def test_default_config_caps_the_providers_that_billed_and_leaves_free_tiers_open():
    caps = C.load_caps(CONFIG)
    for p in ("gemini_lyria", "gemini_image", "gemini_image_review"):
        assert caps[p].daily_inr is not None and caps[p].per_video_inr is not None, p
        assert caps[p].ledger_provider == "gemini_api"
    assert caps["gemini_lyria"].idempotent is False and caps["gemini_lyria"].stages == ("MUSIC", "DREAM_MUSIC")
    assert caps["groq_whisper"].free and not caps["groq_whisper"].limited and caps["groq_whisper"].idempotent


def test_cap_allows_a_call_inside_the_limits():
    c, spend = caps_with(daily=5, video=2)
    assert c.refusal("gemini_lyria", Decimal("10"), stage="DREAM_MUSIC", scope_id=uuid.uuid4()) is None
    assert spend.seen[0][1] == datetime(2026, 11, 25, tzinfo=UTC)  # the day starts at 00:00 UTC


def test_daily_cap_refuses_with_a_clear_reason():
    c, _ = caps_with(daily=25)
    why = c.refusal("gemini_lyria", Decimal("10"), stage="MUSIC")
    assert why and "daily cap" in why and "00:00 UTC" in why


def test_per_video_cap_refuses():
    c, _ = caps_with(video=10)
    why = c.refusal("gemini_lyria", Decimal("10"), stage="MUSIC", scope_id=uuid.uuid4())
    assert why and "per-video cap" in why


def test_stage_not_allowed_is_refused():
    c, _ = caps_with()
    why = c.refusal("gemini_lyria", Decimal("1"), stage="KEYFRAMES")
    assert why and "not allowed for stage KEYFRAMES" in why


def test_free_tier_and_unknown_providers_are_never_refused():
    c, _ = caps_with(daily=10**6, video=10**6)
    assert c.refusal("groq_whisper", Decimal("5"), stage="CAPTIONS", scope_id=uuid.uuid4()) is None
    assert c.refusal("not_configured", Decimal("5")) is None


def test_check_provider_budget_raises_a_hold_error():
    c, _ = caps_with(daily=100)
    with pytest.raises(BudgetExceededError) as ei:
        C.check_provider_budget("gemini_lyria", Decimal("1"), stage="MUSIC", caps=c)
    assert isinstance(ei.value, C.ProviderCapError) and "waits for you" in str(ei.value)
    C.configure(None)
    C.check_provider_budget("gemini_lyria", Decimal("1"), stage="MUSIC")  # nothing configured: no-op


def test_startup_check_names_providers_with_a_key_and_no_cap(tmp_path, caplog):
    (tmp_path / "providers.yaml").write_text(
        "providers:\n  - {id: a, secret_ref: KEY_A, enabled: true}\n  - {id: b, secret_ref: KEY_B, enabled: true}\n"
        "  - {id: c, secret_ref: KEY_C, enabled: true}\nproviderCaps:\n  b: {dailyInr: 5}\n", encoding="utf-8")
    env = {"KEY_A": "topsecret-a", "KEY_B": "topsecret-b"}
    with caplog.at_level("WARNING", logger="projectv.provider.caps"):
        assert C.warn_uncapped(tmp_path, env) == ["a"]
    assert "topsecret" not in caplog.text and "provider a " in caplog.text


def test_real_config_has_no_uncapped_keyed_provider():
    env = {"GEMINI_API_KEY": "x"}
    assert C.keys_without_cap(CONFIG, env) == []


def test_usd_limits_convert_with_the_configured_rate(tmp_path):
    (tmp_path / "providers.yaml").write_text("pricing: {fxInrPerUsd: 90}\nproviderCaps:\n  p: {dailyUsd: 2}\n", encoding="utf-8")
    assert C.load_caps(tmp_path)["p"].daily_inr == Decimal("180")


def test_daily_window_rolls_over_midnight_utc():
    c, spend = caps_with()
    c.clock = lambda: NOW + timedelta(hours=9)
    c.refusal("gemini_lyria", Decimal("1"), stage="MUSIC")
    assert spend.seen[-1][1] == datetime(2026, 11, 26, tzinfo=UTC)
