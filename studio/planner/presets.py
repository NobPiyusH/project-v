"""Loads config/style-presets.yaml. Port of StylePresets."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Preset:
    id: str
    name: str
    description: str
    visual_style: str  # appended to every image/video prompt so all shots share one look
    defaults: dict[str, Any] = field(default_factory=dict)


class StylePresets:
    def __init__(self, config_dir: str | Path):
        path = Path(config_dir) / "style-presets.yaml"
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)["presets"]
        self._presets = [
            Preset(
                id=m["id"], name=m["name"], description=str(m.get("description")).strip(),
                visual_style=str(m["visualStyle"]).strip() if m.get("visualStyle") is not None else "",
                defaults=m.get("defaults") or {},
            )
            for m in raw
        ]
        if not self._presets:
            raise RuntimeError(f"No style presets in {path}")

    def all(self) -> list[Preset]:
        return self._presets

    def by_id_or_default(self, preset_id: str | None) -> Preset:
        return next((p for p in self._presets if p.id == preset_id), self._presets[0])

    def exists(self, preset_id: str) -> bool:
        return any(p.id == preset_id for p in self._presets)
