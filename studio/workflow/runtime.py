"""Temporal plumbing of the Python control plane: the gateway VideoService uses to start workflows and signal them, and the worker that runs
the workflows and control activities. Everything here is wiring; the logic lives in video_production.py and activities.py.

Nothing starts by itself: a worker only runs when `run_worker` is called (S7, after the owner's go), so the Java worker stays the only one on its queue.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any

from studio.workflow import queues
from studio.workflow.codec import video_request_json
from studio.workflow.types import VideoRequest


def _plain(v: Any) -> Any:
    return v.name if isinstance(v, Enum) else v


class TemporalGateway:
    """Sync facade over the async Temporal client (VideoService is sync). Owns a small background event loop unless one is given."""

    def __init__(self, client: Any, loop: asyncio.AbstractEventLoop | None = None):
        self._client = client
        if loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="temporal-gateway", daemon=True).start()
        self._loop = loop

    def _run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=30)

    def start(self, workflow: str, workflow_id: str, arg: Any) -> None:
        payload = video_request_json(arg) if isinstance(arg, VideoRequest) else arg
        self._run(self._client.start_workflow(workflow, payload, id=workflow_id, task_queue=queues.CONTROL))

    def signal(self, workflow_id: str, name: str, *args: Any) -> None:
        handle = self._client.get_workflow_handle(workflow_id)
        try:
            self._run(handle.signal(name, args=[_plain(a) for a in args]))
        except Exception as e:  # a signal to a workflow that already ended and left the server is answered NOT_FOUND
            from temporalio.service import RPCError, RPCStatusCode

            if isinstance(e, RPCError) and e.status == RPCStatusCode.NOT_FOUND:
                from studio.dream.dream_studio import WorkflowNotFoundError

                raise WorkflowNotFoundError(workflow_id) from e
            raise

    def query(self, workflow_id: str, name: str) -> Any:
        return self._run(self._client.get_workflow_handle(workflow_id).query(name))


def build_worker(client: Any, control: Any, producer: Any, executor: ThreadPoolExecutor | None = None) -> Any:
    """The control worker: all Python workflows and every `control.*` / `producer.*` activity on the control queue."""
    from temporalio.worker import Worker

    from studio.workflow.activities import activity_functions
    from studio.workflow.video_production import WORKFLOWS

    return Worker(client, task_queue=queues.CONTROL, workflows=WORKFLOWS, activities=activity_functions(control, producer),
                  activity_executor=executor or ThreadPoolExecutor(max_workers=16))


def build_dream_worker(client: Any, dream: Any, dream_producer: Any, autopilot: Any, executor: ThreadPoolExecutor | None = None) -> Any:
    """The Dream Shorts worker (same control queue): the Dream workflow and `dream.*` / `dreamProducer.*` activities, and the post workflow
    with `autopilot.*`. Like build_worker, nothing starts until S7 calls run() on it after the owner's go."""
    from temporalio.worker import Worker

    from studio.workflow import autopilot_workflow, dream_activities, dream_workflow

    return Worker(client, task_queue=queues.CONTROL, workflows=dream_workflow.WORKFLOWS + autopilot_workflow.WORKFLOWS,
                  activities=dream_activities.activity_functions(dream, dream_producer) + autopilot_workflow.activity_functions(autopilot),
                  activity_executor=executor or ThreadPoolExecutor(max_workers=16))


def build_all_worker(client: Any, control: Any, producer: Any, dream: Any, dream_producer: Any, autopilot: Any,
                     executor: ThreadPoolExecutor | None = None) -> Any:
    """ONE worker for the whole Python control queue: video workflows and activities, Dream workflows and activities, and the post workflow.
    Temporal lets a process run one worker per queue, and a task for a workflow type this worker did not register would fail, so every Python
    workflow type is registered together. Nothing starts until S7 calls run() on it after the owner's go."""
    from temporalio.worker import Worker

    from studio.workflow import autopilot_workflow, dream_activities, dream_workflow
    from studio.workflow.activities import activity_functions
    from studio.workflow.video_production import WORKFLOWS

    return Worker(client, task_queue=queues.CONTROL, workflows=WORKFLOWS + dream_workflow.WORKFLOWS + autopilot_workflow.WORKFLOWS,
                  activities=activity_functions(control, producer) + dream_activities.activity_functions(dream, dream_producer)
                  + autopilot_workflow.activity_functions(autopilot),
                  activity_executor=executor or ThreadPoolExecutor(max_workers=16))
