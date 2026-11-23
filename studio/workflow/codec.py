"""JSON shapes that travel through Temporal. Workflow code and activities exchange plain JSON-compatible dicts with camelCase keys, the same
shape the Java control plane and the Python media worker already use, so a request built by a control activity goes straight into a media
activity and its result straight into a save activity (the workflow never touches media bytes)."""

from __future__ import annotations

from typing import Any

from studio.media.wire import to_json
from studio.workflow.stage import Stage, StageMode
from studio.workflow.types import InspectionReport, StageResult, VideoRequest


def stage_result_json(r: StageResult) -> dict[str, Any]:
    return {"summary": r.summary, "artifactJson": r.artifact_json,
            "plannedStages": None if r.planned_stages is None else [s.name for s in r.planned_stages]}


def inspection_report_json(r: InspectionReport) -> dict[str, Any]:
    return {"verdict": r.verdict, "summary": r.summary, "notes": r.notes, "redoTargets": list(r.redo_targets or [])}


def video_request_json(r: VideoRequest) -> dict[str, Any]:
    return {"videoId": r.video_id, "prompt": r.prompt, "defaultMode": r.default_mode.name,
            "modes": {k.name: v.name for k, v in r.modes.items()},
            "onlyStages": None if r.only_stages is None else [s.name for s in r.only_stages], "reviewMinutes": r.review_minutes}


def video_request_from_json(d: dict[str, Any]) -> VideoRequest:
    return VideoRequest(d["videoId"], d["prompt"], StageMode.parse(d.get("defaultMode")),
                        {Stage[k]: StageMode.parse(v) for k, v in (d.get("modes") or {}).items()},
                        None if d.get("onlyStages") is None else [Stage[s] for s in d["onlyStages"]], d.get("reviewMinutes"))


__all__ = ["inspection_report_json", "stage_result_json", "to_json", "video_request_from_json", "video_request_json"]
