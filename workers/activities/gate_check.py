"""Quality gate after the edit (tiers 1-2, judge OFF, free): downloads the master, runs tools.qualitygate, stores the report JSON next to it.

A failing gate raises a non-retryable error, which the workflow handles like any failed stage: the video waits for the owner (reject = redo the
edit, approve = continue without the gate). Warnings come back in the summary and never block. A crash of the checker itself returns
status "error" and does not block.
"""

from __future__ import annotations

from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError

from tools.qualitygate.hook import run_hook
from workers.common import download, log, put_file, workdir

GATE_FAILED = "QualityGateFailed"


@activity.defn(name="quality_gate_check")
def quality_gate_check(req: dict) -> dict:
    """req: {videoId, masterUri, srtUri?} -> {status, summary, reportUri, warnings}"""
    with workdir("gate") as tmp:
        tmp = Path(tmp)
        master = download(req["masterUri"], tmp / "master.mp4")
        srt = download(req["srtUri"], tmp / "captions.srt") if req.get("srtUri") else None
        outcome = run_hook(master, tmp / "quality-gate.json", srt)
        report_uri = None
        if outcome.report_path is not None:
            report_uri = put_file(f"videos/{req['videoId']}/master/quality-gate.json", outcome.report_path, "application/json")
    log.info("Quality gate %s for %s", outcome.status, req["videoId"])
    if outcome.failed:
        raise ApplicationError(f"{outcome.summary} ({report_uri})", type=GATE_FAILED, non_retryable=True)
    return {"status": outcome.status, "summary": outcome.summary, "reportUri": report_uri, "warnings": outcome.warnings}
