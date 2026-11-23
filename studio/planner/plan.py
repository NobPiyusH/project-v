"""What the Planner decides from a free-text prompt (R1). Port of VideoPlan.

Field names and allowed values are also the contract the LLM is asked to fill, so they stay camelCase (as in Java).
"""

from __future__ import annotations

from pydantic import BaseModel


class VideoPlan(BaseModel):
    title: str | None = None
    summary: str | None = None
    format: str | None = None  # long_form | short
    durationSec: int = 0  # noqa: N815
    aspectRatio: str | None = None  # noqa: N815  16:9 | 9:16
    outputResolution: str | None = None  # noqa: N815  2160p (4K) | 1080p
    stylePreset: str | None = None  # noqa: N815
    tone: str | None = None
    language: str | None = None
    needsResearch: bool = False  # noqa: N815
    narration: str | None = None  # full | sparse | none
    voice: str | None = None
    music: str | None = None  # lead (music-first edit) | support | none
    soundDesign: bool = False  # noqa: N815
    visualTypes: list[str] | None = None  # noqa: N815  ai_video, ai_image_motion, motion_graphics, stock_footage, text_cards, data_counter
    shortsCount: int = 0  # noqa: N815
    mustInclude: list[str] | None = None  # noqa: N815
    rationale: str | None = None
