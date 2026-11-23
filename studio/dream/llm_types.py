"""What the Dream writer, Scout and similarity check ask the LLM for. Field names stay camelCase: they are the JSON contract with the
model and with the stored world_json / scout_json columns (Jackson wrote the Java records the same way)."""

from __future__ import annotations

from pydantic import BaseModel


class PlannedDreamShot(BaseModel):
    """One shot as the writer plans it: the scene (shared world added later) and how the camera moves."""

    scene: str | None = None
    motion: str | None = None


class PlannedScene(BaseModel):
    """One SVI scene as the writer plans it: what its one picture shows, and one motion line per 5 s beat."""

    scene: str | None = None
    beats: list[str] | None = None


class DreamWorld(BaseModel):
    """The writer's output: one world shared by every shot, so the Short looks like one place.

    hero: the one recurring object described exactly once, pasted into every picture so it stays the same. cameraPath: locked scene
    only. hook: the 3-6 word hook title (the starred word is the big coloured one). paths: choose-the-path only, the two options on the
    end card. style/scenes: SVI scenes only."""

    title: str | None = None
    world: str | None = None
    palette: str | None = None
    light: str | None = None
    hero: str | None = None
    shots: list[PlannedDreamShot] | None = None
    hook: str | None = None
    cameraPath: str | None = None  # noqa: N815
    paths: list[str] | None = None
    style: str | None = None
    scenes: list[PlannedScene] | None = None


class ScoutAngle(BaseModel):
    title: str | None = None
    idea: str | None = None
    why: str | None = None


class ScoutBrief(BaseModel):
    """Claude's brief: what most of them do (avoid), what none of them do, and ways to be different."""

    cliches: list[str] | None = None
    gaps: list[str] | None = None
    angles: list[ScoutAngle] | None = None


class SimilarShot(BaseModel):
    shot: int = 0
    like: str | None = None
    why: str | None = None


class Similar(BaseModel):
    """Shots of ours that come too close to a Short that already exists."""

    similar: list[SimilarShot] | None = None
