"""Text-model clients: OpenAI-compatible APIs (NVIDIA NIM, Groq, OpenRouter, AICredits) and Claude. Ports of OpenAiCompatChat and ClaudeChat.

Structured output: format instructions (a JSON schema) in the prompt, JSON parsed back with pydantic, since these models
do not all support a JSON-schema parameter. A cut-off or unreadable answer is the model's problem: the message starts
with "unavailable" so the fallback list moves on to the next model.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Generic, TypeVar

import httpx
from pydantic import TypeAdapter

from studio.ledger.pricing import dec

T = TypeVar("T")

CLAUDE_PREFIX = "claude:"
CLAUDE_MAX_TOKENS = 16_000  # hard ceiling per call (output + thinking)


@dataclass
class ChatResult(Generic[T]):
    entity: T
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class ClaudeResult(Generic[T]):
    entity: T
    model: str
    input_tokens: int
    output_tokens: int


def clean(text: str) -> str:
    """Drops reasoning blocks and text around the JSON object some models add."""
    s = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    a, b = s.find("{"), s.rfind("}")
    return s[a : b + 1] if a >= 0 and b > a else s


def format_instructions(tp: Any) -> str:
    schema = TypeAdapter(tp).json_schema()
    return (
        "Your response should be in JSON format.\n"
        "Do not include any explanations, only provide a RFC8259 compliant JSON response following this format "
        "without deviation.\nDo not include markdown code blocks in your response.\n"
        "Remove the ```json markdown from the output.\nHere is the JSON Schema instance your output must adhere to:\n"
        f"```{json.dumps(schema)}```\n"
    )


def parse(tp: Any, text: str) -> Any:
    """Parses a model answer into tp. A plain `str` type returns the text unchanged."""
    if tp is str:
        return text
    return TypeAdapter(tp).validate_json(clean(text))


def handles_openai_compat(model: str | None) -> bool:
    """True for nvidia:..., groq:..., openrouter:... and openai:... model ids."""
    return model is not None and model.startswith(("nvidia:", "groq:", "openrouter:", "openai:"))


def is_openai(model: str | None) -> bool:
    """True for paid OpenAI model ids (openai:...); the other providers here are free tiers."""
    return model is not None and model.startswith("openai:")


def handles_claude(model: str | None) -> bool:
    return model is not None and model.startswith(CLAUDE_PREFIX)


def provider_of(model: str) -> str:
    return model[: model.index(":")]


class OpenAiCompatChat:
    def __init__(self, providers: dict[str, tuple[str, str]], client: httpx.Client | None = None, *,
                 openai_in_per_m: float = 0.0, openai_out_per_m: float = 0.0, inr_per_usd: float = 90.0):
        """providers: name -> (base_url, api_key). `client` is injectable for tests. The OpenAI list prices (USD per 1M tokens) and
        the rupee rate are for the cost ledger (owner-paid, recorded as a real cost)."""
        self._providers = providers
        self._openai_in, self._openai_out, self._inr_per_usd = openai_in_per_m, openai_out_per_m, inr_per_usd
        self._http = client or httpx.Client(timeout=httpx.Timeout(300, connect=15))  # ModelFallback's limit is the real one

    def call(self, model: str, system: str, user: str, tp: Any) -> ChatResult:
        base_url, key = self._providers.get(provider_of(model), ("", ""))
        if not key or not key.strip():
            # reported as a model problem so the fallback list moves on
            raise RuntimeError(f"not supported: no API key for {provider_of(model)}")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user + "\n\n" + format_instructions(tp)},
        ]
        name = model[model.index(":") + 1 :]
        if is_openai(model):
            # OpenAI's GPT-5 family takes max_completion_tokens (reasoning included) and rejects a custom temperature.
            body = {"model": name, "max_completion_tokens": 16000, "reasoning_effort": "low", "messages": messages}
        else:
            body = {"model": name, "temperature": 0.6, "max_tokens": 8192, "messages": messages}
        resp = self._http.post(base_url + "/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body)  # key never logged
        resp.raise_for_status()
        r = resp.json()
        choices = r.get("choices") or []
        if not choices:
            raise RuntimeError("unavailable: the provider returned no choices")
        choice = choices[0]
        text = (choice.get("message") or {}).get("content")
        if not text or not text.strip():
            raise RuntimeError("unavailable: the provider returned an empty answer")
        if choice.get("finish_reason") == "length":
            raise RuntimeError(f"unavailable: {model} answer was cut off (token limit)")
        try:
            entity = parse(tp, text)
        except Exception as e:
            raise RuntimeError(f"unavailable: {model} returned an unreadable answer ({type(e).__name__})") from e
        u = r.get("usage") or {}
        p, c = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        t = int(u["total_tokens"]) if "total_tokens" in u else p + c
        return ChatResult(entity, p, c, t)


    def openai_inr(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        """Rupee cost of one OpenAI call at the configured list prices."""
        usd = (prompt_tokens * self._openai_in + completion_tokens * self._openai_out) / 1_000_000.0
        return dec(usd * self._inr_per_usd).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


class ClaudeChat:
    """Claude via the official Anthropic SDK (imported lazily: not needed unless a key is set).

    NOTE: not exercised against the live API in this port (that spends the owner's money); the Java version was.
    """

    def __init__(self, api_key: str, client: Any | None = None):
        self._client = client
        if client is None and api_key and api_key.strip():
            import anthropic

            self._client = anthropic.Anthropic(api_key=api_key, timeout=170, max_retries=1)

    def configured(self) -> bool:
        return self._client is not None

    def send(self, **params: Any) -> Any:
        """A raw request (e.g. with server-side web search); the caller records the cost from the response's usage."""
        if self._client is None:
            raise RuntimeError("not supported: no ANTHROPIC_API_KEY configured")
        return self._client.messages.create(**params)

    def call(
        self, model: str, system: str, user: str, tp: Any, effort: str = "medium",
        on_usage: Callable[[int, int], None] = lambda i, o: None,
    ) -> ClaudeResult:
        if self._client is None:
            raise RuntimeError("not supported: no ANTHROPIC_API_KEY configured")
        response = self._client.messages.create(
            model=model[len(CLAUDE_PREFIX) :],
            max_tokens=CLAUDE_MAX_TOKENS,
            output_config={"effort": effort},
            system=system,
            messages=[{"role": "user", "content": user + "\n\n" + format_instructions(tp)}],
        )
        text = "\n".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        in_tokens, out_tokens = response.usage.input_tokens, response.usage.output_tokens
        on_usage(in_tokens, out_tokens)  # a paid call is always recorded, even if the answer cannot be used
        if "max_tokens" in str(response.stop_reason or "").lower():
            raise RuntimeError(f"unavailable: {model} answer was cut off (token limit); tokens {in_tokens}/{out_tokens}")
        if not text.strip():
            raise RuntimeError(f"unavailable: {model} returned no text")
        try:
            return ClaudeResult(parse(tp, text), model, in_tokens, out_tokens)
        except Exception as e:
            raise RuntimeError(f"unavailable: {model} returned an unreadable answer ({type(e).__name__})") from e
