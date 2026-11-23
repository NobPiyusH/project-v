"""Safety limits and the provider-agnostic pod API (CLAUDE.md rules 7 and 8). Ports of GpuPodLimits, PodApi and RunPodApi.

Prices are never config: a pod's hourly rate is read live from the provider's API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import quote

import httpx


@dataclass(frozen=True)
class GpuPodLimits:
    """prefix: only pods whose name starts with this are ever touched (the owner's other pods are left alone);
    max_runtime_minutes: hard lifetime cap from creation; idle_minutes: a leased pod whose heartbeat is older is idle and
    gets terminated; lease_grace_minutes: a new pod may run this long before its lease must exist; max_pods and
    max_hourly_usd: the start-side guard refuses more; dry_run: log what would be terminated, terminate nothing."""

    prefix: str
    max_runtime_minutes: int
    idle_minutes: int
    lease_grace_minutes: int
    max_pods: int
    max_hourly_usd: float
    dry_run: bool

    def __post_init__(self) -> None:
        if not self.prefix or not self.prefix.strip():
            raise ValueError("projectv.gpu.pod-prefix must not be empty (it would match every pod)")
        if (self.max_runtime_minutes <= 0 or self.idle_minutes <= 0 or self.lease_grace_minutes < 0
                or self.max_pods < 0 or self.max_hourly_usd < 0):
            raise ValueError("projectv.gpu.* limits must be positive")

    @staticmethod
    def from_settings(s: Any) -> GpuPodLimits:
        return GpuPodLimits(s.gpu_pod_prefix, s.gpu_max_runtime_minutes, s.gpu_idle_minutes, s.gpu_lease_grace_minutes,
                            s.gpu_max_pods, s.gpu_max_hourly_usd, s.gpu_watchdog_dry_run)

    def to_json(self) -> dict[str, Any]:
        return {"prefix": self.prefix, "maxRuntimeMinutes": self.max_runtime_minutes, "idleMinutes": self.idle_minutes,
                "leaseGraceMinutes": self.lease_grace_minutes, "maxPods": self.max_pods,
                "maxHourlyUsd": self.max_hourly_usd, "dryRun": self.dry_run}


@dataclass
class Pod:
    """cost_per_hr_usd: the provider's live hourly rate; created_at: lifetime is measured from here, so a restart cannot reset it."""

    id: str
    name: str
    status: str
    cost_per_hr_usd: float
    created_at: datetime | None
    started_at: datetime | None


class PodApi(Protocol):
    def provider(self) -> str: ...

    def configured(self) -> bool:
        """True when an API key is configured; without one the watchdog has nothing to do."""
        ...

    def list_pods(self) -> list[Pod]: ...

    def terminate(self, pod_id: str) -> None:
        """Permanently removes the pod (terminate, not stop: a stopped pod still bills its disk)."""
        ...


def _instant(o: Any) -> datetime | None:
    if not isinstance(o, str) or not o.strip():
        return None
    return datetime.fromisoformat(o)


class RunPodApi:
    """RunPod REST v2: GET /v2/pods (cursor-paginated; pods[] with id, name, status, cost USD/hour, createdAt, startedAt) and
    DELETE /v2/pods/{id} (terminate, 204). The key is sent as a bearer token and never logged."""

    def __init__(self, base_url: str, api_key: str, client: httpx.Client | None = None):
        self._base = base_url.rstrip("/")
        self._key = (api_key or "").strip()
        self._http = client or httpx.Client(timeout=httpx.Timeout(30, connect=10))

    def provider(self) -> str:
        return "runpod"

    def configured(self) -> bool:
        return bool(self._key)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}

    def list_pods(self) -> list[Pod]:
        out: list[Pod] = []
        cursor: str | None = None
        for _ in range(50):  # 50 pages x 1000 pods: a hard stop against a looping cursor
            url = f"{self._base}/pods?limit=1000" + (f"&cursor={quote(cursor, safe='')}" if cursor else "")
            resp = self._http.get(url, headers=self._headers())
            resp.raise_for_status()
            r = resp.json()
            if not isinstance(r, dict) or not isinstance(r.get("pods"), list):
                raise RuntimeError("RunPod list pods returned no pods array")
            out += [self._pod(p) for p in r["pods"] if isinstance(p, dict)]
            pg = r.get("pagination") if isinstance(r.get("pagination"), dict) else {}
            if pg.get("hasNextPage") is not True or not isinstance(pg.get("nextCursor"), str):
                return out
            cursor = pg["nextCursor"]
        raise RuntimeError("RunPod list pods: too many pages")

    def terminate(self, pod_id: str) -> None:
        self._http.delete(f"{self._base}/pods/{quote(pod_id, safe='')}", headers=self._headers()).raise_for_status()

    @staticmethod
    def _pod(p: dict[str, Any]) -> Pod:
        def s(k: str) -> str:
            return "" if p.get(k) is None else str(p[k])

        cost = p.get("cost")
        return Pod(s("id"), s("name"), s("status"), float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else 0.0,
                   _instant(p.get("createdAt")), _instant(p.get("startedAt")))
