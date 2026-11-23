"""Start-up of the Python Dream worker: it refuses to run unless asked, listens only on its own queue, and registers every activity and workflow
(built on Temporal's local test server: no network, nothing rented)."""

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("temporalio")

from studio import worker  # noqa: E402
from studio.api.wiring import build_dream_context  # noqa: E402
from studio.config import Settings  # noqa: E402
from studio.tests.dworld import FakeGateway, FakeLlm, make_world  # noqa: E402
from studio.workflow import queues  # noqa: E402


def test_the_worker_refuses_to_start_unless_the_flag_is_set(capsys):
    assert worker.enabled({}) is False and worker.enabled({"STUDIO_TEMPORAL_WORKER": "false"}) is False
    assert worker.enabled({"STUDIO_TEMPORAL_WORKER": "TRUE"}) is True
    assert worker.main({}) == 2
    assert "STUDIO_TEMPORAL_WORKER=true" in capsys.readouterr().out


def test_the_worker_is_built_on_its_own_queue_with_every_activity_registered(scratch_db):
    from temporalio.testing import WorkflowEnvironment

    make_world(scratch_db)
    ctx = build_dream_context(scratch_db, Settings(), llm=FakeLlm(), gateway=FakeGateway(), producer_service=SimpleNamespace(deciding=lambda: False))

    async def main():
        env = await WorkflowEnvironment.start_time_skipping()
        try:
            w = worker.build(env.client, ctx)
            return w.task_queue
        finally:
            await env.shutdown()

    assert asyncio.run(main()) == queues.CONTROL == "projectv-control-py", "never the Java queue (projectv-control)"


def test_the_one_worker_for_video_dream_and_post_registers_every_python_workflow_on_the_python_queue(scratch_db):
    from temporalio.testing import WorkflowEnvironment

    from studio.api.video_wiring import build_video_context

    make_world(scratch_db)
    stub = SimpleNamespace(deciding=lambda: False)
    ctx = build_dream_context(scratch_db, Settings(), llm=FakeLlm(), gateway=FakeGateway(), producer_service=stub)
    vctx = build_video_context(scratch_db, Settings(), llm=FakeLlm(), gateway=FakeGateway(), dream_ctx=ctx, researcher=object())

    async def main():
        env = await WorkflowEnvironment.start_time_skipping()
        try:
            return worker.build(env.client, ctx, vctx).task_queue
        finally:
            await env.shutdown()

    assert asyncio.run(main()) == queues.CONTROL
