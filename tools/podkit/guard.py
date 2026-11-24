"""Safety guards for a rented pod: watchdog lease + independent kill-timer, FAIL-CLOSED.

On 24 Nov 2026 Docker was off, the lease helper died on its first psql call without anyone noticing, and the studio watchdog
(which needs the same database) was off too: only the kill-timer protected the pods. A pod must never run on that footing
silently: start_guards() confirms the lease row really exists and returns False if it does not; the caller then terminates the pod.
"""
from __future__ import annotations

import subprocess
import sys
import time

CREATE_NO_WINDOW = 0x08000000
DETACHED = 0x00000008
NEW_GROUP = 0x00000200


def psql(sql: str, timeout: int = 30) -> str:
    r = subprocess.run(["docker", "exec", "projectv-postgres-1", "psql", "-U", "projectv", "-d", "projectv", "-tA", "-c", sql],
                       capture_output=True, text=True, timeout=timeout,
                       creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    if r.returncode:
        raise RuntimeError("psql failed: " + r.stderr.strip()[:200])
    return r.stdout.strip()


def database_ready() -> bool:
    """True when the studio database (Docker) answers. Check BEFORE renting."""
    try:
        return psql("select 1", timeout=20) == "1"
    except Exception:  # noqa: BLE001 - any failure means "not ready"
        return False


def lease_open(pod_id: str, query=psql) -> bool:
    try:
        return query(f"select count(*) from gpu_pod_lease where pod_id = '{pod_id}' and released_at is null") not in ("", "0")
    except Exception:  # noqa: BLE001
        return False


def start_guards(pod_id: str, name: str, purpose: str, state_path: str, deadline_min: int, lease_py: str, killer_py: str,
                 provider: str = "runpod", popen=subprocess.Popen, query=psql, confirm_s: float = 90.0,
                 sleep=time.sleep, clock=time.time) -> bool:
    """Start the lease keeper and the kill-timer. Returns True only when the lease row is confirmed open."""
    popen([sys.executable, lease_py, "keep", provider, pod_id, name, purpose, "--state", state_path,
           "--max-minutes", str(deadline_min + 5)], creationflags=CREATE_NO_WINDOW | DETACHED if sys.platform == "win32" else 0,
          close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    popen([sys.executable, killer_py, pod_id, str(deadline_min * 60), state_path],
          creationflags=DETACHED | NEW_GROUP if sys.platform == "win32" else 0, close_fds=True,
          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = clock() + confirm_s
    while clock() < end:
        if lease_open(pod_id, query):
            return True
        sleep(5)
    return False
