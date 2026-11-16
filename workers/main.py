"""Local Python worker. Run from the repo root:  uv run python -m workers.main"""

import asyncio
import concurrent.futures
import logging
import os

from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.worker import Worker

from workers.activities.media import tool_versions

# Must match com.projectv.workflow.TaskQueues.MEDIA
MEDIA_TASK_QUEUE = "projectv-media"


async def main() -> None:
    load_dotenv()  # reads .env in the repo root; values are never logged
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    target = os.getenv("TEMPORAL_TARGET", "localhost:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    client = await Client.connect(target, namespace=namespace)

    # Media activities are blocking (subprocess / CPU), so they run in a thread pool.
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        worker = Worker(
            client,
            task_queue=MEDIA_TASK_QUEUE,
            activities=[tool_versions],
            activity_executor=pool,
        )
        logging.info("Media worker listening on task queue %s (%s)", MEDIA_TASK_QUEUE, target)
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
