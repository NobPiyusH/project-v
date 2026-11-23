"""Cost ledger for paid gate calls (CLAUDE.md rule 6) with a hard per-run cap and a per-day cap.

Every OpenAI call becomes one ledger row. The JSONL file is always written (also the source of the per-day sum);
a database sink, when given, also inserts a cost_entry row like the Java control plane does. `estimate` shows the
price BEFORE a paid step (queue item 7); `reserve` refuses a call that would pass a cap.
"""
from __future__ import annotations

import json
import math
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path


class CapExceeded(RuntimeError):
    pass


def image_tokens(width: int, height: int, multiplier: float, detail_budget_patches: int = 2500) -> int:
    """Billable image tokens for a patch-based model: 32px patches (budgeted), times the model multiplier."""
    patches = math.ceil(width / 32) * math.ceil(height / 32)
    if patches > detail_budget_patches:
        patches = detail_budget_patches
    return math.ceil(patches * multiplier)


def call_cost_usd(input_tokens: int, output_tokens: int, price: dict) -> float:
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


def estimate(n_images: int, cfg: dict, width: int = 1024, height: int = 576, prompt_tokens: int = 350) -> dict:
    """Upper-ish estimate shown before a run: input = image + prompt, output = max_output_tokens (worst case)."""
    v = cfg["vision"]
    per_in = image_tokens(width, height, v["patch_multiplier"]) + prompt_tokens
    typical_out = v["max_output_tokens"] // 3
    worst = call_cost_usd(per_in, v["max_output_tokens"], v["price_usd_per_1m"])
    typical = call_cost_usd(per_in, typical_out, v["price_usd_per_1m"])
    return {"images": n_images, "per_call_typical_usd": round(typical, 5), "per_call_worst_usd": round(worst, 5),
            "typical_usd": round(typical * n_images, 4), "worst_usd": round(worst * n_images, 4),
            "typical_inr": round(typical * n_images * cfg["caps"]["fx_inr_per_usd"], 2)}


class Ledger:
    def __init__(self, path: Path, cfg: dict, video_id: str | None = None,
                 db_sink: Callable[[dict], None] | None = None, now: Callable[[], datetime] | None = None):
        self.path, self.cfg, self.video_id, self.db_sink = Path(path), cfg, video_id, db_sink
        self.now = now or (lambda: datetime.now(UTC))
        self.run_usd = 0.0

    def day_usd(self) -> float:
        day = self.now().date().isoformat()
        total = 0.0
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("ts", "")[:10] == day:
                    total += float(row.get("amountUsd") or 0)
        return total

    def reserve(self, worst_case_usd: float) -> None:
        caps = self.cfg["caps"]
        if self.run_usd + worst_case_usd > caps["per_run_usd"]:
            raise CapExceeded(f"per-run cap ${caps['per_run_usd']:.2f} would be passed "
                              f"(spent ${self.run_usd:.4f}, next call up to ${worst_case_usd:.4f})")
        if self.day_usd() + worst_case_usd > caps["per_day_usd"]:
            raise CapExceeded(f"per-day cap ${caps['per_day_usd']:.2f} would be passed")

    def record(self, usd: float, units: int, note: str, estimated: bool = False) -> dict:
        fx = float(self.cfg["caps"]["fx_inr_per_usd"])
        row = {"id": str(uuid.uuid4()), "videoId": self.video_id, "providerId": self.cfg["vision"]["provider_id"],
               "amountUsd": round(usd, 6), "amountInr": round(usd * fx, 4), "fxRate": fx, "units": units,
               "unitType": "tokens", "estimated": estimated, "note": note, "ts": self.now().isoformat()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        self.run_usd += usd
        if self.db_sink:
            try:
                self.db_sink(row)
            except Exception:  # noqa: BLE001 - a failed database write must not lose the JSONL row or kill the run
                pass
        return row
