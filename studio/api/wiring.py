"""Builds the object graph behind the Dream, Dream GPU and Autopilot routes. Everything real is created lazily and nothing here talks to a paid
service by itself: the LLM clients only call out when a workflow asks them to, the GPU broker exists only with DREAM_GPU_PROVIDER=broker
(and then only RunPod, the studio's default provider), and the Temporal gateway connects on first use. Tests pass fakes for any part."""

from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from studio.assets.asset_store import AssetStore
from studio.autopilot.service import AutopilotService
from studio.config import Settings
from studio.db import Db
from studio.dream.dream_studio import DreamStudio
from studio.dream.gpu import DreamGpu
from studio.dream.lessons import DreamLessons
from studio.dream.producer import DreamProducer
from studio.dream.service import DreamService
from studio.dream.settings import DreamSettings, RentalEstimate
from studio.ledger import BudgetGuard, CostLedger, Pricing
from studio.planner.presets import StylePresets
from studio.production.producer import ProducerService
from studio.production.support import ProductionDeps
from studio.routing import Router
from studio.video.clip_progress import ClipProgress


@dataclass
class DreamContext:
    settings: Settings
    db: Db
    pricing: Any
    dreams: DreamService
    studio: DreamStudio
    lessons: DreamLessons
    gpu: DreamGpu | None
    dream_producer: DreamProducer
    autopilot: AutopilotService
    gateway: Any
    clip_progress: ClipProgress


class LazyGateway:
    """The Temporal client, connected on first use (so an app with no Temporal running still starts and serves everything else)."""

    def __init__(self, address: str):
        self.address = address
        self._gateway: Any = None
        self._client: Any = None
        self._lock = threading.Lock()

    def _get(self) -> Any:
        with self._lock:
            if self._gateway is None:
                from temporalio.client import Client

                from studio.workflow.runtime import TemporalGateway

                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="temporal-lazy", daemon=True).start()
                self._client = asyncio.run_coroutine_threadsafe(Client.connect(self.address), loop).result(timeout=15)
                self._gateway = TemporalGateway(self._client, loop)
            return self._gateway

    def client(self) -> Any:
        self._get()
        return self._client

    def start(self, workflow: str, workflow_id: str, arg: Any) -> None:
        self._get().start(workflow, workflow_id, arg)

    def signal(self, workflow_id: str, name: str, *args: Any) -> None:
        self._get().signal(workflow_id, name, *args)

    def query(self, workflow_id: str, name: str) -> Any:
        return self._get().query(workflow_id, name)


def build_llm(s: Settings, ledger: CostLedger, pricing: Pricing, budget: BudgetGuard, router: Router) -> Any:
    """The text-model stack (S3): Gemini, NVIDIA / Groq / OpenRouter / OpenAI-compatible, Claude and the AICredits fallback. No call is made here."""
    from studio.agents.aicredits import AiCreditsFallback
    from studio.agents.chat import ClaudeChat, OpenAiCompatChat
    from studio.agents.llm import Llm, gemini_client

    providers = {"nvidia": (s.nvidia_base_url, s.nvidia_api_key), "groq": (s.groq_base_url, s.groq_api_key),
                 "openrouter": (s.openrouter_base_url, s.openrouter_api_key), "openai": (s.openai_base_url, s.openai_api_key)}
    chat = OpenAiCompatChat(providers, openai_in_per_m=s.openai_price_in_usd_per_1m, openai_out_per_m=s.openai_price_out_usd_per_1m,
                            inr_per_usd=s.inr_per_usd)
    aicredits = AiCreditsFallback(OpenAiCompatChat({"aicredits": (s.aicredits_base_url, s.aicredits_api_key)}), ledger, pricing, budget, router,
                                  s.aicredits_fallback_enabled and bool(s.aicredits_api_key))
    return Llm(ledger, pricing, budget, chat, ClaudeChat(s.anthropic_api_key), aicredits, gemini_client(s.gemini_api_key) if s.gemini_api_key else None,
               timeout_seconds=s.agents_timeout_seconds, model=s.text_model, fallbacks=s.text_fallbacks, effort_overrides=s.claude_effort_overrides)


def build_dream_gpu(s: Settings, db: Db, pricing: Pricing, ledger: CostLedger, dream_settings: DreamSettings) -> DreamGpu:
    """The Dream door to the GPU broker. Like Java's bean it always exists; with DREAM_GPU_PROVIDER=modal (default) it is disabled and its
    broker has no provider, so nothing can be rented. With =broker it holds RunPod (the studio's default provider; the others need their own
    config) and even then nothing is rented until a Short asks for pictures or clips. Every rental goes through GpuBroker.acquire()
    (self-stop wrapper + watchdog lease)."""
    from studio.gpu.broker import support
    from studio.gpu.broker.broker import GpuBroker
    from studio.gpu.broker.config import BrokerConfig
    from studio.gpu.broker.records import PgBrokerRecords
    from studio.gpu.broker.runpod import RunPodProvider
    from studio.gpu.broker.wallet import GpuWallet
    from studio.gpu.events import PgPodEvents
    from studio.gpu.leases import PgPodLeases
    from studio.gpu.pods import GpuPodLimits, RunPodApi

    on = s.dream_gpu_provider.lower() == "broker"
    cfg = BrokerConfig.load(s.config_dir)
    providers = []
    if on:
        api = RunPodApi(s.runpod_base_url, s.runpod_api_key)
        providers.append(RunPodProvider(api, s.runpod_base_url, s.runpod_api_key))
    prefs = support.PgPreferences(db)
    broker = GpuBroker(providers, cfg, GpuWallet.from_db(cfg, db, pricing), PgBrokerRecords(db, pricing), PgPodLeases(db), PgPodEvents(db, ledger, pricing),
                       GpuPodLimits.from_settings(s), prefs=prefs, auth=support.WorkerAuth(), states=support.PgStates(db), boot_timings=support.PgTimings(db))
    estimate = RentalEstimate(fallback_hours=cfg.expected_hours)
    return DreamGpu(broker, db, prefs, pricing, "broker" if on else "modal", start_seconds=estimate.start_seconds, estimate=estimate,
                    worst_case_hourly_usd=s.gpu_max_hourly_usd, warm_image=os.environ.get("GPU_WARM_IMAGE", ""),
                    volume_id=os.environ.get("GPU_POD_VOLUME_ID", ""),
                    image=os.environ.get("GPU_POD_IMAGE", "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"))


def build_dream_context(db: Db, s: Settings, *, llm: Any = None, gateway: Any = None, gpu: Any = None, assets: Any = None,
                        producer_service: Any = None, dream_settings: DreamSettings | None = None,
                        clock: Any = None) -> DreamContext:
    """Every part can be injected; what is not given is built from Settings. Nothing is started and nothing is spent."""
    ledger = CostLedger(db)
    pricing = Pricing(s.config_dir)
    budget = BudgetGuard(db, pricing)
    router = Router(db, budget, pricing)
    assets = assets or AssetStore(db)
    llm = llm or build_llm(s, ledger, pricing, budget, router)
    dream_settings = dream_settings or DreamSettings.from_env(dict(os.environ))
    gpu = gpu if gpu is not None else build_dream_gpu(s, db, pricing, ledger, dream_settings)
    gateway = gateway or LazyGateway(s.temporal_address)
    clip_progress = ClipProgress()
    lessons = DreamLessons(db)
    dreams = DreamService(db, llm, assets, pricing, budget, router, clip_progress, dream_settings, gpu, lessons)
    if producer_service is None:  # the real AI Producer control side (trust switch, agreement score, learned rules)
        deps = ProductionDeps(db, None, None, assets, ledger, pricing, budget, StylePresets(s.config_dir), text_model=s.text_model)
        producer_service = ProducerService(deps, llm=llm)
    dream_producer = DreamProducer(db, dreams, producer_service, pricing)
    studio = DreamStudio(db, gateway, dreams, clip_progress, dream_producer)
    autopilot = AutopilotService(db, studio, llm, s.autopilot_zone, clock, s.youtube_token_file)
    return DreamContext(s, db, pricing, dreams, studio, lessons, gpu, dream_producer, autopilot, gateway, clip_progress)


def now_utc() -> datetime:
    return datetime.now(UTC)
