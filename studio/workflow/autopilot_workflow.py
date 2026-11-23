"""Posting a ready Short to YouTube after the owner tapped Post. Port of PostWorkflow(Impl) and AutopilotActivitiesImpl.

Deterministic (rule 5): the upload is an activity on the media worker. It is tried ONCE: a retry after a lost reply could post the Short
twice, so a failure goes back to the owner as POST_FAILED with the reason, and Post can be tapped again. Never started any other way."""

from __future__ import annotations

import functools
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from studio.workflow import queues

CONTROL = dict(start_to_close_timeout=timedelta(minutes=1), retry_policy=RetryPolicy(maximum_attempts=3))
UPLOAD = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(minutes=30), retry_policy=RetryPolicy(maximum_attempts=1))


def _root(e: BaseException) -> str:
    c = e
    while c.__cause__ is not None:
        c = c.__cause__
    return str(c)


@workflow.defn(name="PostWorkflowPy")
class PostWorkflow:
    @workflow.run
    async def post(self, run_id: str) -> str:
        request = await workflow.execute_activity("autopilot.upload_request", args=[run_id], **CONTROL)
        try:
            result = await workflow.execute_activity("youtube_upload", request, **UPLOAD)
        except ActivityError as failure:
            result = {"videoId": None, "url": None, "privacy": request["privacy"], "error": _root(failure)}
        return await workflow.execute_activity("autopilot.save_upload", args=[run_id, result], **CONTROL)


WORKFLOWS = [PostWorkflow]


class AutopilotActivities:
    """Control-plane side of posting: a thin adapter over AutopilotService (JSON in, JSON out)."""

    def __init__(self, autopilot: Any):
        self.autopilot = autopilot

    def upload_request(self, run_id: str) -> dict[str, Any]:
        from studio.media.wire import to_json

        return to_json(self.autopilot.upload_request(uuid.UUID(run_id)))

    def save_upload(self, run_id: str, result: dict[str, Any]) -> str:
        from studio.autopilot.service import YoutubeUploadResult
        from studio.media.wire import from_json

        return self.autopilot.save_upload(uuid.UUID(run_id), from_json(YoutubeUploadResult, result))


def activity_functions(acts: AutopilotActivities) -> list[Callable[..., Any]]:
    from temporalio import activity

    out: list[Callable[..., Any]] = []
    for name in dir(type(acts)):
        if name.startswith("_"):
            continue
        fn = getattr(acts, name)
        if not callable(fn):
            continue

        @functools.wraps(fn)
        def wrapper(*args: Any, __fn: Callable[..., Any] = fn) -> Any:
            return __fn(*args)

        out.append(activity.defn(name=f"autopilot.{name}")(wrapper))
    return out
