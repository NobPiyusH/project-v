"""Ports of ModelFallbackTest, OpenAiCompatChatTest, ClaudeChatTest (offline parts), AiCreditsFallbackTest, plus Llm tests.
No network, no spend: clients, ledger, guard and router are fakes."""

import socket
import time
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from studio.agents import model_fallback as mf
from studio.agents.aicredits import AiCreditsFallback, should_fall_back
from studio.agents.chat import ChatResult, ClaudeChat, OpenAiCompatChat, clean, handles_openai_compat
from studio.agents.llm import Llm
from studio.ledger import BudgetExceededError
from studio.ledger.budget_guard import AICREDITS
from studio.media.types import Usage
from studio.tests.test_pricing import at

# ---- ModelFallback


def test_retired_model_falls_back_to_the_next():
    tried = []

    def call(m):
        tried.append(m)
        if m == "gemini-2.5-flash":
            raise RuntimeError("404 models/gemini-2.5-flash is no longer available to new users")
        return "ok"

    a = mf.run(mf.models("gemini-2.5-flash", "gemini-3.6-flash"), 5, "test", call)
    assert a.model == "gemini-3.6-flash" and tried == ["gemini-2.5-flash", "gemini-3.6-flash"]


def test_a_hanging_model_falls_back_after_the_time_limit():
    def call(m):
        if m == "hangs":
            time.sleep(3)
        return m

    assert mf.run(["hangs", "fast"], 0.3, "test", call).model == "fast"


def test_billing_errors_do_not_hop_models():
    tried = []

    def call(m):
        tried.append(m)
        raise RuntimeError("403 PERMISSION_DENIED: billing account disabled")

    with pytest.raises(RuntimeError):
        mf.run(["a", "b"], 5, "test", call)
    assert tried == ["a"]


def test_last_model_timeout_is_reported():
    with pytest.raises(TimeoutError):
        mf.run(["only"], 0.2, "test", lambda m: time.sleep(2))


def test_lists_are_parsed_and_deduplicated():
    assert mf.models("a, b", "b,c,,") == ["a", "b", "c"]


def test_a_slow_paid_model_gets_its_own_time_limit():
    def call(m):
        if m.startswith("claude:"):
            time.sleep(0.5)
        return m

    a = mf.run(["claude:slow", "fast"], lambda m: 3 if m.startswith("claude:") else 0.1, "test", call)
    assert a.model == "claude:slow"


# ---- OpenAiCompatChat


def test_reasoning_and_fences_are_stripped_around_the_json():
    assert clean('<think>plan {x}</think>\n```json\n{"a":1}\n```') == '{"a":1}'


def test_only_prefixed_models_are_routed():
    assert handles_openai_compat("nvidia:nvidia/nemotron-3-ultra-550b-a55b")
    assert handles_openai_compat("groq:openai/gpt-oss-120b")
    assert not handles_openai_compat("gemini-3.5-flash")


def test_missing_key_is_a_model_problem_so_the_list_moves_on():
    chat = OpenAiCompatChat({"nvidia": ("http://127.0.0.1:1", "")})
    with pytest.raises(RuntimeError) as e:
        chat.call("nvidia:x", "s", "u", str)
    assert mf.is_model_problem(e.value)


def test_cut_off_or_broken_answers_count_as_model_problems():
    assert mf.is_model_problem(RuntimeError("unavailable: groq:x answer was cut off (token limit)"))
    assert mf.is_model_problem(RuntimeError("unavailable: groq:x returned an unreadable answer (X)"))


class Haiku(BaseModel):
    title: str
    lines: list[str]


def http_chat(body, status=200):
    seen = {}

    def handler(request: httpx.Request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = request.content.decode()
        return httpx.Response(status, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAiCompatChat({"groq": ("http://x/v1", "secret-key")}, client=client), seen


def test_structured_answer_is_parsed_with_usage():
    chat, seen = http_chat({
        "choices": [{"message": {"content": '<think>hm</think>{"title":"t","lines":["a","b","c"]}'}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    })
    r = chat.call("groq:m", "sys", "user", Haiku)
    assert r.entity.lines == ["a", "b", "c"] and (r.prompt_tokens, r.completion_tokens, r.total_tokens) == (10, 20, 30)
    assert seen["auth"] == "Bearer secret-key" and "JSON Schema" in seen["body"]


def test_length_finish_and_garbage_are_model_problems():
    chat, _ = http_chat({"choices": [{"message": {"content": '{"title":'}, "finish_reason": "length"}]})
    with pytest.raises(RuntimeError, match="cut off"):
        chat.call("groq:m", "s", "u", Haiku)
    chat, _ = http_chat({"choices": [{"message": {"content": "not json"}, "finish_reason": "stop"}]})
    with pytest.raises(RuntimeError, match="unreadable") as e:
        chat.call("groq:m", "s", "u", Haiku)
    assert mf.is_model_problem(e.value)


def test_claude_without_a_key_is_skipped_as_a_model_problem():
    none = ClaudeChat("")
    assert not none.configured()
    with pytest.raises(RuntimeError) as e:
        none.call("claude:x", "s", "u", Haiku)
    assert mf.is_model_problem(e.value)


def test_sonnet_cost_is_priced_from_config():
    p = at("2026-11-19")
    assert p.claude_inr(4_000, 3_000) == Decimal("3.5720")  # $0.038 at 94/$
    assert p.claude_monthly_cap_inr() == Decimal("752.0000")


# ---- AiCreditsFallback


class FakeChat:
    def __init__(self):
        self.calls = []

    def call(self, model, system, user, tp):
        self.calls.append(model)
        return ChatResult("fallback answer", 1_000, 2_000, 3_000)


class FakeLedger:
    def __init__(self):
        self.records = []

    def record(self, *a):
        self.records.append(a)


class FakeBudget:
    def __init__(self, can=True, gemini_blocked=False, claude_ok=True):
        self.can, self.gemini_blocked, self.claude_ok = can, gemini_blocked, claude_ok

    def can_spend_aicredits(self, x):
        return self.can

    def require(self, x, what):
        if self.gemini_blocked:
            raise BudgetExceededError("Monthly Gemini cap of ₹1000 would be exceeded by " + what)

    def can_spend_claude(self, x):
        return self.claude_ok


class FakeRouter:
    def __init__(self):
        self.recorded = []

    def record_external(self, *a):
        self.recorded.append(a)


CHAIN = ["gemini-3.6-flash", "groq:openai/gpt-oss-120b"]
OUTAGE = httpx.HTTPStatusError("503 model overloaded", request=httpx.Request("POST", "http://x"), response=httpx.Response(503))


def fb(active=True, can=True):
    chat, ledger, router = FakeChat(), FakeLedger(), FakeRouter()
    return AiCreditsFallback(chat, ledger, at("2026-11-19"), FakeBudget(can), router, active), chat, ledger, router


def test_a_503_retries_the_same_model_once_on_aicredits_and_writes_the_ledger():
    f, chat, ledger, router = fb()
    video = object()
    assert f.retry(video, "SCRIPT", CHAIN, OUTAGE, "sys", "user", str) == "fallback answer"
    assert chat.calls == ["aicredits:google/gemini-3.6-flash"]
    v, provider, amount, units, unit_type, estimated, note = ledger.records[0]
    assert (v, provider, units, unit_type, estimated) == (video, AICREDITS, Decimal(3000), "tokens", True)
    assert amount == at("2026-11-19").aicredits_inr("gemini-3.6-flash", 1_000, 2_000) and note.startswith("fallback: SCRIPT")
    assert router.recorded[0][1:4] == ("SCRIPT", "llm.text", AICREDITS) and router.recorded[0][4].startswith("fallback: gemini-3.6-flash failed")


def test_price_is_list_times_fx_times_markup_from_config():
    p = at("2026-11-19")
    assert p.aicredits_inr("gemini-3.6-flash", 1_000, 2_000).quantize(Decimal("0.01")) == Decimal("1.71")
    assert p.aicredits_cap_inr() == Decimal("150.0")
    assert p.aicredits_model_id("claude-sonnet-5-5") == "anthropic/claude-sonnet-5.5"


def test_claude_maps_to_the_same_claude_model_not_a_downgrade():
    f, chat, *_ = fb()
    f.retry(None, "SHOT_PLAN", ["claude:claude-sonnet-5-5", "gemini-3.6-flash"], OUTAGE, "s", "u", str)
    assert chat.calls == ["aicredits:anthropic/claude-sonnet-5.5"]


def test_gemini_cap_rejection_falls_back():
    f, chat, ledger, _ = fb()
    cap = BudgetExceededError("Monthly Gemini cap of ₹1000 would be exceeded by SCRIPT")
    assert f.retry(None, "SCRIPT", CHAIN, cap, "s", "u", str) == "fallback answer"
    assert chat.calls == ["aicredits:google/gemini-3.6-flash"] and ledger.records


def test_claude_cap_rejection_falls_back_to_the_same_claude_model():
    f, chat, *_ = fb()
    cap = RuntimeError("unavailable: Claude monthly cap reached")
    f.retry(None, "SHOT_PLAN", ["claude:claude-sonnet-5-5", "gemini-3.6-flash"], cap, "s", "u", str)
    assert chat.calls == ["aicredits:anthropic/claude-sonnet-5.5"]


@pytest.mark.parametrize("failure,can", [
    (BudgetExceededError("Monthly Gemini cap of ₹1000 would be exceeded"), False),  # fallback cap used up
    (BudgetExceededError("autopilot daily cap of ₹50 reached"), True),  # never worked around
    (httpx.HTTPStatusError("400 invalid argument", request=httpx.Request("POST", "http://x"), response=httpx.Response(400)), True),
    (RuntimeError("blocked: SAFETY"), True),
    (OUTAGE, False),
])
def test_cases_that_must_not_call_aicredits(failure, can):
    f, chat, ledger, router = fb(can=can)
    assert f.retry(None, "SCRIPT", CHAIN, failure, "s", "u", str) is None
    assert not chat.calls and not ledger.records and not router.recorded


def test_outages_are_recognised():
    assert should_fall_back(OUTAGE)
    assert should_fall_back(TimeoutError())
    assert should_fall_back(RuntimeError("429 RESOURCE_EXHAUSTED: rate limit"))
    try:
        raise RuntimeError("x") from socket.gaierror("no host")
    except RuntimeError as e:
        assert should_fall_back(e)
    assert not should_fall_back(RuntimeError("404 model not found"))


def test_off_when_disabled_and_no_equivalent_model_means_no_fallback():
    f, chat, *_ = fb(active=False)
    assert f.retry(None, "SCRIPT", CHAIN, OUTAGE, "s", "u", str) is None
    f, chat, *_ = fb()
    assert f.retry(None, "SCRIPT", ["groq:openai/gpt-oss-120b", "gemini-9-unknown"], OUTAGE, "s", "u", str) is None
    assert not chat.calls
    assert not AiCreditsFallback.build(SimpleNamespace(aicredits_base_url="http://x", aicredits_api_key="", aicredits_fallback_enabled=True),
                                       None, None, None, None).active()


# ---- Llm (new: no Java test; behaviour read from Llm.java)


def make_llm(gemini, *, claude=None, budget=None, models="m1", fallbacks="", aic=None):
    ledger = FakeLedger()
    claude = claude or SimpleNamespace(configured=lambda: False)
    f = aic or fb(active=False)[0]
    llm = Llm(ledger, at("2026-11-19"), budget or FakeBudget(), FakeChat(), claude, f, gemini, timeout_seconds=2, model=models,
              fallbacks=fallbacks)
    return llm, ledger


def test_gemini_call_is_booked_with_thinking_tokens_as_output():
    llm, ledger = make_llm(lambda m, s, u, t: ("answer", Usage(1000, 500, 200, 1700)))
    a = llm.call_with_model("v", "SCRIPT", "s", "u", str)
    assert (a.entity, a.model) == ("answer", "m1")
    _, provider, amount, units, _, estimated, note = ledger.records[0]
    assert provider == "gemini_api" and units == Decimal(1700) and estimated is True and note == "SCRIPT (m1)"
    assert amount == at("2026-11-19").text_inr(Usage(1000, 500, 200, 1700))


def test_free_backup_models_are_booked_at_zero():
    def gemini(m, s, u, t):
        raise RuntimeError("503 overloaded")

    llm, ledger = make_llm(gemini, fallbacks="groq:x")
    llm._nvidia = SimpleNamespace(call=lambda m, s, u, t: ChatResult("g", 5, 5, 10))
    assert llm.call_with_model("v", "SCRIPT", "s", "u", str).model == "groq:x"
    assert ledger.records[0][1:3] == ("groq", Decimal(0))


def test_claude_preferred_model_is_used_booked_once_and_skipped_without_a_key():
    sent = []
    claude = SimpleNamespace(
        configured=lambda: True,
        call=lambda m, s, u, t, effort, on_usage: (on_usage(1000, 2000), SimpleNamespace(entity="c", input_tokens=1000, output_tokens=2000))[1],
    )
    llm, ledger = make_llm(lambda *a: sent.append(a), claude=claude)
    a = llm.call_with_model("v", "PLAN", "s", "u", str, "claude:claude-sonnet-5-5")
    assert a.model == "claude:claude-sonnet-5-5" and not sent and len(ledger.records) == 1
    assert ledger.records[0][1] == "anthropic" and ledger.records[0][5] is False
    llm2, _ = make_llm(lambda m, s, u, t: ("g", Usage(1, 1, 0, 2)))
    assert llm2.call_with_model("v", "PLAN", "s", "u", str, "claude:claude-sonnet-5-5").model == "m1"


def test_a_capped_gemini_budget_goes_to_aicredits_when_enabled_else_raises():
    llm, _ = make_llm(lambda *a: None, budget=FakeBudget(gemini_blocked=True))
    with pytest.raises(BudgetExceededError):
        llm.call_with_model("v", "SCRIPT", "s", "u", str)
    f, chat, *_ = fb()
    llm, _ = make_llm(lambda *a: None, budget=FakeBudget(gemini_blocked=True), aic=f, models="gemini-3.6-flash")
    a = llm.call_with_model("v", "SCRIPT", "s", "u", str)
    assert (a.entity, a.model) == ("fallback answer", "aicredits")


def test_effort_overrides():
    llm, _ = make_llm(None)
    llm._effort_overrides = "SHOT_PLAN:low, dream.scout:HIGH"
    assert llm.effort_for("shot_plan") == "low" and llm.effort_for("dream.scout") == "high" and llm.effort_for("PLAN") == "medium"


def test_all_models_failing_raises_a_clear_error_naming_the_purpose():
    def gemini(m, s, u, t):
        raise RuntimeError("500 boom")

    llm, _ = make_llm(gemini)
    with pytest.raises(RuntimeError, match="SCRIPT failed: RuntimeError: 500 boom"):
        llm.call_with_model("v", "SCRIPT", "s", "u", str)
