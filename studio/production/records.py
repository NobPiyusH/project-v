"""Shared production records (port of the records in ProductionService). Stored as JSON in the script row: camelCase on the wire (see
studio.media.wire), snake_case here. Frozen: every `with_*` returns a new record, like the Java records."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from enum import Enum


@dataclass(frozen=True)
class AudioVersion:
    """One generated voice take of a line. All takes are kept; the line points at the active one. text: exactly what this take says
    (so the system knows whether an edited line still matches it)."""

    asset_id: uuid.UUID | None
    uri: str
    duration_sec: float
    created_at: str
    check: str | None = None
    style: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class StoredLine:
    """One stored script line, enriched with its narration audio once generated.

    voice_status: None | REDOING | READY | IMPERFECT (last take still failed the transcript check) | FAILED.
    needs_voice: True when the owner edited the text after it was voiced (the audio no longer matches).
    original_text: the writer's text before the owner's first edit (kept for taste learning).
    delivery: how to read THIS line (owner, 18 Nov 2026: the voice follows the story line by line).
    """

    index: int
    text: str
    act: str | None = None
    purpose: str | None = None
    spoken: bool = False
    audio_asset_id: uuid.UUID | None = None
    audio_uri: str | None = None
    duration_sec: float | None = None
    voice_status: str | None = None
    needs_voice: bool | None = None
    edited: bool | None = False
    original_text: str | None = None
    audio_versions: list[AudioVersion] | None = field(default_factory=list)
    check: str | None = None
    delivery: str | None = None

    def versions(self) -> list[AudioVersion]:
        return self.audio_versions or []

    def with_status(self, status: str | None, why: str | None) -> StoredLine:
        return replace(self, voice_status=status, check=why)

    def with_new_take(self, v: AudioVersion, passed: bool) -> StoredLine:
        all_versions = list(self.versions())
        if not all_versions and self.audio_asset_id is not None:
            # Lines voiced before versions existed: keep the current take as the first version.
            all_versions.append(AudioVersion(self.audio_asset_id, self.audio_uri or "", self.duration_sec or 0.0, "", self.check, None,
                                             self.voiced_text()))
        all_versions.insert(0, v)
        return replace(self, audio_asset_id=v.asset_id, audio_uri=v.uri, duration_sec=v.duration_sec,
                       voice_status="READY" if passed else "IMPERFECT", needs_voice=False, audio_versions=all_versions, check=v.check)

    def with_active(self, v: AudioVersion) -> StoredLine:
        needs = self.spoken and v.text is not None and v.text != self.text
        return replace(self, audio_asset_id=v.asset_id, audio_uri=v.uri, duration_sec=v.duration_sec, voice_status="READY",
                       needs_voice=needs, check=v.check)

    def voiced_text(self) -> str | None:
        """The text the active take actually speaks (older data: the text before the owner's first edit)."""
        if self.audio_asset_id is None:
            return None
        for v in self.versions():
            if self.audio_asset_id == v.asset_id and v.text is not None:
                return v.text
        if self.original_text is not None:
            return self.original_text
        return None if self.needs_voice is True else self.text

    def with_edit(self, new_text: str, new_spoken: bool) -> StoredLine:
        needs = new_spoken and new_text != self.voiced_text()
        writers = self.original_text if self.original_text is not None else self.text  # the writer's version, before any edit
        differs = new_text != writers
        return replace(self, text=new_text, spoken=new_spoken, needs_voice=needs, edited=differs,
                       original_text=writers if differs else None)


@dataclass(frozen=True)
class StoredScript:
    """line_starts: script line index -> start time on the timeline (set by the shot plan, from real voice lengths);
    tempo: voice speed factor the animatic/edit applies (1.0 = unchanged; Shorts may use up to 1.08)."""

    version: int
    lines: list[StoredLine]
    voice_name: str | None = None
    voice_style: str | None = None
    music_brief: str | None = None
    line_starts: dict[str, float] | None = None
    tempo: float | None = None


class ImagePolicy(Enum):
    """Which image model a (re)render may use. AUTO: hero shots premium while the budget allows, the rest free."""

    AUTO = "AUTO"
    PREMIUM = "PREMIUM"
    FREE = "FREE"
