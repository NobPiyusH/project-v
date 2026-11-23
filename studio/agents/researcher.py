"""Backup researcher (added 17 Nov 2026 after Gemini's Google Search grounding stalled a video for 30+ minutes). Port of ClaudeResearcher.

Claude with Anthropic's server-side web search. Same output as the Gemini researcher: one fact per line, each tied to the web
pages Claude cited for it. Used only when the Gemini research activity has failed; its cost (tokens and searches) is recorded
the moment the answer arrives and counts against the owner's Claude cap.

Response blocks are read as the Anthropic Python SDK returns them (objects with .type, .text, .citations, .content). NOT
exercised against the live API in this port (that spends the owner's money); parse() is tested with stand-in objects.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from studio.agents.chat import CLAUDE_MAX_TOKENS, CLAUDE_PREFIX, ClaudeChat, handles_claude
from studio.ledger import BudgetGuard, CostLedger, Pricing
from studio.ledger.budget_guard import CLAUDE
from studio.media.types import Fact, ResearchRequest, ResearchResult, Source, Usage

MAX_SEARCHES = 5
ESTIMATE_INR = Decimal(15)  # upper bound to check the Claude cap before calling: ~40k tokens of results + ~2k out + 5 searches
_FACT = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+)$", re.MULTILINE)  # "- ", "* ", "• " or "1. " bullets (models vary)
_SYSTEM = "You are a careful science researcher for a premium documentary studio. Accuracy over drama."


class ClaudeResearcher:
    def __init__(self, claude: ClaudeChat, ledger: CostLedger, budget: BudgetGuard, pricing: Pricing,
                 model: str = "claude:claude-sonnet-5-5"):
        self._claude, self._ledger, self._budget, self._pricing, self._model = claude, ledger, budget, pricing, model

    def research(self, video_id, req: ResearchRequest) -> ResearchResult:
        if not self._claude.configured() or not handles_claude(self._model):
            raise RuntimeError("unavailable: no Claude backup researcher configured")
        if not self._budget.can_spend_claude(ESTIMATE_INR):
            raise RuntimeError("unavailable: Claude monthly cap reached")
        must = ", ".join(req.must_include) if req.must_include else "none"
        owner = f"Owner notes: {req.owner_notes}" if req.owner_notes and req.owner_notes.strip() else ""
        prompt = f"""Research this topic for a documentary video script. Use web search.
Topic: {req.topic}
Video summary: {req.summary or ""}
Must include: {must}
{owner}
Write {req.max_facts} concise, verifiable facts, one per line, each starting with "- ".
Prefer surprising, visual, emotionally powerful facts with concrete numbers, scales and time spans.
Only state facts that your search results support. No introduction, no conclusion."""
        params: dict[str, Any] = {
            "model": self._model[len(CLAUDE_PREFIX) :],
            "max_tokens": CLAUDE_MAX_TOKENS,
            "output_config": {"effort": "medium"},
            "system": _SYSTEM,
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}],
            "messages": [{"role": "user", "content": prompt}],
        }
        response = self._claude.send(**params)
        turns = [response]
        self._record(video_id, response)
        # A long server-side search turn can pause; continue it once with the partial answer.
        if response.stop_reason == "pause_turn":
            params["messages"] = [*params["messages"], {"role": "assistant", "content": response.content}]
            response = self._claude.send(**params)
            turns.append(response)
            self._record(video_id, response)
        return parse(turns, self._model)

    def _record(self, video_id, m: Any) -> None:
        i, o = m.usage.input_tokens, m.usage.output_tokens
        stu = getattr(m.usage, "server_tool_use", None)
        searches = (getattr(stu, "web_search_requests", 0) or 0) if stu is not None else 0
        self._ledger.record(
            video_id, CLAUDE, self._pricing.claude_inr(i, o) + self._pricing.claude_search_inr(searches), Decimal(i + o),
            "tokens", False, f"RESEARCH backup ({self._model}, {searches} web searches)",
        )


def parse(turns: list[Any], model: str) -> ResearchResult:
    """Facts from the "- " lines; each fact's sources are the pages cited by the text blocks it overlaps."""
    text = ""
    spans: list[tuple[int, int]] = []
    span_sources: list[list[Source]] = []
    all_sources: dict[str, Source] = {}
    tokens_in = tokens_out = 0
    for m in turns:
        tokens_in += m.usage.input_tokens
        tokens_out += m.usage.output_tokens
        for b in m.content:
            kind = getattr(b, "type", None)
            if kind == "web_search_tool_result" and isinstance(b.content, list):
                for r in b.content:
                    all_sources.setdefault(r.url, Source(r.title, r.url))
            elif kind == "text":
                start = len(text)
                text += b.text
                cited = [
                    Source(getattr(c, "title", None) or c.url, c.url)
                    for c in (getattr(b, "citations", None) or [])
                    if getattr(c, "type", None) == "web_search_result_location"
                ]
                spans.append((start, len(text)))
                span_sources.append(cited)
    facts: list[Fact] = []
    cited_urls: dict[str, None] = {}
    for mt in _FACT.finditer(text):
        sources: dict[str, Source] = {}
        for (s, e), cited in zip(spans, span_sources, strict=True):
            if s < mt.end(1) and e > mt.start(1):
                for src in cited:
                    sources.setdefault(src.uri, src)
        cited_urls.update(dict.fromkeys(sources))
        facts.append(Fact(mt.group(1).strip(), list(sources.values())))
    if not facts:
        raise RuntimeError(f"unavailable: {model} returned no facts")
    # Cited pages first, then the rest of what the searches found.
    sources = [all_sources.get(u, Source(u, u)) for u in cited_urls]
    sources += [s for s in all_sources.values() if s.uri not in cited_urls]
    return ResearchResult(facts, sources, [], model, Usage(tokens_in, tokens_out, 0, tokens_in + tokens_out))
