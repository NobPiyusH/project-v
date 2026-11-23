"""Check 7: cost estimate against the caps, shown before every paid step (queue item 7).

`preflight` takes what a step is about to spend and returns a plain sentence plus findings; the caller must not start
the paid step when any finding is FAIL. The gate's own vision calls use it before the first call.
"""
from __future__ import annotations

from . import FAIL, INFO, WARN, CheckResult, Finding


def preflight(step: str, estimate_usd: float, worst_usd: float, cfg: dict, spent_run_usd: float = 0.0, spent_day_usd: float = 0.0) -> tuple[str, list[Finding]]:
    caps = cfg["caps"]
    fx = caps["fx_inr_per_usd"]
    msg = (f"{step}: about ${estimate_usd:.3f} (Rs {estimate_usd * fx:.1f}), at most ${worst_usd:.3f}. "
           f"Run cap ${caps['per_run_usd']:.2f}, day cap ${caps['per_day_usd']:.2f}, spent today ${spent_day_usd:.3f}.")
    out: list[Finding] = []
    if spent_run_usd + worst_usd > caps["per_run_usd"]:
        out.append(Finding("cost", FAIL, "OVER_RUN_CAP", f"{step}: worst case ${worst_usd:.3f} would pass the run cap ${caps['per_run_usd']:.2f}.", step))
    if spent_day_usd + worst_usd > caps["per_day_usd"]:
        out.append(Finding("cost", FAIL, "OVER_DAY_CAP", f"{step}: worst case ${worst_usd:.3f} would pass the day cap ${caps['per_day_usd']:.2f}.", step))
    if not out and spent_run_usd + estimate_usd > 0.8 * caps["per_run_usd"]:
        out.append(Finding("cost", WARN, "NEAR_RUN_CAP", f"{step}: the estimate uses over 80% of the run cap.", step))
    if not out:
        out.append(Finding("cost", INFO, "COST_OK", msg, step))
    return msg, out


def result(findings: list[Finding], stats: dict) -> CheckResult:
    return CheckResult("cost", findings, stats=stats)
