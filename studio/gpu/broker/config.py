"""Broker settings: provider order and per-provider monthly caps (USD, 0 = blocked) from pricing.gpuBroker in
config/providers.yaml. Port of BrokerConfig. Caps are limits, never prices (rule 7). daily_cap_usd: all studio GPU spend per UTC
day (owner, 21 Nov 2026: $3.50); 0 = no daily limit."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_ORDER = ["runpod", "jarvislabs", "cloudrift", "vast", "shadeform", "modal"]
DEFAULT_GPUS = ["NVIDIA GeForce RTX 4090", "NVIDIA GeForce RTX 5090", "NVIDIA RTX A6000", "NVIDIA L40S"]


def _num(o: Any, default: float) -> float:
    return float(o) if isinstance(o, (int, float)) and not isinstance(o, bool) else default


@dataclass
class BrokerConfig:
    order: list[str] = field(default_factory=lambda: list(DEFAULT_ORDER))
    monthly_cap_usd: dict[str, float] = field(default_factory=dict)
    total_cap_usd: float = 0
    gpu_preference: list[str] = field(default_factory=lambda: list(DEFAULT_GPUS))
    min_vram_gb: int = 24
    min_ram_gb: int = 48
    disk_gb: int = 80
    expected_hours: float = 0.75
    port: int = 8000
    daily_cap_usd: float = 0

    def cap_usd(self, provider: str) -> float:
        return self.monthly_cap_usd.get(provider, 0.0)

    @staticmethod
    def from_yaml(root: dict[str, Any]) -> BrokerConfig:
        pricing = root.get("pricing") if isinstance(root.get("pricing"), dict) else {}
        b = pricing.get("gpuBroker") if isinstance(pricing.get("gpuBroker"), dict) else {}
        caps: dict[str, float] = {}
        if isinstance(b.get("providers"), dict):
            for k, v in b["providers"].items():
                if isinstance(v, dict) and isinstance(v.get("monthlyCapUsd"), (int, float)):
                    caps[str(k)] = float(v["monthlyCapUsd"])
        return BrokerConfig(
            order=[str(x) for x in b["order"]] if isinstance(b.get("order"), list) else list(DEFAULT_ORDER),
            monthly_cap_usd=caps,
            total_cap_usd=_num(b.get("totalCapUsd"), 0),
            gpu_preference=[str(x) for x in b["gpuPreference"]] if isinstance(b.get("gpuPreference"), list) else list(DEFAULT_GPUS),
            min_vram_gb=int(_num(b.get("minVramGb"), 24)), min_ram_gb=int(_num(b.get("minRamGb"), 48)),
            disk_gb=int(_num(b.get("diskGb"), 80)), expected_hours=_num(b.get("expectedHours"), 0.75),
            port=int(_num(b.get("port"), 8000)), daily_cap_usd=_num(b.get("dailyCapUsd"), 0),
        )

    @staticmethod
    def load(config_dir: str | Path) -> BrokerConfig:
        with open(Path(config_dir) / "providers.yaml", encoding="utf-8") as f:
            return BrokerConfig.from_yaml(yaml.safe_load(f))
