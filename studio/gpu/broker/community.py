"""Opt-in RunPod Community-cloud mode with an abort-and-retry rule (POD_CLOUD_PREFERENCE=community_then_secure).

Measured 24-25 Nov 2026: a Community 4090 costs about a third of Secure, but some hosts are bad (CUDA False after boot, or the image
pull hangs). The rule: rent Community; if no boot log line appears within 9 minutes, or the CUDA check says False, or the image
pull makes no progress, terminate that pod at once (a failed attempt costs a few cents) and try once more on Community, then fall
back to Secure. The decision is a pure function (decide); the broker does the I/O. Prices are never set here (CLAUDE.md rule 7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

SECURE = "secure"
COMMUNITY_THEN_SECURE = "community_then_secure"
BOOT_MARKER = "[pod-worker"  # every line the pod worker prints starts like this (workers/pod/server.py log())
_CUDA = re.compile(r"cuda_available=(True|False)")


class Verdict(Enum):
    KEEP = "keep"
    ABORT = "abort"
    WAIT = "wait"


@dataclass(frozen=True)
class AttemptLimits:
    """boot_timeout_s: no boot line (or no CUDA answer) by then means abort. pull_stall_s: once pull output has appeared, this long
    with no new output (and no boot line yet) means the pull hangs. max_community_attempts: Community tries before Secure."""

    boot_timeout_s: float = 540.0
    pull_stall_s: float = 360.0
    poll_s: float = 15.0
    max_community_attempts: int = 2

    def __post_init__(self) -> None:
        if self.boot_timeout_s <= 0 or self.pull_stall_s <= 0 or self.poll_s < 0 or self.max_community_attempts < 1:
            raise ValueError("attempt limits must be positive")


@dataclass(frozen=True)
class AttemptState:
    """What the last look saw: how much log text, and when (seconds since the rent) it last grew."""

    last_log_len: int = 0
    last_progress_s: float = 0.0


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    reason: str
    state: AttemptState


def parse_cuda(boot_log: str) -> bool | None:
    """The last 'cuda_available=True|False' the pod worker printed, or None when it has not said yet."""
    found = _CUDA.findall(boot_log or "")
    return None if not found else found[-1] == "True"


def has_boot_line(boot_log: str) -> bool:
    return BOOT_MARKER in (boot_log or "")


def decide(state: AttemptState, elapsed_s: float, boot_log: str, cuda: bool | None, limits: AttemptLimits | None = None) -> Decision:
    """KEEP the pod, ABORT it (terminate now) or WAIT for the next look. cuda: True/False from the pod's check, None = not reported."""
    limits = limits or AttemptLimits()
    elapsed = max(0.0, elapsed_s)
    log_len = len(boot_log or "")
    st = AttemptState(log_len, elapsed) if log_len > state.last_log_len else state
    if cuda is False:
        return Decision(Verdict.ABORT, "the CUDA check reports False", st)
    booted = has_boot_line(boot_log)
    if booted and cuda is True:
        return Decision(Verdict.KEEP, "boot line seen and CUDA is True", st)
    if elapsed >= limits.boot_timeout_s:
        why = (f"CUDA was never reported {limits.boot_timeout_s:.0f} s after the rent" if booted
               else f"no boot log line within {limits.boot_timeout_s:.0f} s")
        return Decision(Verdict.ABORT, why, st)
    if not booted and st.last_log_len > 0 and elapsed - st.last_progress_s >= limits.pull_stall_s:
        return Decision(Verdict.ABORT, f"the image pull made no progress for {elapsed - st.last_progress_s:.0f} s", st)
    return Decision(Verdict.WAIT, "waiting for the CUDA check" if booted else "waiting for the boot line and the CUDA check", st)


def normalise_preference(value: str | None) -> str:
    """Anything but the exact opt-in value is today's behaviour."""
    return COMMUNITY_THEN_SECURE if (value or "").strip().lower() == COMMUNITY_THEN_SECURE else SECURE


def is_community(gpu_key: str | None) -> bool:
    """RunPod quotes carry '<gpu id>|COMMUNITY' or '<gpu id>|SECURE'."""
    return bool(gpu_key) and "|" in gpu_key and gpu_key.split("|")[-1].upper() == "COMMUNITY"


class BootProbe(Protocol):
    def observe(self, pod_id: str) -> str:
        """The pod's log text so far (empty when nothing yet). May raise; the broker then treats it as 'nothing new'."""
        ...


class PodLogsProbe:
    """Reads the pod's log with the dashboard's PodLogs reader (one bounded read per look)."""

    def __init__(self, pod_logs: object, tail: int = 400):
        self._logs, self._tail = pod_logs, tail

    def observe(self, pod_id: str) -> str:
        page = self._logs.read(pod_id, None, self._tail)  # type: ignore[attr-defined]
        if page.error and not page.lines:
            raise RuntimeError(page.error)
        return "\n".join(x.line for x in page.lines)
