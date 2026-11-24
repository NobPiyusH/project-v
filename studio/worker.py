"""Start-up of the Python Temporal worker for Dream Shorts, the Dream GPU controls' workflows and the autopilot's post workflow:

    uv run python -m studio.worker

It listens on the task queue projectv-control-py (the Python workflow types, suffix "Py"). It starts unless STUDIO_TEMPORAL_WORKER=false, and it never
rents or pays for anything by itself: it only runs workflows someone started on its queue. Workflows started by the old Java worker (queue
projectv-control, removed 24 Nov 2026) are not served by it: see docs/java-removal.md."""

from __future__ import annotations

import asyncio
import logging
import sys

from studio.api.video_wiring import build_video_context
from studio.api.wiring import build_dream_context
from studio.config import Settings
from studio.db import ThreadLocalPgDb
from studio.workflow.autopilot_workflow import AutopilotActivities
from studio.workflow.dream_activities import DreamActivities, DreamProducerActivities
from studio.workflow.runtime import TemporalGateway, build_all_worker, build_dream_worker

log = logging.getLogger("studio.worker")


def enabled(env: dict[str, str]) -> bool:
    return env.get("STUDIO_TEMPORAL_WORKER", "true").lower() == "true"


def build(client, ctx, vctx=None):
    """The worker for a connected client and built contexts (separate so tests can build it on a test server). With a video context it is ONE
    worker for every Python workflow type on the queue (video, Dream, post); without, the Dream and post workflows only."""
    dream, dream_producer, autopilot = DreamActivities(ctx.dreams), DreamProducerActivities(ctx.dream_producer), AutopilotActivities(ctx.autopilot)
    if vctx is None:
        return build_dream_worker(client, dream, dream_producer, autopilot)
    return build_all_worker(client, vctx.control, vctx.producer_acts, dream, dream_producer, autopilot)


async def run(settings: Settings) -> None:
    from temporalio.client import Client

    client = await Client.connect(settings.temporal_address)
    db = ThreadLocalPgDb(settings.dsn)
    ctx = build_dream_context(db, settings, gateway=TemporalGateway(client, asyncio.get_running_loop()))
    vctx = build_video_context(db, settings, dream_ctx=ctx)
    log.info("Python worker (video, Dream, post) listening on projectv-control-py at %s", settings.temporal_address)
    await build(client, ctx, vctx).run()


def main(env: dict[str, str] | None = None) -> int:
    import os

    env = dict(os.environ) if env is None else env
    if not enabled(env):
        print("Not starting: STUDIO_TEMPORAL_WORKER=false.")
        return 2
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(Settings.from_env()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
