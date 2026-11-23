"""Start-up of the Python Temporal worker for Dream Shorts, the Dream GPU controls' workflows and the autopilot's post workflow:

    STUDIO_TEMPORAL_WORKER=true uv run python -m studio.worker

It listens on its OWN task queue (projectv-control-py), so the Java worker on projectv-control keeps every running workflow. It refuses to start
unless STUDIO_TEMPORAL_WORKER=true, and it never rents or pays for anything by itself: it only runs workflows someone started on its queue.
The video pipeline's worker (studio.workflow.runtime.build_worker) needs its own wiring (writer, director, inspector, voices...) and is not
started here yet."""

from __future__ import annotations

import asyncio
import logging
import sys

from studio.api.wiring import build_dream_context
from studio.config import Settings
from studio.db import ThreadLocalPgDb
from studio.workflow.autopilot_workflow import AutopilotActivities
from studio.workflow.dream_activities import DreamActivities, DreamProducerActivities
from studio.workflow.runtime import TemporalGateway, build_dream_worker

log = logging.getLogger("studio.worker")


def enabled(env: dict[str, str]) -> bool:
    return env.get("STUDIO_TEMPORAL_WORKER", "false").lower() == "true"


def build(client, ctx):
    """The worker for a connected client and a built context (separate so tests can build it on a test server)."""
    return build_dream_worker(client, DreamActivities(ctx.dreams), DreamProducerActivities(ctx.dream_producer), AutopilotActivities(ctx.autopilot))


async def run(settings: Settings) -> None:
    from temporalio.client import Client

    client = await Client.connect(settings.temporal_address)
    ctx = build_dream_context(ThreadLocalPgDb(settings.dsn), settings, gateway=TemporalGateway(client, asyncio.get_running_loop()))
    log.info("Python Dream worker listening on projectv-control-py at %s", settings.temporal_address)
    await build(client, ctx).run()


def main(env: dict[str, str] | None = None) -> int:
    import os

    env = dict(os.environ) if env is None else env
    if not enabled(env):
        print("Not starting: set STUDIO_TEMPORAL_WORKER=true to run the Python worker (the Java worker stays in charge until then).")
        return 2
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(Settings.from_env()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
