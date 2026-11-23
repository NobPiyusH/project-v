"""Shared helpers for Dream Shorts tests on a scratch Postgres: real ledger, budget guard, router and asset store; fake LLM, gateway and GPU."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

from studio.assets.asset_store import LICENCE_ZIMAGE as LIC_Z
from studio.assets.asset_store import AssetStore, NewAsset
from studio.dream.dream_studio import DreamStudio
from studio.dream.producer import DreamProducer
from studio.dream.service import DreamService
from studio.dream.settings import DreamSettings
from studio.ledger import BudgetGuard
from studio.routing.router import Router
from studio.tests.test_pricing import at
from studio.video.clip_progress import ClipProgress

BED = "s3://projectv/music-beds/my track.m4a"


class FakeLlm:
    """Records the calls and answers with what the test queued for the purpose."""

    def __init__(self) -> None:
        self.answers: dict[str, Any] = {}
        self.calls: list[tuple[str, str, str, Any, Any]] = []

    def call(self, video_id, purpose, system, user, tp, preferred=None):
        self.calls.append((purpose, system, user, tp, preferred))
        a = self.answers.get(purpose)
        return a(system, user) if callable(a) else a


class FakeGateway:
    def __init__(self) -> None:
        self.started: list[tuple[str, str, Any]] = []
        self.signals: list[tuple[str, str, tuple]] = []
        self.views: dict[str, Any] = {}
        self.gone: set[str] = set()

    def start(self, workflow, workflow_id, arg):
        self.started.append((workflow, workflow_id, arg))

    def signal(self, workflow_id, name, *args):
        if workflow_id in self.gone:
            from studio.dream.dream_studio import WorkflowNotFoundError
            raise WorkflowNotFoundError(workflow_id)
        self.signals.append((workflow_id, name, args))

    def query(self, workflow_id, name):
        if workflow_id not in self.views:
            raise LookupError(workflow_id)
        return self.views[workflow_id]


def provider(db, id_: str, caps: list[str], kind: str = "gpu_serverless", cfg: str = '{"quality":6,"budget":"gpu"}') -> None:
    db.execute("INSERT INTO provider (id, name, kind, capabilities_json, config_json, enabled) VALUES (%(id)s, %(id)s, %(k)s, "
               "CAST(%(c)s AS jsonb), CAST(%(cfg)s AS jsonb), true) ON CONFLICT (id) DO UPDATE SET capabilities_json = EXCLUDED.capabilities_json, "
               "config_json = EXCLUDED.config_json", {"id": id_, "k": kind, "c": json.dumps(caps), "cfg": cfg})


def make_world(db, gpu=None, lessons=None, music_bed=BED, **settings_kw):
    pricing = at("2026-11-19")
    budget = BudgetGuard(db, pricing)
    router = Router(db, budget, pricing)
    db.execute("INSERT INTO provider (id, name, kind, capabilities_json, config_json, enabled) VALUES ('local_laptop', 'Laptop', 'local', "
               "CAST('[\"ffmpeg\"]' AS jsonb), CAST('{}' AS jsonb), true) ON CONFLICT (id) DO NOTHING")
    provider(db, "modal_zimage", ["image.dream"])
    provider(db, "modal_wan", ["video.i2v.dream"])
    provider(db, "gemini_lyria", ["music.dream"], "api", '{"quality":7,"budget":"gemini"}')
    llm = FakeLlm()
    settings = DreamSettings(music_bed=music_bed, **settings_kw)
    dreams = DreamService(db, llm, AssetStore(db), pricing, budget, router, ClipProgress(), settings, gpu, lessons)
    gateway = FakeGateway()
    producer_service = SimpleNamespace(deciding=lambda: False, mark_acted=lambda *a: None)
    producer = DreamProducer(db, dreams, producer_service, pricing)
    studio = DreamStudio(db, gateway, dreams, dreams.clip_progress, producer)
    return SimpleNamespace(db=db, dreams=dreams, studio=studio, producer=producer, llm=llm, gateway=gateway, pricing=pricing, router=router,
                           budget=budget)


def seed_short(db, shots: int = 2, idea: str = "a pink pier", **cols) -> tuple[uuid.UUID, list[uuid.UUID]]:
    dream = uuid.uuid4()
    db.execute("INSERT INTO dream_short (id, idea, status) VALUES (%(id)s, %(i)s, 'RUNNING')", {"id": dream, "i": idea})
    ids = []
    for o in range(shots):
        s = uuid.uuid4()
        ids.append(s)
        db.execute("INSERT INTO dream_shot (id, dream_short_id, ord, scene, picture_prompt, motion_prompt) "
                   "VALUES (%(id)s, %(d)s, %(o)s, 'scene', 'picture prompt', 'walk forward')", {"id": s, "d": dream, "o": o})
    for k, v in cols.items():
        db.execute(f"UPDATE dream_short SET {k} = %(v)s WHERE id = %(d)s", {"v": v, "d": dream})
    return dream, ids


def asset(db, uri: str) -> uuid.UUID:
    video = uri.endswith(".mp4")
    return AssetStore(db).create(NewAsset(None, "video" if video else "image", uri, "video/mp4" if video else "image/png", 480, 832, None,
                                          "test", "m", "p", 1, LIC_Z, True))


def count(db, sql: str, **p) -> int:
    return int(db.rows(sql.replace(":d", "%(d)s"), p)[0]["count"])
