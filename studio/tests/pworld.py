"""Shared helpers for production-stage tests on a scratch Postgres: real ledger, budget guard and asset store; fake writer/director."""

from __future__ import annotations

import json
import uuid

from studio.assets.asset_store import AssetStore
from studio.ledger import BudgetGuard, CostLedger
from studio.media.wire import to_json
from studio.planner.plan import VideoPlan
from studio.planner.presets import StylePresets
from studio.production.records import StoredLine, StoredScript
from studio.production.support import ProductionDeps
from studio.tests.test_pricing import TEST_CONFIG, at


def make_deps(db, writer=None, director=None, **kw) -> ProductionDeps:
    pricing = at("2026-11-19")
    base = dict(text_model="gemini-3.6-flash", tts_model="gemini-3.8-flash-tts", image_model="img", music_model="music",
                default_voice="Charon", modal_app="app", modal_class="cls")
    base.update(kw)
    return ProductionDeps(db, writer, director, AssetStore(db), CostLedger(db), pricing, BudgetGuard(db, pricing),
                          StylePresets(TEST_CONFIG), **base)


def seed_video(db, status="COMPLETED", duration=60, aspect="9:16", with_script=True, narration="full") -> uuid.UUID:
    video = uuid.uuid4()
    plan = VideoPlan(title="Test", summary="s", format="short", durationSec=duration, aspectRatio=aspect, outputResolution="1080p",
                     stylePreset="shorts_punchy", tone="calm", language="en", needsResearch=False, narration=narration, voice="",
                     music="support", soundDesign=True, visualTypes=[], shortsCount=0, mustInclude=[], rationale="")
    db.execute("INSERT INTO video (id, prompt, status, plan_json) VALUES (%(i)s, 'p', %(s)s, CAST(%(p)s AS jsonb))",
               {"i": video, "s": status, "p": json.dumps({"plan": plan.model_dump()})})
    if with_script:
        script = StoredScript(1, [StoredLine(0, "A star is born.", "Act 1", "hook", True), StoredLine(1, "Silence.", "Act 1", "beat", False)],
                              "Charon", "calm", "strings", None, None)
        db.execute("INSERT INTO script (id, video_id, version, beat_sheet_json) VALUES (%(i)s, %(v)s, 1, CAST(%(b)s AS jsonb))",
                   {"i": uuid.uuid4(), "v": video, "b": json.dumps(to_json(script))})
    return video


def one(db, sql, **params):
    return db.rows(sql, params)[0]


def scalar(db, sql, **params):
    return next(iter(db.rows(sql, params)[0].values()))
