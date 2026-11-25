"""Requests/results exchanged with the media workers (port of com.projectv.media.MediaTypes).

Field names are the JSON contract with workers/activities/*.py: snake_case here, camelCase on the wire (see wire.py).
Java's extra constructors become trailing defaults. Media travels as s3:// URIs, never bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from studio.agents.types import CreativeReview


@dataclass
class Usage:
    prompt_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    total_tokens: int = 0


# ---- research
@dataclass
class Source:
    title: str
    uri: str


@dataclass
class Fact:
    text: str
    sources: list[Source]
    check: str | None = None  # confirmed | unconfirmed | unreachable | no-numbers | None (not checked)


@dataclass
class ResearchRequest:
    topic: str
    summary: str
    must_include: list[str]
    language: str
    max_facts: int
    model: str
    owner_notes: str | None = None


@dataclass
class ResearchResult:
    facts: list[Fact]
    sources: list[Source]
    queries: list[str]
    model: str
    usage: Usage


# ---- narration
@dataclass
class NarrationLine:
    index: int
    text: str
    delivery: str | None = None  # how to read this line: passed to the TTS director's notes


@dataclass
class NarrationRequest:
    video_id: str
    model: str
    check_model: str
    voice_name: str
    style: str
    language: str
    lines: list[NarrationLine]
    voice_provider: str = "auto"  # auto | elevenlabs | gemini


@dataclass
class NarrationClip:
    index: int
    uri: str
    duration_sec: float
    usage: Usage
    takes: int
    passed: bool
    check: str | None = None


@dataclass
class NarrationResult:
    lines: list[NarrationClip]
    model: str | None
    voice_name: str | None
    characters: int = 0  # ElevenLabs credits used (0 for Gemini)

    def eleven_labs(self) -> bool:
        return self.model is not None and self.model.startswith("elevenlabs:")

    def chatterbox(self) -> bool:
        return self.model is not None and self.model.startswith("chatterbox:")

    def kokoro(self) -> bool:
        return self.model is not None and self.model.startswith("kokoro:")


# ---- music
@dataclass
class MusicRequest:
    video_id: str
    model: str
    prompt: str
    target_sec: float
    max_segments: int


@dataclass
class MusicResult:
    uri: str
    duration_sec: float
    bpm: float
    beat_times: list[float]
    downbeat_times: list[float]
    segments: int
    model: str
    usage: list[Usage]


# ---- keyframes
@dataclass
class KeyframeJob:
    shot_id: str
    attempt: int
    provider: str
    prompt: str
    width: int
    height: int
    aspect_ratio: str
    image_size: str
    reference_query: str
    seed: int
    pod_model: str | None = None  # rented-pod pictures only: flux2-klein or zimage (None = zimage)


@dataclass
class KeyframeRequest:
    video_id: str
    gemini_image_model: str
    modal_app: str
    modal_class: str
    jobs: list[KeyframeJob]
    gpu_url: str | None = None  # rented-pod route only
    ready_timeout_seconds: int = 0


@dataclass
class KeyframeOutput:
    shot_id: str
    uri: str | None = None
    provider: str | None = None
    model: str | None = None
    seed: int | None = None
    seconds: float | None = None
    usage: Usage | None = None
    error: str | None = None
    references: list[str] | None = None  # credit lines of real reference photos (rule 6)


@dataclass
class KeyframeResult:
    results: list[KeyframeOutput]
    modal_gpu_seconds: float
    pod_gpu_seconds: float = 0


# ---- animatic
@dataclass
class AnimaticShot:
    index: int
    start_sec: float
    duration_sec: float
    image_uri: str
    overlay_text: str | None


@dataclass
class AnimaticLine:
    uri: str
    start_sec: float
    tempo: float  # voice speed factor (1.0 = as recorded)


@dataclass
class AnimaticRequest:
    video_id: str
    title: str
    shots: list[AnimaticShot]
    narration: list[AnimaticLine]
    music_uri: str | None
    total_sec: float
    aspect_ratio: str


@dataclass
class AnimaticResult:
    uri: str
    duration_sec: float


# ---- Inspector
@dataclass
class Finding:
    severity: str  # FAIL | WARN | INFO
    code: str
    message: str
    ref: str | None = None
    target_id: str | None = None  # the shot id a FAIL can be fixed on alone


@dataclass
class InspectLine:
    index: int
    text: str
    uri: str | None


@dataclass
class InspectImage:
    ref: str
    uri: str


@dataclass
class MediaInspectRequest:
    video_id: str
    stage: str
    aspect_ratio: str
    is_short: bool
    target_sec: float
    lines: list[InspectLine]
    music_uri: str | None
    images: list[InspectImage]
    video_uri: str | None
    speech_end_sec: float | None


@dataclass
class MediaInspectResult:
    findings: list[Finding] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)


# ---- Creative Director image review
@dataclass
class ReviewLine:
    index: int
    act: str
    text: str
    start_sec: float


@dataclass
class ReviewShot:
    n: int
    shot_id: str
    line_index: int | None
    act: str
    start_sec: float
    duration_sec: float
    description: str
    keyframe_prompt: str
    priority: str
    image_uri: str | None


@dataclass
class ImageReviewRequest:
    video_id: str
    model: str
    rubric: str
    title: str
    aspect_ratio: str
    visual_style: str
    logline: str
    big_idea: str
    emotional_arc: str
    music_brief: str
    lines: list[ReviewLine]
    shots: list[ReviewShot]


@dataclass
class ImageReviewResult:
    review: CreativeReview
    model: str
    usage: Usage
    images_sent: int


# ---- moving video per shot
@dataclass
class ClipJob:
    shot_id: str
    shot_number: int
    take: int
    kind: str  # draft (480p) or final (720p)
    mode: str  # fast | hybrid | standard
    image_uri: str
    prompt: str
    width: int
    height: int
    num_frames: int  # 4n+1 frames at 16 fps (81 = ~5 s)
    seed: int
    negative: str | None = None
    chain_from_uri: str | None = None
    end_image_uri: str | None = None  # first-last frame chaining
    scene: int = 0  # SVI scenes (1-based), 0 = not an SVI beat
    beat: int = 0
    camera: str | None = None  # Fun Camera preset (tools/film4k/camera_presets.py); set = the pod runs the Fun Camera graph (mode "camera")


@dataclass
class ClipRequest:
    video_id: str
    modal_app: str
    modal_class: str
    progress_url: str | None
    jobs: list[ClipJob]
    containers: int
    estimate_gpu_seconds: float
    estimate_inr: Decimal
    provider: str
    resolution: str
    omni_model: str | None
    hold_seconds: int = 0
    gpu_url: str | None = None
    ready_timeout_seconds: int = 0
    chain: bool = False  # clips run one at a time, each from the last frame of the shot before (Dream Shorts)
    chain_group: int = 0  # 0 = one chain through the whole Short; N = chains of N shots
    locked: bool = False
    svi: bool = False  # SVI scenes, one scene per pod job (pod route only)


@dataclass
class ClipOutput:
    shot_id: str
    take: int
    kind: str
    uri: str | None
    gpu_seconds: float
    frames: int
    fps: int
    mode: str | None
    error: str | None
    output_tokens: int


@dataclass
class ClipResult:
    clips: list[ClipOutput]
    containers: int
    stopped: bool
    cold_starts: int | None = None

    def start_ups(self) -> int:
        """Start-ups to book: the cold starts the GPU reported, else (older app) one per container used."""
        return self.containers if self.cold_starts is None else self.cold_starts


@dataclass
class ClipEstimate:
    kind: str
    clips: int
    gpu_seconds: float
    inr: Decimal
    usd: float
    wall_minutes: float
    needs_approval: bool
    summary: str


# ---- stock footage lane
@dataclass
class StockRequest:
    video_id: str
    shot_id: str
    shot_number: int
    description: str
    aspect_ratio: str
    duration_sec: float
    sources: list[str]
    query_model: str


@dataclass
class StockProvenance:
    source: str | None = None
    source_id: str | None = None
    page_url: str | None = None
    author: str | None = None
    licence: str | None = None
    licence_url: str | None = None
    attribution: str | None = None
    credit_required: bool = False
    fetched_at: str | None = None
    sha256: str | None = None


@dataclass
class StockResult:
    found: bool  # False (no key, no match, any error) means: use the AI lane for this shot
    reason: str | None = None
    queries: list[str] | None = None
    skipped: list[str] | None = None
    uri: str | None = None
    width: int | None = None
    height: int | None = None
    duration_sec: float | None = None
    provenance: StockProvenance | None = None


# ---- free real footage (NASA)
@dataclass
class FootageQuery:
    shot_id: str
    query: str


@dataclass
class FootageCandidate:
    nasa_id: str
    title: str
    description: str | None
    credit: str | None
    date: str | None
    thumb_url: str | None
    preview_url: str | None


@dataclass
class FootageSearchRequest:
    queries: list[FootageQuery]


@dataclass
class FootageSearchResult:
    shot_id: str
    query: str
    candidates: list[FootageCandidate]
    error: str | None


@dataclass
class FootageSearchResponse:
    results: list[FootageSearchResult]


@dataclass
class FootageFetchRequest:
    video_id: str
    shot_id: str
    shot_number: int
    nasa_id: str
    start_sec: float | None  # None = automatic (skips NASA's opening title card)
    duration_sec: float
    aspect_ratio: str


@dataclass
class FootageFetchResult:
    uri: str
    width: int
    height: int
    duration_sec: float
    source_width: int
    source_height: int
    upscale: float | None  # 1.0 = native, >1.5 = soft
    start_sec: float
    source_url: str | None


# ---- assembly (the edit)
@dataclass
class AssemblyShot:
    n: int
    uri: str
    kind: str  # footage | final | draft
    start_sec: float
    duration_sec: float
    overlay_text: str | None
    sfx_query: str | None = None


@dataclass
class AssemblyLine:
    uri: str
    start_sec: float
    tempo: float
    text: str
    duration_sec: float


@dataclass
class AssemblyRequest:
    video_id: str
    title: str
    aspect_ratio: str
    language: str
    captions: bool
    shots: list[AssemblyShot]
    narration: list[AssemblyLine]
    music_uri: str | None
    total_sec: float
    output_height: int = 0  # 0 = default 1080p master; 1440; 2160
    credits: list[str] | None = None
    quality_gate: bool = False  # run the free quality gate (tiers 1-2) on the master after the edit


@dataclass
class AssemblyResult:
    master_uri: str
    package_uri: str | None
    srt_uri: str | None
    duration_sec: float
    width: int
    height: int
    fps: int
    fits: list[dict[str, Any]]
    caption_chunks: int
    lines_timed_by_whisper: int
    sfx_credits: list[str] | None = None
    credits_uri: str | None = None


# ---- smooth motion
@dataclass
class SmoothJob:
    shot_id: str
    shot_number: int
    source_asset_id: str
    uri: str
    src_fps: float


@dataclass
class SmoothRequest:
    video_id: str
    progress_url: str | None
    dst_fps: float
    jobs: list[SmoothJob]
    estimate_inr: Decimal


@dataclass
class SmoothOutput:
    shot_id: str
    source_asset_id: str
    uri: str | None
    gpu_seconds: float
    frames: int
    fps: float
    error: str | None


@dataclass
class SmoothResult:
    clips: list[SmoothOutput]
    containers: int
    stopped: bool


# ---- AI Producer
@dataclass
class ProducerItem:
    id: str
    label: str
    kind: str  # text | image | video | audio
    uri: str | None
    text: str | None
    expect: str | None


@dataclass
class ProducerReviewRequest:
    video_id: str
    stage: str
    model: str
    rubric: str
    context: str
    rules: list[str]
    items: list[ProducerItem]


@dataclass
class ProducerItemVerdict:
    id: str
    score: int
    ok: bool
    problem: str | None
    fix: str | None


@dataclass
class ProducerReviewResult:
    verdict: str  # approve | redo | ask
    confidence: float
    summary: str
    redo_notes: str | None
    items: list[ProducerItemVerdict]
    model: str
    usage: Usage


@dataclass
class ProducerAdvice:
    verdict: str
    confidence: float
    summary: str
    redo_notes: str | None
    decision_id: str | None
    weak_items: list[str] = field(default_factory=list)


@dataclass
class SpendDecision:
    approve: bool
    text: str | None
