"""Small, serialisable types passed through Temporal (port of WorkflowTypes). Never media bytes: only IDs, text and small JSON."""

from __future__ import annotations

from dataclasses import dataclass, field

from studio.workflow.stage import Stage, StageMode


@dataclass
class VideoRequest:
    """Workflow input. modes holds per-stage overrides; stages not listed use default_mode. only_stages: None for a normal production
    run; for a re-run, exactly the stages to run again (in stage order). Other stages keep their earlier output and are not touched."""

    video_id: str
    prompt: str
    default_mode: StageMode = StageMode.AUTO
    modes: dict[Stage, StageMode] = field(default_factory=dict)
    only_stages: list[Stage] | None = None
    review_minutes: int | None = None


@dataclass
class StageResult:
    """Result of one stage attempt. planned_stages is only set by the PLAN stage and decides which later stages run."""

    summary: str
    artifact_json: str | None = None
    planned_stages: list[Stage] | None = None

    @staticmethod
    def of(summary: str, artifact_json: str | None) -> StageResult:
        return StageResult(summary, artifact_json, None)


@dataclass
class InspectionReport:
    """Inspector verdict for one stage attempt. notes are the FAIL findings written as redo instructions."""

    verdict: str
    summary: str | None = None
    notes: str | None = None
    redo_targets: list[str] = field(default_factory=list)


SKIPPED = InspectionReport("PASS", None, None, [])


@dataclass
class StageView:
    stage: Stage
    mode: StageMode
    status: str
    attempt: int
    summary: str | None


@dataclass
class WorkflowStatus:
    phase: str
    current_stage: Stage | None
    paused: bool
    default_mode: StageMode
    modes: dict[Stage, StageMode]
    stages: list[StageView]
