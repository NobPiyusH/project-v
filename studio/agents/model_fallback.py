"""Ordered model fallback for LLM calls. Port of ModelFallback.

Google retires and overloads models without notice, so every call has a list: if a model is gone, unknown, overloaded
or hangs, the next one is tried. Billing, quota and content errors are not model problems and fail immediately (trying
another model would only spend more).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")
log = logging.getLogger(__name__)

_MODEL_PROBLEM_WORDS = (
    "no longer available", "not found", "404", "not supported", "503", "unavailable", "overloaded", "504", "deadline",
)


@dataclass
class Attempt(Generic[T]):
    model: str
    value: T


def models(primary: str | None, fallbacks: str | None) -> list[str]:
    out: list[str] = []
    for spec in (primary, fallbacks):
        if spec is None:
            continue
        for m in spec.split(","):
            t = m.strip()
            if t and t not in out:
                out.append(t)
    return out


def _call_with_timeout(fn: Callable[[], T], seconds: float) -> T:
    """Runs fn on a daemon thread; raises TimeoutError when it does not finish (the thread is left to end on its own)."""
    box: dict[str, object] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - handed back to the caller's thread
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"no answer within {seconds}s")
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box["value"]  # type: ignore[return-value]


def is_model_problem(t: BaseException | None) -> bool:
    seen = set()
    while t is not None and id(t) not in seen:
        seen.add(id(t))
        msg = str(t).lower()
        if any(w in msg for w in _MODEL_PROBLEM_WORDS):
            return True
        t = t.__cause__ or t.__context__
    return False


def run(
    model_list: list[str],
    timeout_for: Callable[[str], float] | float,
    what: str,
    call: Callable[[str], T],
) -> Attempt[T]:
    """Tries each model in turn. A timeout or a model problem moves on; anything else (billing, quota, content) raises at once.

    The last model's failure is raised as it is (TimeoutError included).
    """
    if not model_list:
        raise RuntimeError(f"{what}: no model configured")
    for i, m in enumerate(model_list):
        last = i == len(model_list) - 1
        seconds = timeout_for(m) if callable(timeout_for) else timeout_for
        try:
            return Attempt(m, _call_with_timeout(lambda m=m: call(m), seconds))
        except TimeoutError:
            if last:
                raise
            log.warning("%s: %s did not answer within %ss, falling back to %s", what, m, seconds, model_list[i + 1])
        except Exception as e:
            if last or not is_model_problem(e):
                raise
            first_line = (str(e).splitlines() or [""])[0]
            log.warning("%s: %s failed (%s), falling back to %s", what, m, first_line, model_list[i + 1])
    raise RuntimeError("unreachable")
