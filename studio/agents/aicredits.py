"""Fallback for text calls via AICredits.in (the owner's prepaid INR wallet, 19 Nov 2026). Port of AiCreditsFallback.

Not a router provider: when the Gemini/Claude chain fails with an outage (5xx, 429, timeout, connection error) or is
refused by the Gemini/Claude monthly cap, the same call is retried ONCE on the same model through AICredits. Never on
4xx validation/content errors or the autopilot daily cap. Off unless enabled and a key is set; all fallback spend is
capped (pricing.aicredits.totalCapInr) from the cost ledger.
"""

from __future__ import annotations

import logging
import re
import socket
from decimal import Decimal
from typing import Any

import httpx

from studio.agents.chat import CLAUDE_PREFIX, OpenAiCompatChat, handles_claude, handles_openai_compat
from studio.ledger import BudgetExceededError, BudgetGuard, CostLedger, Pricing, VideoBudgetExceededError
from studio.ledger.budget_guard import AICREDITS

log = logging.getLogger(__name__)

_OUTAGE_STATUS = re.compile(r"\b(429|5\d\d)\b")
CALL_ESTIMATE_INR = Decimal(10)  # the most one capped call can cost (Sonnet, 8k output)


def _chain(t: BaseException | None):
    seen = set()
    while t is not None and id(t) not in seen:
        seen.add(id(t))
        yield t
        t = t.__cause__ or t.__context__


def should_fall_back(failure: BaseException) -> bool:
    """True for provider outages and the Gemini/Claude monthly-cap rejections; False for 4xx validation and content
    errors, and for the autopilot daily cap (never worked around)."""
    if any("daily cap" in str(c).lower() for c in _chain(failure)):
        return False
    if any(isinstance(c, VideoBudgetExceededError) for c in _chain(failure)):
        return False  # the video's own cap: a second paid route would spend past it
    for c in _chain(failure):
        if isinstance(c, BudgetExceededError) or "cap reached" in str(c).lower():
            return True
    for c in _chain(failure):
        if isinstance(c, (TimeoutError, socket.timeout, ConnectionError, socket.gaierror, httpx.TimeoutException, httpx.ConnectError)):
            return True
        if isinstance(c, httpx.HTTPStatusError) and (c.response.status_code == 429 or c.response.status_code >= 500):
            return True
        msg = str(c).lower()
        if _OUTAGE_STATUS.search(msg) or any(w in msg for w in ("overloaded", "timed out", "connection refused", "connection reset")):
            return True
    return False


class AiCreditsFallback:
    def __init__(
        self, chat: OpenAiCompatChat, ledger: CostLedger, pricing: Pricing, budget: BudgetGuard, router: Any, active: bool,
    ):
        self._chat, self._ledger, self._pricing, self._budget, self._router = chat, ledger, pricing, budget, router
        self._enabled = active

    @staticmethod
    def build(settings: Any, ledger: CostLedger, pricing: Pricing, budget: BudgetGuard, router: Any) -> AiCreditsFallback:
        chat = OpenAiCompatChat({"aicredits": (settings.aicredits_base_url, settings.aicredits_api_key)})
        active = settings.aicredits_fallback_enabled and bool(settings.aicredits_api_key.strip())
        return AiCreditsFallback(chat, ledger, pricing, budget, router, active)

    def active(self) -> bool:
        return self._enabled

    def primary(self, models: list[str]) -> str | None:
        """The first Gemini or Claude model in the list with an AICredits equivalent, or None."""
        for m in models:
            if handles_openai_compat(m):
                continue
            model_id = m[len(CLAUDE_PREFIX) :] if handles_claude(m) else m
            if self._pricing.aicredits_model_id(model_id) is None:
                continue
            return model_id
        return None

    def retry(self, video_id, purpose: str, models: list[str], failure: BaseException, system: str, user: str, tp: Any):
        """Repeats a failed text call once on AICredits. Returns None (caller re-raises the original error) when off, not an
        outage or cap refusal, no equivalent model, or the fallback cap is used up. Records a CostEntry and a RoutingDecision."""
        if not self._enabled or not should_fall_back(failure):
            return None
        primary = self.primary(models)
        if primary is None:
            return None
        if not self._budget.can_spend_aicredits(CALL_ESTIMATE_INR):
            log.warning("%s: AICredits fallback cap of ₹%s reached; not falling back", purpose, self._pricing.aicredits_cap_inr())
            return None
        aic_id = self._pricing.aicredits_model_id(primary)
        first = (str(failure).splitlines() or [""])[0]
        reason = f"fallback: {primary} failed ({first}); retried once on AICredits {aic_id}"
        self._router.record_external(
            video_id, purpose, "llm.text", AICREDITS, reason,
            [{"providerId": primary, "failed": True}, {"providerId": AICREDITS, "model": aic_id}],
        )
        log.warning("%s: %s", purpose, reason)
        r = self._chat.call(f"aicredits:{aic_id}", system, user, tp)
        self._ledger.record(
            video_id, AICREDITS, self._pricing.aicredits_inr(primary, r.prompt_tokens, r.completion_tokens),
            Decimal(r.total_tokens), "tokens", True,
            f"fallback: {purpose} ({aic_id} for {primary}, {r.prompt_tokens} in / {r.completion_tokens} out)",
        )
        return r.entity
