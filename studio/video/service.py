"""Owner-facing operations on a video. Port of VideoService.

The workflow is the source of truth for what happens next; the database mirrors it for the dashboard. Mode changes and decisions are
signalled to the workflow AND recorded here (approval and preference_event rows) so taste learning can use them later.
Temporal is behind `WorkflowGateway`, so everything here runs on fakes in tests.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from studio.db import Db
from studio.planner.imported import initial_plan_json
from studio.workflow import queues
from studio.workflow.stage import Stage, StageMode
from studio.workflow.types import VideoRequest

log = logging.getLogger(__name__)

# What must be redone together, so outputs never go stale: a new script needs a new voice, new shots, new keyframes and a new animatic;
# a new voice only needs a new animatic.
REDO_WITH: dict[Stage, list[Stage]] = {
    Stage.RESEARCH: [Stage.RESEARCH],
    Stage.CONCEPT: [Stage.CONCEPT],
    Stage.SCRIPT: [Stage.SCRIPT, Stage.NARRATION, Stage.SHOT_PLAN, Stage.KEYFRAMES, Stage.ANIMATIC],
    Stage.MUSIC: [Stage.MUSIC, Stage.ANIMATIC],
    Stage.NARRATION: [Stage.NARRATION, Stage.ANIMATIC],
    Stage.SHOT_PLAN: [Stage.SHOT_PLAN, Stage.KEYFRAMES, Stage.ANIMATIC],
    Stage.KEYFRAMES: [Stage.KEYFRAMES, Stage.ANIMATIC],
    Stage.ANIMATIC: [Stage.ANIMATIC],
    # Clips cost GPU money: each runs alone, after showing its estimate (finals follow drafts on request).
    Stage.DRAFT_GEN: [Stage.DRAFT_GEN],
    Stage.FINAL_GEN: [Stage.FINAL_GEN],
    Stage.UPSCALE: [Stage.UPSCALE, Stage.ASSEMBLY, Stage.MOON_LAYER],
    Stage.ASSEMBLY: [Stage.ASSEMBLY, Stage.MOON_LAYER],  # the Moon layer skips itself for a video without a moon block
    Stage.MOON_LAYER: [Stage.MOON_LAYER],
}

_IN_PRODUCTION = ("RUNNING", "WAITING", "PAUSED", "CREATED")


class WorkflowGateway(Protocol):
    """The Temporal client, as the service needs it."""

    def start(self, workflow: str, workflow_id: str, arg: Any) -> None: ...

    def signal(self, workflow_id: str, name: str, *args: Any) -> None: ...


class ProducerHooks(Protocol):
    def record_owner_decision(self, video_id: uuid.UUID, stage: str, decision: str, notes: str | None) -> None: ...

    def learn(self, video_id: uuid.UUID, stage: str) -> None: ...


@dataclass
class CreateVideo:
    prompt: str
    default_mode: StageMode | None = None
    modes: dict[Stage, StageMode] | None = None
    review_minutes: int | None = None
    budget_inr: float | None = None  # this video's own spend cap in INR (0 = no cap); None = the default in config/providers.yaml
    plan_doc: dict | None = None  # a finished plan file (studio/planner/imported.py): the video starts from it, no paid planner/writer/director


def modes_json(default_mode: StageMode, modes: dict[Stage, StageMode]) -> str:
    return json.dumps({"default": default_mode.name, "stages": {k.name: v.name for k, v in modes.items()}}, separators=(",", ":"))


class VideoService:
    def __init__(self, db: Db, gateway: WorkflowGateway, producer: ProducerHooks | None = None,
                 spawn: Callable[[Callable[[], None]], None] | None = None):
        self.db = db
        self.gateway = gateway
        self.producer = producer
        self._spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True).start())

    # ------------------------------------------------------------------------------------------------ starting work

    def create(self, req: CreateVideo) -> uuid.UUID:
        """Not in a transaction on purpose: the row must be committed before the workflow's first activity reads it."""
        vid = uuid.uuid4()
        workflow_id = f"video-{vid}"
        default_mode = req.default_mode or StageMode.AUTO
        modes = dict(req.modes or {})
        review = 10 if req.review_minutes is None or req.review_minutes < 1 else min(req.review_minutes, 24 * 60)
        prompt = req.prompt.strip()
        plan_json = None if req.plan_doc is None else initial_plan_json(req.plan_doc)  # validates: a bad plan is refused before anything is stored
        self.db.execute(
            "INSERT INTO video (id, prompt, status, modes_json, workflow_id, review_minutes, plan_json, budget_inr) "
            "VALUES (%(id)s, %(prompt)s, 'CREATED', CAST(%(modes)s AS jsonb), %(wf)s, %(review)s, CAST(%(plan)s AS jsonb), %(budget)s)",
            {"id": vid, "prompt": prompt, "modes": modes_json(default_mode, modes), "wf": workflow_id, "review": review, "plan": plan_json,
             "budget": req.budget_inr})
        self.gateway.start(queues.VIDEO_WORKFLOW, workflow_id, VideoRequest(str(vid), prompt, default_mode, modes, None, review))
        return vid

    def rerun(self, vid: uuid.UUID, stage: Stage) -> list[Stage]:
        """Redo one stage of a finished video, reusing everything else. Stages before the animatic also rebuild the animatic so the
        owner can watch the change. The workflow id is reused (allowed once the previous run closed)."""
        v = self._video(vid, "prompt, status, modes_json::text AS modes, review_minutes")
        if v["status"] in _IN_PRODUCTION:
            raise RuntimeError(f"The video is still in production ({v['status']}); finish or let it complete first")
        stages = REDO_WITH.get(stage)
        if stages is None:
            raise RuntimeError(f"{stage.label} can't be redone yet (it isn't built until a later milestone)")
        modes = json.loads(v["modes"])
        default_mode = StageMode.parse(str(modes.get("default", "AUTO")))
        self.gateway.start(queues.VIDEO_WORKFLOW, f"video-{vid}",
                           VideoRequest(str(vid), v["prompt"], default_mode, {}, list(stages), int(v["review_minutes"])))
        self.db.execute("UPDATE video SET status = 'RUNNING', current_stage = %(s)s, updated_at = now() WHERE id = %(id)s",
                        {"s": stage.name, "id": vid})
        self._record_decision(vid, stage, "rerun", "[" + ", ".join(s.name for s in stages) + "]")
        return stages

    def redo_shots(self, vid: uuid.UUID, shot_ids: list[uuid.UUID] | None, notes: str | None, policy: str | None) -> str:
        """Starts a per-shot redo; returns the redo's workflow id. Blocked only while the main run is rendering keyframes."""
        if not shot_ids:
            raise RuntimeError("Pick at least one shot")
        self._block_while(vid, ("KEYFRAMES", "ANIMATIC"), "Keyframes or the animatic are being made right now; wait until that finishes")
        p = "AUTO" if policy is None else policy.upper()
        if p not in ("AUTO", "PREMIUM", "FREE"):
            raise RuntimeError("policy must be AUTO, PREMIUM or FREE")
        workflow_id = f"shot-redo-{vid}-{uuid.uuid4()}"
        self.gateway.start(queues.SHOT_REDO_WORKFLOW, workflow_id,
                           {"videoId": str(vid), "shotIds": [str(s) for s in shot_ids], "ownerNotes": notes, "policy": p})
        self._record_decision(vid, Stage.KEYFRAMES, "shot_redo", f"{notes or ''} [{len(shot_ids)} shot(s), {p}]")
        return workflow_id

    def redo_clips(self, vid: uuid.UUID, shot_ids: list[uuid.UUID] | None, kind: str) -> str:
        """Redo the clips of the given shots (draft or final). Blocked while the main run is making clips."""
        if not shot_ids:
            raise RuntimeError("Pick at least one shot")
        if kind not in ("draft", "final"):
            raise RuntimeError("kind must be draft or final")
        self._block_while(vid, ("DRAFT_GEN", "FINAL_GEN"), "Clips are being made right now; wait until that batch finishes")
        workflow_id = f"clip-redo-{vid}-{uuid.uuid4()}"
        self.gateway.start(queues.CLIP_REDO_WORKFLOW, workflow_id, {"videoId": str(vid), "shotIds": [str(s) for s in shot_ids], "kind": kind})
        self._record_decision(vid, Stage.DRAFT_GEN if kind == "draft" else Stage.FINAL_GEN, "clip_redo", f"{kind} {len(shot_ids)} shot(s)")
        return workflow_id

    def footage(self, vid: uuid.UUID, shot_id: uuid.UUID, query: str | None, nasa_id: str | None, start_sec: float | None) -> str:
        """Real NASA footage for one shot: search (nasa_id None) or use a picked clip. Free."""
        workflow_id = f"footage-{vid}-{uuid.uuid4()}"
        self.gateway.start(queues.FOOTAGE_WORKFLOW, workflow_id,
                           {"videoId": str(vid), "shotId": str(shot_id), "query": query, "nasaId": nasa_id, "startSec": start_sec})
        return workflow_id

    def review_images(self, vid: uuid.UUID) -> str:
        """Creative Director review of the existing images (nothing is regenerated)."""
        n = self.db.rows("SELECT count(*) AS n FROM shot s JOIN scene sc ON sc.id = s.scene_id "
                         "WHERE sc.video_id = %(id)s AND s.keyframe_asset_id IS NOT NULL", {"id": vid})[0]["n"]
        if n == 0:
            raise RuntimeError("This video has no images to review yet")
        workflow_id = f"creative-review-{vid}-{uuid.uuid4()}"
        self.gateway.start(queues.CREATIVE_REVIEW_WORKFLOW, workflow_id, {"videoId": str(vid)})
        return workflow_id

    def redo_lines(self, vid: uuid.UUID, line_indexes: list[int] | None, style_notes: str | None) -> str:
        """Re-voice only the given lines. Blocked while the main run is voicing or building the animatic."""
        if not line_indexes:
            raise RuntimeError("Pick at least one line")
        self._block_while(vid, ("NARRATION", "ANIMATIC"), "The voice or the animatic is being made right now; wait until that finishes")
        workflow_id = f"voice-redo-{vid}-{uuid.uuid4()}"
        self.gateway.start(queues.VOICE_REDO_WORKFLOW, workflow_id,
                           {"videoId": str(vid), "lineIndexes": list(line_indexes), "styleNotes": style_notes})
        self._record_decision(vid, Stage.NARRATION, "voice_redo", f"{style_notes or ''} [{', '.join(str(i) for i in line_indexes)}]")
        return workflow_id

    # ------------------------------------------------------------------------------------------------ reads

    def list(self) -> list[dict[str, Any]]:
        return self.db.rows(
            """
            SELECT v.id, v.title, v.prompt, v.status, v.current_stage, v.spent_inr, v.created_at, v.updated_at, v.master_asset_id,
                   (SELECT s.keyframe_asset_id FROM shot s JOIN scene sc ON sc.id = s.scene_id
                    WHERE sc.video_id = v.id AND s.keyframe_asset_id IS NOT NULL ORDER BY s.ord LIMIT 1) AS thumb_asset_id,
                   (SELECT count(*) FROM shot s JOIN scene sc ON sc.id = s.scene_id WHERE sc.video_id = v.id) AS shots,
                   (SELECT r.summary FROM stage_run r WHERE r.video_id = v.id AND r.stage = v.current_stage
                    ORDER BY r.attempt DESC LIMIT 1) AS current_summary
            FROM video v ORDER BY v.created_at DESC
            """)

    def detail(self, vid: uuid.UUID) -> dict[str, Any]:
        video = self._video(
            vid, "id, title, prompt, status, current_stage, spent_inr, workflow_id, created_at, updated_at, plan_json::text AS plan_json, "
                 "modes_json::text AS modes_json, review_minutes, master_asset_id, master_package_asset_id, master_captions_asset_id")
        runs = self.db.rows(
            "SELECT stage, attempt, mode, status, summary, artifact_json::text AS artifact_json, owner_notes, started_at, finished_at, "
            "review_deadline FROM stage_run WHERE video_id = %(id)s ORDER BY started_at, attempt", {"id": vid})
        return {**video, "stageRuns": runs}

    # ------------------------------------------------------------------------------------------------ owner decisions

    def approve(self, vid: uuid.UUID, stage: Stage, notes: str | None) -> None:
        self._signal(vid, "approve", stage, notes)
        self._record_decision(vid, stage, "approve", notes)

    def reject(self, vid: uuid.UUID, stage: Stage, notes: str | None) -> None:
        self._signal(vid, "reject", stage, notes)
        self._record_decision(vid, stage, "reject", notes)

    def set_mode(self, vid: uuid.UUID, stage: Stage, mode: StageMode) -> None:
        self._signal(vid, "set_mode", stage, mode)
        with self.db.transaction():
            self.db.execute(
                "UPDATE video SET modes_json = jsonb_set(modes_json, ARRAY['stages', %(stage)s], to_jsonb(CAST(%(mode)s AS text)), true), "
                "updated_at = now() WHERE id = %(id)s", {"stage": stage.name, "mode": mode.name, "id": vid})
            self._record_decision(vid, stage, "mode_change", mode.name)

    def set_all_modes(self, vid: uuid.UUID, mode: StageMode) -> None:
        self._signal(vid, "set_all_modes", mode)
        with self.db.transaction():
            self.db.execute("UPDATE video SET modes_json = CAST(%(modes)s AS jsonb), updated_at = now() WHERE id = %(id)s",
                            {"modes": modes_json(mode, {}), "id": vid})
            self._record_decision(vid, None, "mode_change", f"ALL:{mode.name}")

    def set_review_minutes(self, vid: uuid.UUID, minutes: int) -> None:
        m = max(1, min(minutes, 24 * 60))
        self._signal(vid, "set_review_minutes", m)
        self.db.execute("UPDATE video SET review_minutes = %(m)s, updated_at = now() WHERE id = %(id)s", {"m": m, "id": vid})

    def pause(self, vid: uuid.UUID) -> None:
        self._signal(vid, "pause")
        self.db.execute("UPDATE video SET status = 'PAUSED', updated_at = now() WHERE id = %(id)s AND status IN ('RUNNING','WAITING')", {"id": vid})

    def resume(self, vid: uuid.UUID) -> None:
        self._signal(vid, "resume")

    # ------------------------------------------------------------------------------------------------ internals

    def _signal(self, vid: uuid.UUID, name: str, *args: Any) -> None:
        self.gateway.signal(f"video-{vid}", name, *args)

    def _video(self, vid: uuid.UUID, columns: str) -> dict[str, Any]:
        rows = self.db.rows(f"SELECT {columns} FROM video WHERE id = %(id)s", {"id": vid})
        if not rows:
            raise LookupError(f"No video {vid}")
        return rows[0]

    def _block_while(self, vid: uuid.UUID, stages: tuple[str, ...], message: str) -> None:
        v = self._video(vid, "status, current_stage")
        if v["status"] == "RUNNING" and v["current_stage"] in stages:
            raise RuntimeError(message)

    def _record_decision(self, vid: uuid.UUID, stage: Stage | None, decision: str, notes: str | None) -> None:
        stage_name = "ALL" if stage is None else stage.name
        self.db.execute("INSERT INTO approval (id, video_id, stage, decision, notes, actor) "
                        "VALUES (%(aid)s, %(id)s, %(stage)s, %(decision)s, %(notes)s, 'owner')",
                        {"aid": uuid.uuid4(), "id": vid, "stage": stage_name, "decision": decision, "notes": notes})
        # Every owner action is a taste signal (HANDOVER §18).
        kind = "mode_change" if decision == "mode_change" else "rating"
        self.db.execute("INSERT INTO preference_event (id, kind, video_id, context_json, chosen) "
                        "VALUES (%(pid)s, %(kind)s, %(id)s, CAST(%(ctx)s AS jsonb), %(chosen)s)",
                        {"pid": uuid.uuid4(), "kind": kind, "id": vid,
                         "ctx": json.dumps({"stage": stage_name, "decision": decision}, separators=(",", ":")), "chosen": notes})
        # AI Producer (P1): the owner's decision on a stage the Producer reviewed is compared with its recommendation (agreement score)
        # and kept with the owner's notes as a lesson for later reviews.
        if stage is not None and decision in ("approve", "reject") and self.producer is not None:
            producer = self.producer
            producer.record_owner_decision(vid, stage_name, decision, notes)
            # P2: notes are lessons: re-learn this stage's rules in the background (a small text call).
            if notes and notes.strip():
                self._spawn(lambda: self._learn(producer, vid, stage_name))

    @staticmethod
    def _learn(producer: ProducerHooks, vid: uuid.UUID, stage_name: str) -> None:
        try:
            producer.learn(vid, stage_name)
        except Exception as e:  # noqa: BLE001 - background learning must never hurt the owner's action
            log.warning("Producer learning failed: %s", e)
