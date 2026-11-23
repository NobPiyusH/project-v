"""One GPU rental provider (docs/plans/m3-gpu-router.md). Port of GpuProvider and StubProvider.

Extends the PodApi (so the same adapter also feeds the PodWatchdog). quote() is read-only; create() is the only call that spends.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from studio.gpu import self_stop
from studio.gpu.pods import Pod


@dataclass
class GpuSpec:
    """What the job needs. gpu_preference is tried in order, the cheapest one in stock wins."""

    gpu_preference: list[str]
    min_vram_gb: int
    min_ram_gb: int
    disk_gb: int


@dataclass
class Quote:
    """Live answer to 'can I get a GPU now, and at what hourly price'. Prices are never config (rule 7)."""

    provider: str
    available: bool
    stock: str
    gpu: str | None
    hourly_usd: float
    detail: str | None = None

    @staticmethod
    def unavailable(provider: str, detail: str) -> Quote:
        return Quote(provider, False, "NONE", None, 0.0, detail)


@dataclass
class CreateRequest:
    """name must start with the studio pod prefix (the watchdog only touches such pods); boot_argv is already wrapped with
    the in-pod guard; quote is the quote this create is based on (GPU and price to use)."""

    name: str
    image: str
    disk_gb: int
    boot_argv: list[str]
    env: dict[str, str]
    port: int
    volume_id: str | None
    quote: Quote | None


@dataclass(frozen=True)
class Instance:
    provider: str
    id: str
    name: str
    hourly_usd: float
    gpu: str | None
    endpoint: str | None


class State(Enum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    GONE = "GONE"


@dataclass
class InstanceStatus:
    state: State
    endpoint: str | None
    detail: str | None


class PlanUnavailableError(RuntimeError):
    """Raised by create() when the provider refuses this plan because it is out of stock right now and nothing was created.
    The only failure the canary may answer by trying the next plan; credit, auth and validation errors never are."""


@dataclass
class Preflight:
    """ok only when every check passed; checks says what was looked at."""

    ok: bool
    checks: list[str] = field(default_factory=list)


class GpuProvider(ABC):
    # ---- the PodApi part
    @abstractmethod
    def provider(self) -> str: ...

    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    def list_pods(self) -> list[Pod]: ...

    @abstractmethod
    def terminate(self, pod_id: str) -> None: ...

    # ---- the broker part
    @abstractmethod
    def enabled_by_default(self) -> bool: ...

    @abstractmethod
    def quote(self, spec: GpuSpec) -> Quote:
        """The cheapest option in stock (or an unavailable quote saying why not)."""

    @abstractmethod
    def create(self, req: CreateRequest) -> Instance: ...

    @abstractmethod
    def status(self, instance_id: str) -> InstanceStatus: ...

    def quotes(self, spec: GpuSpec) -> list[Quote]:
        """Every option in stock, one per GPU (and cloud) the provider offers: what the owner can pick from in Manual mode."""
        q = self.quote(spec)
        return [q] if q.available else []

    def endpoint_for(self, instance_id: str, port: int) -> str | None:
        """Where the worker on this pod listens (None when the provider cannot say); used to rebuild a rental after a restart."""
        return None

    def preflight(self, spec: GpuSpec, probe: CreateRequest) -> Preflight:
        """Free check, never rents anything: would this create request be accepted? The default only checks the key is set and
        that a quote exists."""
        if not self.configured():
            return Preflight(False, ["not configured (no key in .env)"])
        q = self.quote(spec)
        if q.available:
            return Preflight(True, ["configured", f"live quote: {q.gpu} ${q.hourly_usd}/h"])
        return Preflight(False, ["configured", f"no quote: {q.detail}"])

    def guard(self, job_argv: list[str], max_runtime_minutes: int, idle_minutes: int, created_at: datetime) -> list[str]:
        """The pod's command wrapped with this provider's in-pod guard. Default: the pod deletes itself with its own RunPod key;
        providers whose pods must hold no account key (Vast.ai) override it."""
        return self_stop.wrap(job_argv, max_runtime_minutes, idle_minutes, created_at)


class StubProvider(GpuProvider):
    """A provider that is planned but not built: never configured, so the broker skips it with a clear reason."""

    def __init__(self, provider_id: str, todo: str):
        self._id, self._todo = provider_id, todo

    def provider(self) -> str:
        return self._id

    def configured(self) -> bool:
        return False

    def enabled_by_default(self) -> bool:
        return False

    def list_pods(self) -> list[Pod]:
        return []

    def terminate(self, pod_id: str) -> None:
        raise NotImplementedError(f"{self._id} adapter not built yet: {self._todo}")

    def quote(self, spec: GpuSpec) -> Quote:
        return Quote.unavailable(self._id, f"adapter not built yet (TODO: {self._todo})")

    def create(self, req: CreateRequest) -> Instance:
        raise NotImplementedError(f"{self._id} adapter not built yet: {self._todo}")

    def status(self, instance_id: str) -> InstanceStatus:
        raise NotImplementedError(f"{self._id} adapter not built yet: {self._todo}")
