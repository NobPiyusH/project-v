"""Base of every production stage: shared dependencies and read/write helpers (script row lock, plan, shots...). Port of StageSupport
and ProductionDeps."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from studio.agents.jfmt import jround
from studio.agents.types import Act, Concept
from studio.assets.asset_store import AssetStore
from studio.db import Db
from studio.ledger import BudgetGuard, CostLedger, Pricing
from studio.media.types import Fact
from studio.media.wire import from_json, to_json
from studio.planner.plan import VideoPlan
from studio.planner.presets import StylePresets
from studio.production.records import StoredLine, StoredScript

_DIAGRAM_LANE = re.compile(r'"lane"\s*:\s*"diagram"')
_STOCK_LANE = re.compile(r'"lane"\s*:\s*"stock"')


@dataclass
class ProductionDeps:
    """Shared dependencies of the production stages (one object, so each stage class stays a one-line constructor)."""

    db: Db
    writer: Any
    director: Any
    assets: AssetStore
    ledger: CostLedger
    pricing: Pricing
    budget: BudgetGuard
    presets: StylePresets
    text_model: str = ""
    tts_model: str = ""
    image_model: str = ""
    music_model: str = ""
    default_voice: str = "Charon"
    modal_app: str = ""
    modal_class: str = ""


def stock_shot(shot: dict[str, Any] | None) -> bool:
    """True for a shot in the stock lane (lane = stock): no keyframe, no AI clip. A shot that found no clip is reverted to ai_video."""
    return bool(shot) and isinstance(shot.get("prompt_json"), str) and _STOCK_LANE.search(shot["prompt_json"]) is not None


def diagram_shot(shot: dict[str, Any] | None) -> bool:
    """True for a shot the Director gave the diagram lane (prompt_json has lane = diagram): no keyframe, no AI clip."""
    return bool(shot) and isinstance(shot.get("prompt_json"), str) and _DIAGRAM_LANE.search(shot["prompt_json"]) is not None


def round3(v: float) -> float:
    return jround(v * 1000.0) / 1000.0


def with_lines(s: StoredScript, lines: list[StoredLine]) -> StoredScript:
    return StoredScript(s.version, lines, s.voice_name, s.voice_style, s.music_brief, s.line_starts, s.tempo)


class StageSupport:
    def __init__(self, d: ProductionDeps):
        self.d = d
        self.db = d.db

    def plan(self, video_id: uuid.UUID) -> VideoPlan:
        rows = self.db.rows("SELECT plan_json::text AS p FROM video WHERE id = %(v)s", {"v": video_id})
        if not rows:
            raise LookupError(f"video {video_id}: expected 1 row, got 0")
        if rows[0]["p"] is None:
            raise RuntimeError("Video has no plan yet")
        return VideoPlan.model_validate(json.loads(rows[0]["p"])["plan"])

    def prompt(self, video_id: uuid.UUID) -> str:
        return self.db.rows("SELECT prompt FROM video WHERE id = %(v)s", {"v": video_id})[0]["prompt"]

    def facts(self, video_id: uuid.UUID) -> list[Fact]:
        rows = self.db.rows("SELECT facts_json::text AS f FROM research WHERE video_id = %(v)s ORDER BY version DESC LIMIT 1", {"v": video_id})
        return [from_json(Fact, f) for f in json.loads(rows[0]["f"])] if rows else []

    def latest_concept(self, video_id: uuid.UUID) -> Concept | None:
        rows = self.db.rows("SELECT big_idea, clock_definition::text AS clock, acts_json::text AS acts FROM concept "
                            "WHERE video_id = %(v)s ORDER BY version DESC LIMIT 1", {"v": video_id})
        if not rows:
            return None
        r = rows[0]
        clock = json.loads(r["clock"]) or {}
        acts = [Act.model_validate(a) for a in json.loads(r["acts"]) or []]
        return Concept(logline=clock.get("logline"), bigIdea=r["big_idea"], clockLabel=clock.get("label"), acts=acts,
                       emotionalArc=clock.get("emotionalArc"))

    def latest_script(self, video_id: uuid.UUID) -> StoredScript | None:
        rows = self.db.rows("SELECT beat_sheet_json::text AS b FROM script WHERE video_id = %(v)s ORDER BY version DESC LIMIT 1", {"v": video_id})
        return from_json(StoredScript, json.loads(rows[0]["b"])) if rows else None

    def shots(self, video_id: uuid.UUID) -> list[dict[str, Any]]:
        return self.db.rows(
            """
            SELECT s.id, s.ord, s.duration_target_sec, s.shot_type, s.camera_move, s.description,
                   s.prompt_json::text AS prompt_json, s.priority, s.overlay_text, s.keyframe_asset_id,
                   s.status, s.error, s.draft_clip_asset_id, s.final_clip_asset_id, s.clip_status,
                   s.clip_error, s.final_wanted, s.footage_asset_id, s.smooth_clip_asset_id, s.sharp_clip_asset_id,
                   a.uri_hot AS keyframe_uri, p.provider AS keyframe_provider, sc.title AS scene_title
            FROM shot s JOIN scene sc ON sc.id = s.scene_id
            LEFT JOIN asset a ON a.id = s.keyframe_asset_id
            LEFT JOIN provenance p ON p.id = a.provenance_id
            WHERE sc.video_id = %(v)s ORDER BY s.ord
            """,
            {"v": video_id},
        )

    def beat_times(self, video_id: uuid.UUID) -> list[float]:
        rows = self.db.rows("SELECT beat_times_json::text AS b FROM music_cue WHERE video_id = %(v)s", {"v": video_id})
        return [float(x) for x in json.loads(rows[0]["b"])] if rows else []

    def bpm(self, video_id: uuid.UUID) -> float:
        rows = self.db.rows("SELECT bpm FROM music_cue WHERE video_id = %(v)s", {"v": video_id})
        return float(rows[0]["bpm"]) if rows else 0.0

    def next_version(self, table: str, video_id: uuid.UUID) -> int:
        if table not in ("research", "concept", "script"):  # table names are fixed constants from the stages, never user input
            raise ValueError(table)
        return self.db.rows(f"SELECT COALESCE(max(version), 0) + 1 AS n FROM {table} WHERE video_id = %(v)s", {"v": video_id})[0]["n"]

    def next_keyframe_attempt(self, video_id: uuid.UUID) -> int:
        return self.db.rows("SELECT count(*) + 1 AS n FROM asset WHERE video_id = %(v)s AND kind = 'image'", {"v": video_id})[0]["n"]

    def lock_script(self, video_id: uuid.UUID) -> StoredScript:
        """Reads the latest script with a row lock, so concurrent redos/edits can't overwrite each other (call inside db.transaction())."""
        rows = self.db.rows("SELECT beat_sheet_json::text AS b FROM script WHERE video_id = %(v)s "
                            "AND version = (SELECT max(version) FROM script WHERE video_id = %(v)s) FOR UPDATE", {"v": video_id})
        if not rows:
            raise RuntimeError("This video has no script yet")
        return from_json(StoredScript, json.loads(rows[0]["b"]))

    def write_script(self, video_id: uuid.UUID, s: StoredScript) -> None:
        md = "".join(("" if line.spoken else "_[visual]_ ") + line.text + "\n\n" for line in s.lines)
        words = sum(len(line.text.split()) for line in s.lines if line.spoken)
        self.db.execute(
            "UPDATE script SET beat_sheet_json = CAST(%(b)s AS jsonb), narration_md = %(md)s, word_count = %(w)s, updated_at = now() "
            "WHERE video_id = %(v)s AND version = %(ver)s",
            {"b": json.dumps(to_json(s)), "md": md, "w": words, "v": video_id, "ver": s.version},
        )
