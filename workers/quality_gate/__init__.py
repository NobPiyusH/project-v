"""Cinestack quality gate: automatic checks on a finished cut, one report, pass/fail per check.

Every check is a small pure function (data in, list of findings out) so the future Python control plane can import
them. Only `source.py` (database/S3) and `openai_client.py` (HTTP) touch the outside world.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

FAIL, WARN, INFO = "FAIL", "WARN", "INFO"


@dataclass
class Finding:
    check: str          # vision | diagram | audio | captions | timing | freeze | cost
    severity: str       # FAIL | WARN | INFO
    code: str
    message: str
    ref: str = ""       # "shot 7", "line 3", "00:01:12"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CheckResult:
    name: str
    findings: list[Finding] = field(default_factory=list)
    skipped: str = ""   # reason when the check could not run (never counted as a pass)
    stats: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.skipped:
            return "SKIPPED"
        return "FAIL" if any(f.severity == FAIL for f in self.findings) else "PASS"
