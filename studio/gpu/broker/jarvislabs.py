"""JarvisLabs adapter. Port of JarvislabsProvider and JarvislabsConfig.

JarvisLabs documents no REST API for instances, only the `jarvislabs` Python SDK, so every call goes through
workers/jarvislabs_helper.py (one JSON command per process; the key travels in the JL_API_KEY environment variable of that
process only). Same safety design as the E2E adapter (docs/jarvislabs-provider-notes.md):
- No secret goes to the instance. The startup script holds the worker env only (boot payload, port, root, per-rental worker
  token). Never the JarvisLabs key or any other key.
- Teardown is ours. terminate() destroys the instance (pause would keep billing storage). The in-instance guard only ends the
  worker (self_stop.wrap_exit). The watchdog lists instances by the studio name prefix.
NOT verified live (no key was used): the helper's output fields, GPU type names, how the instance endpoint is addressed, whether a
container template can run the worker, the startup-script size limit. The adapter stays proven=false until the canary passes.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from studio.agents.jfmt import fixed
from studio.gpu import self_stop
from studio.gpu.broker.provider import CreateRequest, GpuProvider, GpuSpec, Instance, InstanceStatus, Preflight, Quote, State
from studio.gpu.broker.runpod import LABEL_PREFIX, SAFE_ENV, abbreviate, shell_quote
from studio.gpu.pods import Pod

log = logging.getLogger(__name__)

Runner = Callable[[list[str], str | None], str]


@dataclass
class JarvisGpu:
    type: str
    vram_gb: int


@dataclass
class JarvislabsConfig:
    """Limits only: the hourly price always comes from the live GPU list (rule 7). service_port: the port JarvisLabs publishes
    through the instance endpoint; in_per_usd: the owner's account is INR, so the live list's prices are rupees per hour;
    min_balance: the preflight fails below this balance (account currency); script_max_bytes: unverified limit."""

    gpus: list[JarvisGpu] = field(default_factory=lambda: [JarvisGpu("A100", 40), JarvisGpu("A100-80GB", 80), JarvisGpu("RTX-PRO6000", 96)])
    template: str = "pytorch"
    service_port: int = 6006
    start_timeout_seconds: int = 900
    script_max_bytes: int = 60_000
    cache_seconds: int = 30
    inr_per_usd: float = 94.0
    min_balance: float = 100.0

    @staticmethod
    def from_yaml(root: dict[str, Any]) -> JarvislabsConfig:
        def d(x: Any) -> dict[str, Any]:
            return x if isinstance(x, dict) else {}

        v = d(d(d(root.get("pricing")).get("gpuBroker")).get("providers")).get("jarvislabs")
        v = d(v)

        def num(k: str, default: float) -> float:
            x = v.get(k)
            return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else default

        cfg = JarvislabsConfig()
        if isinstance(v.get("gpus"), list) and v["gpus"]:
            cfg.gpus = [JarvisGpu(str(g.get("type")), int(g.get("vramGb") or 0)) for g in v["gpus"] if isinstance(g, dict)]
        cfg.template = str(v["template"]) if v.get("template") is not None else "pytorch"
        cfg.service_port = int(num("servicePort", 6006))
        cfg.start_timeout_seconds = int(num("startTimeoutSeconds", 900))
        cfg.script_max_bytes = int(num("scriptMaxBytes", 60_000))
        cfg.cache_seconds = int(num("cacheSeconds", 30))
        cfg.inr_per_usd = max(1.0, num("inrPerUsd", 94))
        cfg.min_balance = num("minBalance", 100)
        return cfg


class _NotFoundError(RuntimeError):
    pass


def _num(o: Any) -> float:
    if isinstance(o, (int, float)) and not isinstance(o, bool):
        return float(o)
    if isinstance(o, str) and re.fullmatch(r"[0-9.]+", o):
        return float(o)
    return 0.0


def _str(o: Any) -> str:
    return "" if o is None else str(o)


def process_runner(api_key: str, command: str, helper: str, timeout_seconds: int) -> Runner:
    """Real runner: <command> <helper> <args...>, key in the child's environment only, killed after the timeout."""

    def run(args: list[str], stdin: str | None) -> str:
        script = Path(helper)
        if not script.is_file() and (Path("..") / helper).is_file():
            script = Path("..") / helper
        import os

        env = {**os.environ, "JL_API_KEY": (api_key or "").strip()}
        try:
            r = subprocess.run([*command.split(), str(script), *args], input=(stdin or "").encode(), capture_output=True,
                               env=env, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"helper timed out after {timeout_seconds} s") from e
        return r.stdout.decode("utf-8", "replace")

    return run


class JarvislabsProvider(GpuProvider):
    def __init__(self, cfg: JarvislabsConfig, api_key: str, runner: Runner):
        self._cfg, self._key, self._runner = cfg, (api_key or "").strip(), runner
        self._first_seen: dict[str, datetime] = {}
        self._hourly: dict[str, float] = {}
        self._last_why = "no GPU passes the filter"
        self._cached_at = 0.0
        self._cached: list[Quote] = []

    def provider(self) -> str:
        return "jarvislabs"

    def configured(self) -> bool:
        return bool(self._key)

    def enabled_by_default(self) -> bool:
        return False  # off until the canary has run a real worker

    def guard(self, job_argv, max_runtime_minutes, idle_minutes, created_at):
        return self_stop.wrap_exit(job_argv, max_runtime_minutes, idle_minutes, created_at)

    # ---- preflight

    def preflight(self, spec: GpuSpec, probe: CreateRequest) -> Preflight:
        if not self.configured():
            return Preflight(False, ["not configured (JARVISLABS_API_KEY missing)"])
        checks: list[str] = []
        ok = True
        try:
            script = self.start_script(probe)
            checks.append(f"startup script {len(script)} bytes (limit used here {self._cfg.script_max_bytes}; JarvisLabs publishes none)")
            if len(script) > self._cfg.script_max_bytes:
                ok = False
                checks.append("FAIL: startup script too large")
        except Exception as e:  # noqa: BLE001
            ok = False
            checks.append(f"FAIL: startup script cannot be built: {e}")
        if len(probe.name) > 40:
            ok = False
            checks.append("FAIL: instance name longer than 40 characters")
        try:
            b = self._call(["balance"], None)
            bal = _num(b.get("balance"))
            checks.append(f"read-only balance: {bal} {_str(b.get('currency'))} (min {self._cfg.min_balance})")
            if bal < self._cfg.min_balance:
                ok = False
                checks.append("FAIL: balance below the minimum; a zero balance pauses instances and can delete their data")
        except Exception as e:  # noqa: BLE001
            ok = False
            checks.append(f"FAIL: balance check: {e}")
        q = self.quotes(spec)
        if not q:
            ok = False
            checks.append(f"FAIL: read-only GPU list shows nothing rentable: {self._last_why}")
        else:
            checks.append(f"read-only GPU list: {len(q)} option(s), cheapest {q[0].gpu} ${fixed(q[0].hourly_usd, 2)}/h")
        return Preflight(ok, checks)

    # ---- quote

    def quote(self, spec: GpuSpec) -> Quote:
        all_quotes = self.quotes(spec)
        return all_quotes[0] if all_quotes else Quote.unavailable("jarvislabs", self._last_why)

    def quotes(self, spec: GpuSpec) -> list[Quote]:
        import time

        now = time.time()
        if now - self._cached_at < self._cfg.cache_seconds and self._cached:
            return self._cached
        fresh = self._search(spec)
        self._cached = fresh
        self._cached_at = 0 if not fresh else now
        return fresh

    def _search(self, spec: GpuSpec) -> list[Quote]:
        rows = self._call_list(["gpus"], None)
        out: list[Quote] = []
        why: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            typ = _str(row.get("gpu_type"))
            allowed = next((g for g in self._cfg.gpus if g.type.lower() == typ.lower()), None)
            if allowed is None:
                continue
            kind = _str(row.get("workload_type"))
            price = self._to_usd(_num(row.get("price_per_hour")), _str(row.get("currency")))
            reject = ("too little VRAM" if allowed.vram_gb < spec.min_vram_gb else "none free" if _num(row.get("free")) < 1
                      else "no live price" if price <= 0 else "not a container template GPU" if kind and kind.lower() != "container" else None)
            if reject is not None:
                why[f"{typ}: {reject}"] = ""
                continue
            region = _str(row.get("region"))
            out.append(Quote("jarvislabs", True, f"FREE {int(_num(row.get('free')))}", f"{typ}|{region}", price,
                             f"{typ} in {region}, ${fixed(price, 2)}/h (live list converted from the account currency; GST not confirmed in it)"))
        out.sort(key=lambda q: q.hourly_usd)
        self._last_why = "" if out else ("no allowed GPU in the JarvisLabs list" if not why else f"every GPU was rejected: {list(why)}")
        return out

    # ---- create

    def create(self, req: CreateRequest) -> Instance:
        if not req.name.startswith(LABEL_PREFIX):
            raise ValueError("instance name must carry the studio prefix so the watchdog can see it")
        g = req.quote.gpu.split("|")
        body: dict[str, Any] = {"name": req.name, "gpu_type": g[0]}
        if len(g) > 1 and g[1].strip():
            body["region"] = g[1]
        body.update({"storage": max(req.disk_gb, 40), "template": self._cfg.template, "http_ports": str(self._cfg.service_port),
                     "script": self.start_script(req)})
        r = self._call(["create"], json.dumps(body))
        id_ = self._id_of(r)
        if id_ is None:
            raise RuntimeError("JarvisLabs create returned no machine_id")
        self._first_seen[id_] = datetime.now(UTC)
        self._hourly[id_] = req.quote.hourly_usd
        return Instance("jarvislabs", id_, req.name, req.quote.hourly_usd, g[0], self._endpoint_of(r))

    def start_script(self, req: CreateRequest) -> str:
        """Bash startup script: exports the worker env (checked against a safe character set) and starts the worker in the
        background, told to listen on the service port JarvisLabs publishes."""
        sb = ["#!/bin/bash\numask 077\n"]
        for k, v in (req.env or {}).items():
            if not k or SAFE_ENV.fullmatch(k) is None or SAFE_ENV.fullmatch(v) is None:
                raise ValueError(f"env {k} has characters that cannot go into the startup script")
            if k != "SV_PORT":
                sb.append(f"export {k}={v}\n")
        sb.append(f"export SV_PORT={self._cfg.service_port}\nnohup setsid")
        for a in req.boot_argv:
            sb.append(" " + shell_quote(a))
        sb.append(" > /tmp/projectv-worker.log 2>&1 &\n")
        return "".join(sb)

    # ---- status / list / delete

    def status(self, instance_id: str) -> InstanceStatus:
        try:
            m = self._call(["get", instance_id], None)
        except _NotFoundError:
            return InstanceStatus(State.GONE, None, "not found")
        st = _str(m.get("status"))
        ep = self._endpoint_of(m)
        if st.lower() == "running":
            return InstanceStatus(State.STARTING, None, "running, no endpoint yet") if ep is None else InstanceStatus(State.RUNNING, ep, st)
        low = st.lower()
        if any(w in low for w in ("fail", "destroy", "delet", "pause")):
            return InstanceStatus(State.GONE, None, st)  # a paused instance still bills storage: the broker destroys it
        born = self._first_seen.setdefault(instance_id, datetime.now(UTC))
        if datetime.now(UTC) > born + timedelta(seconds=self._cfg.start_timeout_seconds * 3):
            return InstanceStatus(State.GONE, None, f"never became running ({st})")
        return InstanceStatus(State.STARTING, None, st)

    def endpoint_for(self, instance_id: str, port: int) -> str | None:
        try:
            return self._endpoint_of(self._call(["get", instance_id], None))
        except Exception as e:  # noqa: BLE001
            log.warning("JarvisLabs: could not read the address of instance %s: %s", instance_id, e)
            return None

    def list_pods(self) -> list[Pod]:
        """All instances of the account (the watchdog filters by the studio name prefix)."""
        out = []
        for m in self._call_list(["list"], None):
            if not isinstance(m, dict):
                continue
            id_ = self._id_of(m)
            if id_ is None:
                continue
            born = self._first_seen.setdefault(id_, datetime.now(UTC))  # the SDK has no creation time
            known = self._hourly.get(id_)
            status = _str(m.get("status")).upper() or "LOADING"
            out.append(Pod(id_, _str(m.get("name")), status, known if known is not None else _num(m.get("cost")), born, born))
        return out

    def terminate(self, pod_id: str) -> None:
        try:
            self._call(["destroy", str(int(pod_id))], None)
        except _NotFoundError:
            pass  # already gone
        self._first_seen.pop(pod_id, None)
        self._hourly.pop(pod_id, None)

    # ---- helpers

    def _call_raw(self, args: list[str], stdin: str | None) -> Any:
        try:
            out = self._runner(args, stdin)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"JarvisLabs helper failed: {self._scrub(str(e))}") from None
        try:
            parsed = json.loads((out or "").strip())
        except ValueError:
            raise RuntimeError(f"JarvisLabs helper returned no JSON: {self._scrub(abbreviate(out))}") from None
        if isinstance(parsed, dict) and parsed.get("error") is not None:
            typ = _str(parsed.get("type"))
            msg = f"JarvisLabs refused the call ({typ}): {self._scrub(abbreviate(_str(parsed['error'])))}"
            raise _NotFoundError(msg) if typ == "NotFoundError" else RuntimeError(msg)
        return parsed

    def _call(self, args: list[str], stdin: str | None) -> dict[str, Any]:
        parsed = self._call_raw(args, stdin)
        if not isinstance(parsed, dict):
            raise RuntimeError("JarvisLabs helper returned a list where an object was expected")
        return parsed

    def _call_list(self, args: list[str], stdin: str | None) -> list[Any]:
        parsed = self._call_raw(args, stdin)
        if not isinstance(parsed, list):
            raise RuntimeError("JarvisLabs helper returned an object where a list was expected")
        return parsed

    @staticmethod
    def _id_of(m: dict[str, Any]) -> str | None:
        x = m.get("machine_id")
        return str(int(x)) if isinstance(x, (int, float)) and not isinstance(x, bool) else None

    @staticmethod
    def _endpoint_of(m: dict[str, Any]) -> str | None:
        """The first listed endpoint, else None. The format is not documented: a bare host gets https://."""
        eps = m.get("endpoints")
        if isinstance(eps, list) and eps and eps[0] is not None:
            e = str(eps[0]).strip().rstrip("/")
            return None if not e else e if "://" in e else "https://" + e
        return None

    def _scrub(self, s: str) -> str:
        return s if not self._key else s.replace(self._key, "***")

    def _to_usd(self, amount: float, currency: str) -> float:
        """The live list is in the account currency; INR (the owner's account) is converted with the configured rate."""
        return amount / self._cfg.inr_per_usd if currency.upper() == "INR" else amount
