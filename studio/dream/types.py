"""Small serialisable types for Dream Shorts, passed through Temporal (IDs and text only, never media bytes). Port of DreamTypes.

Every type is a plain dataclass; studio.media.wire turns it into camelCase JSON, which is what the media worker and the workflows
exchange."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from studio.media.types import Usage

DREAM_STEPS = ["SCOUT", "WRITE", "PICTURES", "CLIPS", "MUSIC", "FINISH"]


@dataclass
class PlannedDreamShot:
    scene: str | None = None
    motion: str | None = None


@dataclass
class PlannedScene:
    scene: str | None = None
    beats: list[str] | None = None


@dataclass
class DreamWorld:
    """The writer's output: one world shared by every shot. hero: the one recurring object described exactly once; camera_path:
    locked scene only; hook: the 3-6 word hook title (the starred word is the big coloured one); paths: choose-the-path only;
    style/scenes: SVI scenes only."""

    title: str | None = None
    world: str | None = None
    palette: str | None = None
    light: str | None = None
    hero: str | None = None
    shots: list[PlannedDreamShot] = field(default_factory=list)
    hook: str | None = None
    camera_path: str | None = None
    paths: list[str] | None = None
    style: str | None = None
    scenes: list[PlannedScene] | None = None


@dataclass
class DreamEstimate:
    """What a step would cost before anything is spent. needs_approval: over the auto-approve amount."""

    step: str
    inr: Decimal
    minutes: float
    needs_approval: bool
    summary: str


@dataclass
class DreamFinishClip:
    uri: str
    fps: float
    scene: str | None = None


@dataclass
class DreamFinishRequest:
    dream_id: str
    look: str | None = None
    clips: list[DreamFinishClip] = field(default_factory=list)
    width: int = 1080
    height: int = 1920
    fps: int = 24
    dissolve_sec: float = 0.5
    music_dir: str | None = None
    progress_url: str | None = None
    music_uri: str | None = None
    music_name: str | None = None
    speed: float = 1.0
    handheld: str | None = None
    music_edit: bool = False
    ambience: bool = False
    pacing: bool = False
    keep_order: bool = False
    hook_text: str | None = None
    hook_color: str | None = None
    path_a: str | None = None
    path_b: str | None = None
    svi_scenes: list[int] | None = None
    svi_scene_frames: list[int] | None = None


@dataclass
class DreamFinishResult:
    uri: str | None = None
    duration_sec: float = 0.0
    width: int = 0
    height: int = 0
    fps: int = 0
    smoothing: str | None = None
    music: str | None = None
    error: str | None = None
    metrics: dict[str, float] | None = None


@dataclass
class DreamBedRequest:
    dream_id: str
    bed_uri: str
    target_sec: float
    seed: str | None = None
    used_starts_sec: list[float] = field(default_factory=list)
    model: str | None = None
    brief: str | None = None
    picture_uris: list[str] = field(default_factory=list)
    candidates: int = 1


@dataclass
class DreamBedResult:
    uri: str | None = None
    duration_sec: float = 0.0
    start_sec: float = 0.0
    bed_duration_sec: float = 0.0
    bed_name: str | None = None
    model: str | None = None
    usage: Usage | None = None
    why: str | None = None
    heard: int = 0


@dataclass
class ScoutVideo:
    url: str | None = None
    title: str | None = None
    channel: str | None = None
    views: int | None = None
    duration_sec: float | None = None
    shows: str | None = None
    look: str | None = None
    camera: str | None = None
    shots: int | None = None
    avg_shot_sec: float | None = None
    music: str | None = None
    hook: str | None = None
    tools: str | None = None
    works: str | None = None
    error: str | None = None


@dataclass
class DreamScoutRequest:
    dream_id: str
    query: str
    model: str
    max_videos: int
    look: str | None = None
    known: dict[str, ScoutVideo] = field(default_factory=dict)


@dataclass
class DreamScoutResult:
    videos: list[ScoutVideo] = field(default_factory=list)
    usage: list[Usage] = field(default_factory=list)
    source: str | None = None
    searches: int = 0
    error: str | None = None


@dataclass
class ScoutAngle:
    title: str | None = None
    idea: str | None = None
    why: str | None = None


@dataclass
class ScoutBrief:
    cliches: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    angles: list[ScoutAngle] = field(default_factory=list)


@dataclass
class SimilarShot:
    shot: int = 0
    like: str | None = None
    why: str | None = None


@dataclass
class Similar:
    similar: list[SimilarShot] = field(default_factory=list)


@dataclass
class DreamCheckRequest:
    dream_id: str
    video_uri: str | None = None
    brief: str | None = None
    model: str | None = None
    picture_uris: list[str] | None = None


@dataclass
class CheckIssue:
    shot: int = 0
    at_sec: float = 0.0
    problem: str | None = None
    fix: str | None = None


@dataclass
class DreamCheckResult:
    scores: dict[str, int] = field(default_factory=dict)
    overall: int = 0
    issues: list[CheckIssue] = field(default_factory=list)
    summary: str | None = None
    model: str | None = None
    usage: Usage | None = None
    error: str | None = None
