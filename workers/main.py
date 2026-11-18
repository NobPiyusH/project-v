"""Local Python worker. Run from the repo root:  uv run python -m workers.main"""

import asyncio
import concurrent.futures
import logging
import os

from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.worker import Worker

from workers.activities.animatic import render_animatic
from workers.activities.assemble import assemble_video
from workers.activities.clips import generate_clips
from workers.activities.finish import smooth_clips, upscale_clips
from workers.activities.footage import fetch_footage, search_footage
from workers.activities.inspect import inspect_media
from workers.activities.keyframes import generate_keyframes
from workers.activities.media import tool_versions
from workers.activities.music import generate_music
from workers.activities.research import research_topic
from workers.activities.review import review_images
from workers.activities.producer import producer_review
from workers.activities.sources import check_sources
from workers.activities.voice import synthesize_narration
from workers.dream.finish import dream_finish
from workers.dream.music_bed import dream_music_bed
from workers import modal_billing
from workers.common import ensure_bucket

# Must match com.projectv.workflow.TaskQueues.MEDIA
MEDIA_TASK_QUEUE = "projectv-media"


async def main() -> None:
    load_dotenv()  # reads .env in the repo root; values are never logged
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ensure_bucket()
    modal_billing.start()  # Modal credit + spend for the dashboard, every 10 min
    target = os.getenv("TEMPORAL_TARGET", "localhost:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    client = await Client.connect(target, namespace=namespace)

    # Media activities are blocking (network, subprocess, CPU), so they run in a thread pool.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        worker = Worker(
            client,
            task_queue=MEDIA_TASK_QUEUE,
            activities=[tool_versions, research_topic, synthesize_narration, generate_music,
                        generate_keyframes, render_animatic, inspect_media, review_images,
                        generate_clips, search_footage, fetch_footage, assemble_video,
                        smooth_clips, upscale_clips, check_sources, producer_review, dream_finish, dream_music_bed],
            activity_executor=pool,
            max_concurrent_activities=8,
        )
        logging.info("Media worker listening on task queue %s (%s)", MEDIA_TASK_QUEUE, target)
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
