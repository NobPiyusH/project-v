"""One place for structured LLM calls by the agents: time limit, budget check, and a CostEntry for every call. Port of Llm.

Model ids: `claude:<model>` goes to Claude, `nvidia:`/`groq:`/`openrouter:` to the OpenAI-compatible backups, anything
else to Gemini. The Gemini call is injected (`gemini_call`) so it can be faked in tests; `gemini_client()` builds the real
one on google-genai (NOT exercised live in this port: that spends the owner's money).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from studio.agents import model_fallback
from studio.agents.aicredits import AiCreditsFallback
from studio.agents.chat import (
    ClaudeChat,
    OpenAiCompatChat,
    format_instructions,
    handles_claude,
    handles_openai_compat,
    is_openai,
    parse,
    provider_of,
)
from studio.ledger import BudgetExceededError, BudgetGuard, CostLedger, Pricing
from studio.ledger.budget_guard import CLAUDE, GEMINI
from studio.media.types import Usage

# (model, system, user, type) -> (entity, usage)
GeminiCall = Callable[[str, str, str, Any], tuple[Any, Usage]]


@dataclass
class Answer:
    """An answer plus the model that produced it ('aicredits' when the AICredits fallback answered)."""

    entity: Any
    model: str


def gemini_client(api_key: str) -> GeminiCall:
    """Real Gemini call via google-genai (structured output through the same prompt-schema route as the other backups)."""

    def call(model: str, system: str, user: str, tp: Any) -> tuple[Any, Usage]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        r = client.models.generate_content(
            model=model, contents=user + "\n\n" + format_instructions(tp),
            config=types.GenerateContentConfig(system_instruction=system),
        )
        md = r.usage_metadata
        prompt, completion = (md.prompt_token_count or 0), (md.candidates_token_count or 0)
        total = md.total_token_count or prompt + completion
        usage = Usage(prompt, completion, max(0, total - prompt - completion), total)  # thinking = the remainder
        return parse(tp, r.text or ""), usage

    return call


class Llm:
    def __init__(
        self, ledger: CostLedger, pricing: Pricing, budget: BudgetGuard, nvidia: OpenAiCompatChat, claude: ClaudeChat,
        aicredits: AiCreditsFallback, gemini_call: GeminiCall | None, *, timeout_seconds: float = 75, model: str,
        fallbacks: str = "", effort_overrides: str = "",
    ):
        self._ledger, self._pricing, self._budget = ledger, pricing, budget
        self._nvidia, self._claude, self._aicredits = nvidia, claude, aicredits
        self._gemini = gemini_call
        self._timeout = timeout_seconds
        self._model = model
        self._fallbacks = fallbacks
        self._effort_overrides = effort_overrides

    def model(self) -> str:
        return self._model

    def call(self, video_id, purpose, system, user, tp, preferred: str | None = None):
        return self.call_with_model(video_id, purpose, system, user, tp, preferred).entity

    def effort_for(self, purpose: str) -> str:
        """Claude thinking effort per call purpose. Default medium everywhere (A/B-tested); the owner can set e.g.
        CLAUDE_EFFORT_OVERRIDES=SHOT_PLAN:low,dream.scout:low to A/B a cheaper setting (quality rule)."""
        for pair in self._effort_overrides.split(","):
            kv = pair.strip().split(":")
            if len(kv) == 2 and kv[0].strip().lower() == purpose.lower():
                return kv[1].strip().lower()
        return "medium"

    def call_with_model(self, video_id, purpose: str, system: str, user: str, tp, preferred: str | None = None) -> Answer:
        """`preferred` is a model tried before the usual list (e.g. claude:claude-sonnet-5-5 for the script and shot
        plan); skipped when it is not configured or its monthly cap is used up."""
        use_preferred = bool(preferred and preferred.strip()) and (not handles_claude(preferred) or self._claude.configured())
        models = model_fallback.models(f"{preferred},{self._model}" if use_preferred else self._model, self._fallbacks)
        try:
            self._budget.require(Decimal(1), purpose)  # a text call costs well under ₹1; refuse only when the cap is hit
        except BudgetExceededError as e:
            # Owner (19 Nov 2026): a capped text call may go to the AICredits fallback (its own cap), if enabled.
            fb = self._aicredits.retry(video_id, purpose, models, e, system, user, tp)
            if fb is not None:
                return Answer(fb, "aicredits")
            raise

        def timeout_for(m: str) -> float:
            return max(self._timeout, 180) if handles_claude(m) else self._timeout

        def one(m: str) -> tuple[Any, Usage]:
            if handles_claude(m):
                # Owner-funded: stop at the monthly cap and let Gemini do it (about ₹10 = the most a capped call costs).
                if not self._budget.can_spend_claude(Decimal(10)):
                    raise RuntimeError("unavailable: Claude monthly cap reached")

                def book(i: int, o: int) -> None:
                    self._ledger.record(
                        video_id, CLAUDE, self._pricing.claude_inr(i, o), Decimal(i + o), "tokens", False,
                        f"{purpose} ({m}, {i} in / {o} out)",
                    )

                c = self._claude.call(m, system, user, tp, self.effort_for(purpose), book)
                return c.entity, Usage(c.input_tokens, c.output_tokens, 0, c.input_tokens + c.output_tokens)
            if handles_openai_compat(m):
                n = self._nvidia.call(m, system, user, tp)
                return n.entity, Usage(n.prompt_tokens, n.completion_tokens, 0, n.total_tokens)
            if self._gemini is None:
                raise RuntimeError("not supported: no Gemini client configured")
            return self._gemini(m, system, user, tp)

        try:
            a = model_fallback.run(models, timeout_for, purpose, one)
        except TimeoutError as e:
            fb = self._aicredits.retry(video_id, purpose, models, e, system, user, tp)
            if fb is not None:
                return Answer(fb, "aicredits")
            raise RuntimeError(f"{purpose}: no model in {models} answered within {self._timeout}s") from e
        except Exception as e:
            # Outage only (5xx, 429, timeout, connection): one retry of the same model via AICredits, if enabled.
            fb = self._aicredits.retry(video_id, purpose, models, e, system, user, tp)
            if fb is not None:
                return Answer(fb, "aicredits")
            if isinstance(e, BudgetExceededError):
                raise
            raise RuntimeError(f"{purpose} failed: {type(e).__name__}: {e}") from e
        entity, usage = a.value
        if handles_claude(a.model):
            pass  # already recorded when the response arrived (a paid call is recorded even if it could not be used)
        elif is_openai(a.model):
            # OpenAI is owner-paid: record the real cost at list price (CLAUDE.md rule 6).
            self._ledger.record(
                video_id, "openai", self._nvidia.openai_inr(usage.prompt_tokens, usage.output_tokens), Decimal(usage.total_tokens),
                "tokens", True, f"{purpose} ({a.model})",
            )
        elif handles_openai_compat(a.model):
            # NVIDIA NIM and Groq free tiers: record the call (₹0) so every call stays in the ledger.
            self._ledger.record(
                video_id, provider_of(a.model), Decimal(0), Decimal(usage.total_tokens), "tokens", True, f"{purpose} ({a.model})"
            )
        else:
            self._ledger.record(
                video_id, GEMINI, self._pricing.text_inr(usage), Decimal(usage.total_tokens), "tokens", True,
                f"{purpose} ({a.model})",
            )
        if entity is None:
            raise RuntimeError(f"{purpose}: the model returned no parsable result")
        return Answer(entity, a.model)
