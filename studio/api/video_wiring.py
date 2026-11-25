"""Builds the object graph behind the video routes and the video worker: the stages, VideoService, the Inspector and Creative Director, the AI
Producer's control side, and the Temporal control activities. Same rule as wiring.py: lazy, nothing real is called while building, every part can
be injected by a test. Model names and lane switches read the same environment variables as application.properties."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from studio.api.wiring import DreamContext, LazyGateway, build_llm
from studio.assets.asset_store import AssetStore
from studio.assets.cache import AssetCache
from studio.assets.media_urls import MediaUrls
from studio.config import Settings
from studio.db import Db
from studio.ledger import BudgetGuard, CostLedger, Pricing
from studio.planner.imported import ImportedDirector, ImportedPlanner, ImportedWriter
from studio.planner.planner import Planner
from studio.planner.presets import StylePresets
from studio.production.creative_director import CreativeDirector
from studio.production.finishing_stages import AssemblyStage, FinishStage, FootageStage, SharpenStage
from studio.production.inspector import Inspector
from studio.production.interpolate_stage import InterpolateStage
from studio.production.moon_stage import MoonLayerStage
from studio.production.producer import ProducerService
from studio.production.read_model import ProductionReadModel
from studio.production.shot_stage import ShotStage, StockStage
from studio.production.stages_m2a import AnimaticStage, MusicStage, ResearchStage, StoryStage, VoiceStage
from studio.production.support import ProductionDeps
from studio.production.video_gpu import VideoGpu
from studio.production.video_stage import VideoStage
from studio.provider.caps import build_caps
from studio.routing import Router
from studio.video.clip_progress import ClipProgress
from studio.video.service import VideoService
from studio.workflow.activities import ControlActivities, ProducerActivities


def _quality_gate_on(value: str) -> bool:
    """QUALITY_GATE_ENABLED: free quality gate after the edit, on unless set to 0/false/off/no."""
    return value.strip().lower() not in ("0", "false", "off", "no")


def _flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).lower() == "true"


@dataclass
class VideoContext:
    settings: Settings
    db: Db
    pricing: Any
    budget: Any
    ledger: Any
    presets: Any
    assets: Any
    media_urls: Any
    planner: Any
    videos: VideoService
    producer: ProducerService
    inspector: Inspector
    creative: CreativeDirector
    read_model: ProductionReadModel
    shots: ShotStage
    voice: VoiceStage
    video: VideoStage
    footage: FootageStage
    clip_progress: ClipProgress
    control: ControlActivities
    producer_acts: ProducerActivities
    gateway: Any
    video_gpu: Any = None  # the VideoGpu when the broker route is wired (the periodic hold check needs it)


def build_video_context(db: Db, s: Settings, *, llm: Any = None, gateway: Any = None, assets: Any = None, media_urls: Any = None,
                        dream_ctx: DreamContext | None = None, clip_progress: ClipProgress | None = None, researcher: Any = None) -> VideoContext:
    """Every part can be injected; what is not given is built from Settings and the environment. Nothing is started and nothing is spent."""
    from studio.agents.chat import ClaudeChat
    from studio.agents.creative_director import CreativeDirectorAgent
    from studio.agents.director import DirectorAgent
    from studio.agents.researcher import ClaudeResearcher
    from studio.agents.writer import WriterAgent

    env = os.environ.get
    ledger, pricing = CostLedger(db), Pricing(s.config_dir)
    budget = BudgetGuard(db, pricing)
    router = Router(db, budget, pricing, build_caps(db, s.config_dir))
    presets = StylePresets(s.config_dir)
    assets = assets or AssetStore(db)
    llm = llm or (dream_ctx.dreams.llm if dream_ctx is not None else build_llm(s, ledger, pricing, budget, router))
    gateway = gateway or (dream_ctx.gateway if dream_ctx is not None else LazyGateway(s.temporal_address, s.temporal_namespace))
    clip_progress = clip_progress or (dream_ctx.clip_progress if dream_ctx is not None else ClipProgress())
    diagram_lane, stock_lane = _flag("VIDEO_DIAGRAM_LANE"), _flag("VIDEO_STOCK_LANE")
    shot_bank = _flag("VIDEO_SHOT_BANK", "true")  # character/location bank in new shot plans; old stored plans load either way
    writer = WriterAgent(llm, presets, s.story_model, s.script_model)
    director = DirectorAgent(llm, presets, s.story_model, diagram_lane, stock_lane, shot_bank)
    writer, director = ImportedWriter(writer, db), ImportedDirector(director, db)  # a video made from a plan file skips the paid LLM calls
    deps = ProductionDeps(db, writer, director, assets, ledger, pricing, budget, presets, text_model=s.text_model,
                          tts_model=env("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts,gemini-3.1-flash-tts-preview"),
                          image_model=env("GEMINI_IMAGE_MODEL", "gemini-nano-banana-2.1,gemini-3.1-flash-image"),
                          music_model=env("GEMINI_MUSIC_MODEL", "lyria-3.5,lyria-3-pro-preview"), default_voice=env("GEMINI_TTS_VOICE", "Charon"),
                          modal_app=env("PROJECTV_MODAL_ZIMAGE_APP", "projectv-zimage"), modal_class=env("PROJECTV_MODAL_ZIMAGE_CLASS", "ZImage"),
                          cache=AssetCache(db, assets, ledger))
    public_url = env("PROJECTV_PUBLIC_URL", "http://127.0.0.1:8080")
    approve = Decimal(env("PROJECTV_VIDEO_AUTO_APPROVE_INR", "0"))
    video_gpu = None
    if dream_ctx is not None and dream_ctx.gpu is not None:  # inert unless a provider-* setting is "broker"
        video_gpu = VideoGpu(dream_ctx.gpu, env("VIDEO_PROVIDER_DRAFT", "wan"), env("VIDEO_PROVIDER_FINAL", "omni"),
                             env("VIDEO_PROVIDER_KEYFRAMES", "default"), int(env("VIDEO_GPU_IDLE_MINUTES", "5")),
                             one_pod=_flag("VIDEO_ONE_POD"), hold_idle_minutes=int(env("VIDEO_ONE_POD_IDLE_MINUTES", "5")),
                             camera_prefetch=_flag("VIDEO_CAMERA_PRESETS", "true"))
    producer = ProducerService(deps, llm=llm)
    inspector = Inspector(deps)
    creative = CreativeDirector(deps, CreativeDirectorAgent(llm), env("GEMINI_VISION_MODEL", "gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite"))
    research, story, music, voice, animatic = ResearchStage(deps), StoryStage(deps), MusicStage(deps), VoiceStage(deps), AnimaticStage(deps)
    shots = ShotStage(deps, router, video_gpu, None, stock_lane, shot_bank)
    stock = StockStage(deps, stock_lane, _flag("VIDEO_STOCK_PEXELS"), _flag("VIDEO_STOCK_PIXABAY"), _flag("VIDEO_STOCK_COMMONS"), _flag("VIDEO_STOCK_ARCHIVE"))
    video = VideoStage(deps, env("PROJECTV_VIDEO_MODAL_APP", "projectv-wan"), env("PROJECTV_VIDEO_MODAL_CLASS", "Wan"), public_url, approve,
                       draft_provider=env("VIDEO_PROVIDER_DRAFT", "wan"), final_provider=env("VIDEO_PROVIDER_FINAL", "omni"),
                       omni_model=env("OMNI_MODEL", "gemini-omni-1.2-flash"), router=router, video_gpu=video_gpu,
                       diagram_width=int(env("VIDEO_DIAGRAM_WIDTH", "1920")), diagram_height=int(env("VIDEO_DIAGRAM_HEIGHT", "1080")),
                       camera_presets=_flag("VIDEO_CAMERA_PRESETS", "true"), camera_allow_untested=_flag("VIDEO_CAMERA_ALLOW_UNTESTED"))
    footage = FootageStage(deps)
    assembly = AssemblyStage(deps, int(env("VIDEO_OUTPUT_HEIGHT", "0")), _quality_gate_on(env("QUALITY_GATE_ENABLED", "1")))
    finish = FinishStage(deps, public_url, approve)
    sharpen = SharpenStage(deps, finish, public_url, approve)
    moon_on = _flag("MOON_LAYER", "true")  # default ON, but only a plan file with a `moon` block ever gets the stage
    moon = MoonLayerStage(deps, moon_on)
    interpolate = InterpolateStage(deps, env("INTERPOLATE_FPS", "off"))  # off by default; the stage is always planned and skips itself when off
    planner = ImportedPlanner(Planner(presets, llm, s.gemini_api_key, s.text_model, s.story_model), db, moon_on)
    researcher = researcher or ClaudeResearcher(ClaudeChat(s.anthropic_api_key), ledger, budget, pricing)
    control = ControlActivities(db, planner, researcher=researcher, inspector=inspector, creative=creative, research=research, story=story, music=music,
                                voice=voice, shots=shots, stock=stock, animatic=animatic, video=video, footage=footage, assembly=assembly, finish=finish,
                                sharpen=sharpen, clip_progress=clip_progress, moon=moon, interpolate=interpolate)
    producer_acts = ProducerActivities(producer, voice)
    videos = VideoService(db, gateway, producer)
    return VideoContext(s, db, pricing, budget, ledger, presets, assets, media_urls or _lazy_urls(s), planner,
                        videos, producer, inspector, creative, ProductionReadModel(deps), shots, voice, video, footage, clip_progress, control,
                        producer_acts, gateway, video_gpu)


class _LazyMediaUrls:
    """boto3 is imported and the client built only when the first asset link is asked for."""

    def __init__(self, s: Settings):
        self._s, self._urls = s, None

    def presign(self, uri: str | None, ttl_seconds: int) -> str:
        if self._urls is None:
            self._urls = MediaUrls(self._s)
        return self._urls.presign(uri, ttl_seconds)


def _lazy_urls(s: Settings) -> _LazyMediaUrls:
    return _LazyMediaUrls(s)
