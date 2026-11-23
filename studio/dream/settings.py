"""Dream Shorts settings and the rental-hours estimate. Port of DreamSettings and RentalEstimate."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

PICTURE_MODELS = ["flux2-klein", "zimage"]


def clean_picture_model(m: str | None, fallback: str) -> str:
    """A known pod picture model, else the given default."""
    v = (m or "").strip().lower()
    return v if v in PICTURE_MODELS else fallback


@dataclass
class DreamSettings:
    zimage_app: str = "projectv-zimage"
    zimage_class: str = "ZImage"
    wan_app: str = "projectv-wan"
    wan_class: str = "Wan"
    public_url: str = "http://127.0.0.1:8080"
    auto_approve_inr: Decimal = Decimal(0)
    shot_seconds: float = 3.5
    music_dir: str = ""
    music_model: str = "lyria-3.5"
    music_bed: str = ""
    listen_model: str = "gemini-3.6-flash"
    brief_model: str = "claude:claude-sonnet-5-5"
    quality_check: bool = True
    picture_model: str = "flux2-klein"

    @staticmethod
    def from_env(env: dict[str, str]) -> DreamSettings:
        """The same keys and defaults as the Spring @Value bindings, read from environment-style names."""
        def get(k: str, d: str) -> str:
            return env.get(k, d)

        return DreamSettings(
            get("PROJECTV_MODAL_ZIMAGE_APP", "projectv-zimage"), get("PROJECTV_MODAL_ZIMAGE_CLASS", "ZImage"),
            get("PROJECTV_VIDEO_MODAL_APP", "projectv-wan"), get("PROJECTV_VIDEO_MODAL_CLASS", "Wan"),
            get("PROJECTV_PUBLIC_URL", "http://127.0.0.1:8080"), Decimal(get("PROJECTV_DREAM_AUTO_APPROVE_INR", "0")),
            float(get("PROJECTV_DREAM_SHOT_SECONDS", "3.5")), get("PROJECTV_DREAM_MUSIC_DIR", ""),
            get("PROJECTV_MODELS_MUSIC", "lyria-3.5"), get("PROJECTV_DREAM_MUSIC_BED", ""),
            get("PROJECTV_MODELS_VISION", "gemini-3.6-flash"), get("PROJECTV_MODELS_STORY", "claude:claude-sonnet-5-5"),
            get("PROJECTV_DREAM_QUALITY_CHECK", "true").lower() == "true",
            clean_picture_model(get("PROJECTV_DREAM_PICTURE_MODEL", "flux2-klein"), "flux2-klein"))


@dataclass
class RentalEstimate:
    """How long one rented pod runs for a Short, from its own work. Every number is config, never a price (rule 7)."""

    start_seconds: float = 900
    flux_seconds_per_picture: float = 3
    zimage_seconds_per_picture: float = 5
    seconds_per_81_frames: float = 76
    svi_setup_seconds: float = 180
    fallback_hours: float = 0.75

    def hours_for_row(self, d: dict[str, Any] | None) -> float:
        n = None if d is None else d.get("shot_count")
        if not isinstance(n, (int, float)) or isinstance(n, bool) or int(n) <= 0:
            return self.fallback_hours
        clips = int(n)
        s = d.get("svi_scenes")
        svi = int(s) if isinstance(s, (int, float)) and not isinstance(s, bool) else 0
        pictures = svi if svi > 0 else clips  # SVI: one picture per scene; otherwise up to one per shot
        model = d.get("picture_model")
        return self.hours(pictures, None if model is None else str(model), clips, svi > 0)

    def hours(self, pictures: int, picture_model: str | None, clips: int, svi: bool) -> float:
        per = (self.flux_seconds_per_picture if picture_model == "flux2-klein"
               else self.zimage_seconds_per_picture if picture_model == "zimage" else -1)
        if per < 0 or pictures < 0 or clips <= 0:
            return self.fallback_hours
        s = self.start_seconds + pictures * per + clips * self.seconds_per_81_frames + (self.svi_setup_seconds if svi else 0)
        return s / 3600.0
