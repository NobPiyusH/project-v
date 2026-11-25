"""Retry with exponential backoff + jitter, and a per-provider circuit breaker, around provider calls.

Rules (docs in the task, CLAUDE.md 2b): retry only TRANSIENT errors (timeouts, connection resets, HTTP 429/5xx), with capped
attempts and a capped total time; never retry a non-idempotent paid call (it may already have billed) unless the caller
marks it idempotent (providerCaps.<id>.idempotent in config/providers.yaml). After N consecutive failures the breaker opens
for a cool-down and calls fail fast with CircuitOpenError, a BudgetExceededError subclass so Temporal does not retry it and the
stage waits for the owner (or the router uses its fallback). After the cool-down ONE trial call is let through (half-open).

Clock, sleep and random are injected so tests need no real time. Log lines name the provider, attempt and error CLASS only:
never keys, URLs with keys, prompts or payloads.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

try:  # the studio's non-retryable "hold the stage" error; a bare worker process without the studio package falls back to RuntimeError
    from studio.ledger.budget_guard import BudgetExceededError as _Hold
except ImportError:  # pragma: no cover
    _Hold = RuntimeError

log = logging.getLogger("projectv.provider.resilience")
T = TypeVar("T")

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"
TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}


class CircuitOpenError(_Hold):
    """The provider's breaker is open: not called. The stage waits for the owner (or the router tries its fallback)."""


def status_of(e: BaseException) -> int | None:
    for attr in ("status_code", "code", "status"):
        v = getattr(e, attr, None)
        if isinstance(v, int) and not isinstance(v, bool):
            return v
    resp = getattr(e, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def is_transient(e: BaseException) -> bool:
    """Timeouts, connection problems, 408/425/429/5xx. Everything else (400, 401, 403, 404, bad input, bugs) is permanent."""
    if isinstance(e, (TimeoutError, ConnectionError)):
        return True
    s = status_of(e)
    if s is not None:
        return s in TRANSIENT_STATUS
    name = type(e).__name__.lower()
    return any(w in name for w in ("timeout", "connecterror", "connectionerror", "remoteprotocol", "readerror", "urlerror"))


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4            # total tries including the first
    base_delay: float = 1.0
    max_delay: float = 20.0
    max_total_sec: float = 60.0      # backoff sleeps never push the call past this
    jitter: float = 0.5              # delay is multiplied by uniform(1 - jitter, 1)

    def delay(self, attempt: int, rnd: Callable[[], float]) -> float:
        raw = min(self.max_delay, self.base_delay * 2 ** (attempt - 1))
        return raw * (1 - self.jitter * rnd())


@dataclass(frozen=True)
class BreakerPolicy:
    failure_threshold: int = 5       # consecutive failures that open it
    cooldown_sec: float = 300.0


class CircuitBreaker:
    def __init__(self, provider: str, policy: BreakerPolicy = BreakerPolicy(), clock: Callable[[], float] = time.monotonic):
        self.provider, self.policy, self._clock = provider, policy, clock
        self._lock = threading.Lock()
        self._state, self._fails, self._opened_at, self._trial = CLOSED, 0, 0.0, False

    @property
    def state(self) -> str:
        with self._lock:
            return self._peek()

    def _peek(self) -> str:
        if self._state == OPEN and self._clock() - self._opened_at >= self.policy.cooldown_sec:
            return HALF_OPEN
        return self._state

    def before_call(self) -> None:
        with self._lock:
            st = self._peek()
            if st == OPEN:
                left = self.policy.cooldown_sec - (self._clock() - self._opened_at)
                raise CircuitOpenError(f"{self.provider} is paused after {self._fails} failures in a row; trying again in about "
                                       f"{max(0, round(left))} s. Retry the stage later, use the fallback or switch it to Manual.")
            if st == HALF_OPEN:
                if self._trial:
                    raise CircuitOpenError(f"{self.provider} is being tested after a pause; wait for that call to finish.")
                self._state, self._trial = HALF_OPEN, True
                log.info("breaker %s half-open: one trial call", self.provider)

    def success(self) -> None:
        with self._lock:
            if self._state != CLOSED:
                log.info("breaker %s closed again", self.provider)
            self._state, self._fails, self._trial = CLOSED, 0, False

    def failure(self) -> bool:
        """Count one failed call (after its retries); True when this failure opened (or re-opened) the breaker."""
        with self._lock:
            self._fails += 1
            self._trial = False
            if self._state == HALF_OPEN or self._fails >= self.policy.failure_threshold:
                self._state, self._opened_at = OPEN, self._clock()
                log.warning("breaker %s OPEN for %.0f s after %d consecutive failures", self.provider, self.policy.cooldown_sec, self._fails)
                return True
            return False

    def release(self) -> None:
        """A call ended in a non-provider error (bad input): do not count it, but free a half-open trial slot."""
        with self._lock:
            self._trial = False
            if self._state == HALF_OPEN:
                self._state = OPEN  # not proven healthy; the next trial is allowed at once because the cool-down has elapsed


class ProviderCaller:
    """call(provider, fn, idempotent=...): breaker check, then fn with retries on transient errors."""

    def __init__(self, retry: RetryPolicy = RetryPolicy(), breaker: BreakerPolicy = BreakerPolicy(), *,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 rnd: Callable[[], float] = random.random,
                 on_open: Callable[[str, str], None] | None = None):
        self.retry, self.breaker_policy, self.clock, self.sleep, self.rnd, self.on_open = retry, breaker, clock, sleep, rnd, on_open
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def breaker(self, provider: str) -> CircuitBreaker:
        with self._lock:
            if provider not in self._breakers:
                self._breakers[provider] = CircuitBreaker(provider, self.breaker_policy, self.clock)
            return self._breakers[provider]

    def call(self, provider: str, fn: Callable[[], T], *, idempotent: bool = False) -> T:
        br = self.breaker(provider)
        br.before_call()
        started = self.clock()
        attempt = 0
        while True:
            attempt += 1
            try:
                result = fn()
            except BaseException as e:  # noqa: BLE001 - classified; re-raised
                if not isinstance(e, Exception) or not is_transient(e):
                    br.release()  # permanent (bad input, auth, a bug): not the provider being down
                    raise
                label = f"{type(e).__name__}/{status_of(e)}"
                if idempotent and attempt < self.retry.max_attempts:
                    wait = self.retry.delay(attempt, self.rnd)
                    if self.clock() - started + wait <= self.retry.max_total_sec:
                        log.warning("provider %s attempt %d failed (%s); retrying in %.1f s", provider, attempt, label, wait)
                        self.sleep(wait)
                        continue
                if not idempotent:
                    log.warning("provider %s failed (%s); not retried (non-idempotent paid call)", provider, label)
                else:
                    log.warning("provider %s failed (%s) after %d attempt(s); giving up", provider, label, attempt)
                if br.failure() and self.on_open is not None:
                    self.on_open(provider, label)
                raise
            br.success()
            return result
