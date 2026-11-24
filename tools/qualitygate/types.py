"""Typed results shared by every tier."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class Status(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"  # check not applicable (for example loudness on a silent clip)


_ORDER = {Status.SKIP: 0, Status.PASS: 1, Status.WARN: 2, Status.FAIL: 3}


def worst(statuses: list[Status]) -> Status:
    return max(statuses, key=_ORDER.__getitem__, default=Status.PASS)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    value: float | None = None  # the measured number (unit in `unit`)
    unit: str = ""
    where: tuple[tuple[float, float], ...] = ()  # (start_s, end_s) spans the finding applies to
    message: str = ""
    shot: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["where"] = [list(w) for w in self.where]
        return d


@dataclass(frozen=True)
class Shot:
    id: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class GateReport:
    video: str
    duration: float
    checks: list[CheckResult] = field(default_factory=list)
    judge: list[dict] = field(default_factory=list)

    @property
    def status(self) -> Status:
        return worst([c.status for c in self.checks])

    def to_dict(self) -> dict:
        return {"video": self.video, "duration": self.duration, "status": self.status.value,
                "checks": [c.to_dict() for c in self.checks], "judge": self.judge}  # fmt: skip
