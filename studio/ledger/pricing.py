"""Prices from config/providers.yaml (`pricing:`). Port of com.projectv.ledger.Pricing (same rounding: 4 places, HALF_UP)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable

import yaml

_FOUR = Decimal("0.0001")


def dec(x: float | int) -> Decimal:
    """Java's BigDecimal.valueOf(double): the shortest decimal text of the double."""
    return Decimal(repr(float(x)))


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    output_tokens: int
    thought_tokens: int
    total_tokens: int


class Pricing:
    def __init__(self, config_dir: str | Path, today: Callable[[], date] | None = None):
        with open(Path(config_dir) / "providers.yaml", encoding="utf-8") as f:
            root = yaml.safe_load(f)
        pricing = root.get("pricing")
        if pricing is None:
            raise RuntimeError("providers.yaml has no pricing section")
        self._pricing: dict[str, Any] = pricing
        self._today = today or (lambda: datetime.now(UTC).date())

    # ---- helpers

    @staticmethod
    def _map(m: dict[str, Any], key: str) -> dict[str, Any]:
        v = m.get(key)
        if not isinstance(v, dict):
            raise RuntimeError(f"Missing pricing key: {key}")
        return v

    @staticmethod
    def _num(m: dict[str, Any], key: str) -> float:
        v = m.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise RuntimeError(f"Missing pricing number: {key}")
        return float(v)

    def _inr(self, usd: float) -> Decimal:
        return (dec(usd * self.fx())).quantize(_FOUR, rounding=ROUND_HALF_UP)

    def _gemini(self) -> dict[str, Any]:
        """Gemini prices in force today: the base section with every announced change whose date has come laid over it."""
        base = self._map(self._pricing, "gemini")
        changes = base.get("priceChanges")
        if not isinstance(changes, list) or not changes:
            return base
        today = self._today()
        out = dict(base)
        for c in changes:
            if today >= date.fromisoformat(str(c["from"])):
                out.update({k: v for k, v in c.items() if k != "from"})
        return out

    def _calibrated(self, inr: Decimal) -> Decimal:
        c = self._gemini().get("calibration")
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            return inr * dec(c)
        return inr

    def _tokens_inr(self, rates: dict[str, Any], u: Usage | None) -> Decimal:
        if u is None:
            return Decimal(0)
        # Thinking tokens are billed as output tokens.
        usd = (
            u.prompt_tokens / 1e6 * self._num(rates, "inputPerMTokUsd")
            + (u.output_tokens + u.thought_tokens) / 1e6 * self._num(rates, "outputPerMTokUsd")
        )
        return self._inr(usd)

    # ---- general

    def fx(self) -> float:
        return self._num(self._pricing, "fxInrPerUsd")

    # ---- Gemini

    def gemini_monthly_cap_inr(self) -> Decimal:
        return dec(self._num(self._gemini(), "monthlyCapInr"))

    def gemini_reserve_inr(self) -> Decimal:
        return dec(self._num(self._gemini(), "reserveInr"))

    def text_inr(self, u: Usage | None) -> Decimal:
        return self._calibrated(self._tokens_inr(self._map(self._gemini(), "text"), u))

    def tts_inr(self, u: Usage | None) -> Decimal:
        return self._calibrated(self._tokens_inr(self._map(self._gemini(), "tts"), u))

    def image_inr(self, size: str) -> Decimal:
        per = self._map(self._map(self._gemini(), "image"), "perImageUsd")
        v = per.get(size, per.get("1K"))
        return self._calibrated(self._inr(float(v)))

    def music_inr(self, songs: int) -> Decimal:
        return self._calibrated(self._inr(self._num(self._map(self._gemini(), "music"), "perSongUsd") * songs))

    def grounding_inr(self, queries: int) -> Decimal:
        return self._calibrated(self._inr(self._num(self._gemini(), "groundingPerQueryUsd") * queries))

    def omni_inr(self, seconds: float, resolution: str) -> Decimal:
        o = self._map(self._gemini(), "omni")
        return self.omni_tokens_inr(int(self._num(self._map(o, "tokensPerSecond"), resolution) * seconds))

    def omni_tokens_inr(self, output_tokens: int) -> Decimal:
        return self._calibrated(
            self._inr(self._num(self._map(self._gemini(), "omni"), "outputPerMTokUsd") * output_tokens / 1_000_000.0)
        )

    # ---- Modal / GPU

    def modal_gpu_inr(self, gpu: str, seconds: float) -> Decimal:
        modal = self._map(self._pricing, "modal")
        return self._inr(self._num(self._map(modal, "gpuPerSecondUsd"), gpu) * seconds)

    def zimage_inr(self, images: int) -> Decimal:
        return self.modal_gpu_inr("L40S", self._num(self._map(self._pricing, "modal"), "zimageSecondsPerImage") * images)

    def gpu_monthly_cap_inr(self) -> Decimal:
        return self._inr(self._num(self._map(self._pricing, "modal"), "monthlyCapUsd"))

    def modal_monthly_free_usd(self) -> float:
        return self._num(self._map(self._pricing, "modal"), "monthlyFreeUsd")

    def wan_first_clip_extra_seconds(self) -> float:
        v = self._map(self._pricing, "modal").get("wanFirstClipExtraSeconds")
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0

    def wan_seconds_per_81_frames(self, kind: str) -> float:
        return self._num(self._map(self._map(self._pricing, "modal"), "wanSecondsPer81Frames"), kind)

    def wan_redo_hold_seconds(self) -> int:
        v = self._map(self._pricing, "modal").get("wanRedoHoldSeconds")
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    def gpu_container_start_seconds(self) -> float:
        return self._num(self._map(self._pricing, "modal"), "containerStartSeconds")

    # ---- AI Producer

    def producer_budget_inr(self, short_video: bool) -> Decimal:
        key = "budgetPerShortInr" if short_video else "budgetPerLongInr"
        return dec(self._num(self._map(self._pricing, "producer"), key))

    def producer_setting(self, key: str) -> int:
        return int(self._num(self._map(self._pricing, "producer"), key))

    def producer_ask_above_inr(self) -> Decimal:
        return dec(self._num(self._map(self._pricing, "producer"), "askAboveInr"))

    # ---- Router

    def routing_lambda_per_inr(self) -> float:
        return self._num(self._map(self._pricing, "routing"), "lambdaPerInr")

    def routing_quality(self, capability: str) -> tuple[float, float]:
        """(minQuality, enoughQuality) for a job capability; defaults 0 / 10 when not configured."""
        job = self._map(self._map(self._pricing, "routing"), "jobs").get(capability)
        if not isinstance(job, dict):
            return (0.0, 10.0)

        def pick(k: str, default: float) -> float:
            v = job.get(k)
            return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default

        return (pick("minQuality", 0.0), pick("enoughQuality", 10.0))

    # ---- AICredits (errors-only text fallback)

    def aicredits_cap_inr(self) -> Decimal:
        return dec(self._num(self._map(self._pricing, "aicredits"), "totalCapInr"))

    def aicredits_model_id(self, primary: str) -> str | None:
        m = self._map(self._map(self._pricing, "aicredits"), "models").get(primary)
        return str(m["id"]) if isinstance(m, dict) and m.get("id") is not None else None

    def aicredits_inr(self, primary: str, input_tokens: int, output_tokens: int) -> Decimal:
        aic = self._map(self._pricing, "aicredits")
        rates = self._map(self._map(aic, "models"), primary)
        usd = (
            input_tokens / 1e6 * self._num(rates, "inputPerMTokUsd") + output_tokens / 1e6 * self._num(rates, "outputPerMTokUsd")
        ) * self._num(aic, "markup")
        return self._inr(usd)

    # ---- Claude / ElevenLabs

    def claude_monthly_cap_inr(self) -> Decimal:
        return self._inr(self._num(self._map(self._pricing, "anthropic"), "monthlyCapUsd"))

    def claude_inr(self, input_tokens: int, output_tokens: int) -> Decimal:
        rates = self._map(self._map(self._pricing, "anthropic"), "sonnet")
        return self._inr(
            input_tokens / 1e6 * self._num(rates, "inputPerMTokUsd") + output_tokens / 1e6 * self._num(rates, "outputPerMTokUsd")
        )

    def claude_search_inr(self, searches: int) -> Decimal:
        return self._inr(searches / 1000.0 * self._num(self._map(self._pricing, "anthropic"), "webSearchPer1000Usd"))

    def eleven_labs_inr(self, credits: int) -> Decimal:
        return self._inr(credits / 1000.0 * self._num(self._map(self._pricing, "elevenlabs"), "usdPer1000Credits"))
