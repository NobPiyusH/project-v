"""External pod reaper: a stand-alone kill-timer for RunPod pods that does NOT depend on the studio or its lease database.

Polls the RunPod REST API and terminates a pod that
  (a) ran past its max wall-clock (default 3 h; per pod via a name suffix such as `projectv-x-maxh=2`), or
  (b) is named `projectv-*`, has no live lease and is past the grace period, or
  (c) would push today's (UTC) estimated spend past the daily cap before the next run.
Dry-run by default (`--apply` terminates). Idempotent (a pod already gone is fine). One log line per decision, a small JSON
state file (accrued spend per UTC day + last run). Exit 0 = ok, 1 = a terminate failed, 2 = the API could not be reached
(so a scheduler alerts). Never prints the key. stdlib only, so it runs on any free non-GPU host (see docs/pod-reaper.md).
Spend is an estimate (hourly rate x hours run), like the ledger; it is not a provider bill.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

BASE_URL = "https://api.runpod.io/v2"
DEFAULT_STATE = r"E:\project-data\outputs\pod-reaper\state.json"
DEFAULT_LOG = r"E:\project-data\outputs\pod-reaper\reaper.log"
MAXH = re.compile(r"maxh[=_:-]?(\d+(?:\.\d+)?)", re.IGNORECASE)


@dataclass(frozen=True)
class Pod:
    id: str
    name: str
    status: str  # RUNNING / EXITED / ...
    cost_per_hr: float
    created_at: datetime | None
    started_at: datetime | None

    def clock_start(self) -> datetime | None:
        return self.started_at or self.created_at


@dataclass(frozen=True)
class Config:
    max_hours: float = 3.0
    lease_grace_min: float = 15.0
    lease_stale_min: float = 30.0  # looser than the studio watchdog (15): the reaper is the backstop, not a rival
    daily_cap_usd: float = 3.50
    interval_min: float = 10.0  # the scheduler period: a pod that cannot fit another interval under the cap is stopped now
    prefix: str = "projectv-"


@dataclass(frozen=True)
class Decision:
    pod_id: str
    name: str
    action: str  # "terminate" | "keep"
    rule: str  # "max_wallclock" | "no_lease" | "daily_cap" | "ok" | "not_running"
    reason: str


@dataclass
class Plan:
    decisions: list[Decision] = field(default_factory=list)
    spend: dict[str, float] = field(default_factory=dict)  # today's accrued estimate per pod id (the state file carries it)
    today_total: float = 0.0


def pod_max_hours(name: str, default: float) -> float:
    m = MAXH.search(name or "")
    return float(m.group(1)) if m and float(m.group(1)) > 0 else default


def day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def decide(
    pods: list[Pod],
    now: datetime,
    leases: dict[str, datetime | None] | None,
    cfg: Config,
    spent_today: dict[str, float] | None = None,
) -> Plan:
    """Pure. `leases` = pod id -> last heartbeat of every UNRELEASED lease (None = lease database unreachable: rule (b) is
    skipped, never guessed). `spent_today` = estimate already carried in the state file (covers pods that have since ended)."""
    plan = Plan()
    spend = dict(spent_today or {})
    d0 = day_start(now)
    for p in pods:
        t0 = p.clock_start()
        if p.status.upper() == "RUNNING" and t0 is not None:
            hrs_today = max(0.0, (now - max(t0, d0)).total_seconds() / 3600)
            spend[p.id] = max(spend.get(p.id, 0.0), round(p.cost_per_hr * hrs_today, 4))
    plan.spend = spend
    plan.today_total = round(sum(spend.values()), 4)

    keepers: list[tuple[Pod, Decision]] = []
    for p in pods:
        if p.status.upper() != "RUNNING":
            plan.decisions.append(Decision(p.id, p.name, "keep", "not_running", f"status {p.status or '?'} (no GPU billing)"))
            continue
        t0 = p.clock_start()
        if t0 is None:
            plan.decisions.append(Decision(p.id, p.name, "keep", "ok", "no start time reported, cannot judge age"))
            continue
        age_h = (now - t0).total_seconds() / 3600
        limit = pod_max_hours(p.name, cfg.max_hours)
        if age_h > limit:
            plan.decisions.append(Decision(p.id, p.name, "terminate", "max_wallclock", f"ran {age_h:.2f} h, limit {limit:g} h"))
            continue
        if p.name.startswith(cfg.prefix) and leases is not None and age_h * 60 >= cfg.lease_grace_min:
            if p.id not in leases:
                plan.decisions.append(
                    Decision(
                        p.id,
                        p.name,
                        "terminate",
                        "no_lease",
                        f"no live lease, {age_h * 60:.0f} min old, grace {cfg.lease_grace_min:g}",
                    )
                )
                continue
            beat = leases[p.id]
            if beat is not None and (now - beat) > timedelta(minutes=cfg.lease_stale_min):
                mins = (now - beat).total_seconds() / 60
                plan.decisions.append(
                    Decision(
                        p.id,
                        p.name,
                        "terminate",
                        "no_lease",
                        f"lease heartbeat {mins:.0f} min old, stale after {cfg.lease_stale_min:g}",
                    )
                )
                continue
        keepers.append((p, Decision(p.id, p.name, "keep", "ok", f"{age_h:.2f} h of {limit:g} h")))

    # Rule (c): spend already accrued is sunk; each pod we keep must fit one more interval under the cap. Priciest first.
    projected = plan.today_total
    for p, ok in sorted(keepers, key=lambda x: -x[0].cost_per_hr):
        nxt = p.cost_per_hr * cfg.interval_min / 60
        if projected + nxt > cfg.daily_cap_usd:
            plan.decisions.append(
                Decision(
                    p.id,
                    p.name,
                    "terminate",
                    "daily_cap",
                    f"today ${plan.today_total:.2f} + next {cfg.interval_min:g} min would pass cap ${cfg.daily_cap_usd:.2f}",
                )
            )
        else:
            projected += nxt
            plan.decisions.append(ok)
    return plan


# --- I/O ----------------------------------------------------------------------------------------------------------------
def _instant(o) -> datetime | None:
    if not isinstance(o, str) or not o.strip():
        return None
    s = o.strip().replace(" UTC", "")  # RunPod sends "2026-11-25 10:20:30.123 +0000 UTC" in places, ISO in others
    for fmt in ("%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_pod(p: dict) -> Pod:
    cost = p.get("costPerHr", p.get("cost"))
    ok = isinstance(cost, (int, float)) and not isinstance(cost, bool)
    return Pod(
        str(p.get("id", "")),
        str(p.get("name") or ""),
        str(p.get("desiredStatus") or p.get("status") or ""),
        float(cost) if ok else 0.0,
        _instant(p.get("createdAt")),
        _instant(p.get("lastStartedAt") or p.get("startedAt")),
    )


def find_env_key(env_file: str | None = None) -> str:
    key = os.environ.get("RUNPOD_API_KEY", "").strip()
    if key:
        return key
    cands = [Path(env_file)] if env_file else [d / ".env" for d in [Path.cwd(), *Path(__file__).resolve().parents]]
    for f in cands:
        if f.is_file():
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"^\s*RUNPOD_API_KEY\s*=\s*(.*?)\s*$", line)
                if m and m.group(1).strip("\"'"):
                    return m.group(1).strip("\"'")
    raise RuntimeError("RUNPOD_API_KEY not found in the environment or a .env file")


class RunPod:
    def __init__(self, key: str, base: str = BASE_URL, timeout: float = 30):
        self._key, self._base, self._timeout = key, base.rstrip("/"), timeout

    def _call(self, method: str, path: str):
        req = urllib.request.Request(
            self._base + path, method=method, headers={"Authorization": f"Bearer {self._key}", "User-Agent": "pod-reaper"}
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as r:  # noqa: S310 (fixed https base)
            body = r.read().decode() or "null"
        return json.loads(body)

    def list_pods(self) -> list[Pod]:
        data = self._call("GET", "/pods")
        if isinstance(data, dict):
            data = data.get("pods")
        if not isinstance(data, list):
            raise RuntimeError("RunPod list pods: unexpected response shape")
        return [parse_pod(p) for p in data if isinstance(p, dict)]

    def terminate(self, pod_id: str) -> None:
        try:
            self._call("DELETE", f"/pods/{pod_id}")
        except urllib.error.HTTPError as e:
            if e.code != 404:  # already gone = done (idempotent)
                raise


def fetch_leases() -> dict[str, datetime | None] | None:
    """Live (unreleased) leases from the studio database via tools/gpu_lease; None when it cannot be read."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gpu_lease"))
        import lease  # noqa: PLC0415

        out = lease.psql(
            "SELECT pod_id || '|' || COALESCE(extract(epoch from heartbeat_at)::text, '') FROM gpu_pod_lease WHERE released_at IS NULL"
        )
    except Exception:  # noqa: BLE001
        return None
    res: dict[str, datetime | None] = {}
    for line in out.splitlines():
        pid, _, ts = line.partition("|")
        if pid.strip():
            res[pid.strip()] = datetime.fromtimestamp(float(ts), UTC) if ts.strip() else None
    return res


def load_state(path: Path, today: str) -> dict:
    try:
        s = json.loads(path.read_text(encoding="utf-8"))
        if s.get("day") == today:
            return s
    except (OSError, ValueError):
        pass
    return {"day": today, "spend": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def run(
    api, now: datetime, cfg: Config, apply: bool, state_path: Path, log_path: Path | None, leases_fn=fetch_leases, out=print
) -> int:
    def say(msg: str) -> None:
        line = f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}"
        out(line)
        if log_path:
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    mode = "APPLY" if apply else "DRY-RUN"
    try:
        pods = api.list_pods()
    except Exception as e:  # noqa: BLE001
        say(f"ERROR cannot reach the RunPod API: {type(e).__name__}")  # type only: a message could echo a URL
        return 2
    leases = leases_fn()
    if leases is None:
        say("WARN lease check off or database unavailable: rule no_lease skipped this run")
    state = load_state(state_path, day_start(now).strftime("%Y-%m-%d"))
    plan = decide(pods, now, leases, cfg, state.get("spend"))
    failed = 0
    say(f"{mode} {len(pods)} pod(s), today's estimated spend ${plan.today_total:.2f} of ${cfg.daily_cap_usd:.2f}")
    for d in plan.decisions:
        if d.action != "terminate":
            say(f"keep {d.pod_id} {d.name} [{d.rule}] {d.reason}")
        elif not apply:
            say(f"WOULD TERMINATE {d.pod_id} {d.name} [{d.rule}] {d.reason}")
        else:
            try:
                api.terminate(d.pod_id)
                say(f"TERMINATED {d.pod_id} {d.name} [{d.rule}] {d.reason}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                say(f"FAILED to terminate {d.pod_id} {d.name} [{d.rule}]: {type(e).__name__}")
    state.update(spend=plan.spend, last_run=now.isoformat(), last_mode=mode.lower(), last_pods=len(pods))
    try:
        save_state(state_path, state)
    except OSError as e:
        say(f"WARN could not write state: {type(e).__name__}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="really terminate (default: dry-run)")
    ap.add_argument("--max-hours", type=float, default=3.0)
    ap.add_argument("--grace-min", type=float, default=15.0)
    ap.add_argument("--daily-cap", type=float, default=3.50, help="USD per UTC day (estimate)")
    ap.add_argument("--interval-min", type=float, default=10.0, help="how often you schedule this")
    ap.add_argument("--state", default=os.environ.get("REAPER_STATE", DEFAULT_STATE))
    ap.add_argument("--log", default=os.environ.get("REAPER_LOG", DEFAULT_LOG))
    ap.add_argument("--no-lease-check", action="store_true", help="host without the studio database: skip rule (b)")
    ap.add_argument("--env-file")
    a = ap.parse_args(argv)
    cfg = Config(a.max_hours, a.grace_min, daily_cap_usd=a.daily_cap, interval_min=a.interval_min)
    try:
        api = RunPod(find_env_key(a.env_file))
    except RuntimeError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2
    leases_fn = (lambda: None) if a.no_lease_check else fetch_leases
    return run(api, datetime.now(UTC), cfg, a.apply, Path(a.state), Path(a.log), leases_fn)


if __name__ == "__main__":
    sys.exit(main())
