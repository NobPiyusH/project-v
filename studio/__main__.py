"""The Python control plane, live:

    uv run python -m studio          # API on 127.0.0.1:8080 (PORT or STUDIO_PORT to change), migrations applied first
    uv run python -m studio.worker   # the Temporal worker (own process; queue projectv-control-py)

Nothing here starts a workflow or rents a pod: pods are only rented through GpuBroker.acquire() when a Short asks for one and DREAM_GPU_PROVIDER=broker
(the default `modal` rents nothing). scripts/start.ps1 and tools/ops/start_studio.ps1 start both processes."""

from __future__ import annotations

import sys


def main() -> int:
    import uvicorn

    from studio.config import Settings

    s = Settings.from_env()
    uvicorn.run("studio.api.app:create_app", factory=True, host="127.0.0.1", port=s.port, log_level="info")
    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()  # the studio read no .env before: every key and flag came out blank. Walks up from this file; values are never logged
    sys.exit(main())
