"""E2E Networks adapter (MyAccount REST https://api.e2enetworks.com/myaccount/api/v1, API key in the query string plus the bearer
token). Port of E2eProvider and E2eConfig.

E2E rents VMs, not containers, so the worker runs as a Docker container started by a cloud-init start script. Same safety design as the
Vast adapter (docs/e2e-provider-notes.md):
- No secret goes to the node. The start scripts hold the worker env only (boot payload, port, root, the per-rental worker token), in a
  root-only file. Never the E2E key or token, nor any other key. The mandatory SSH key is a public key whose private half is generated
  in memory and thrown away, and password login is off: nobody can log in.
- Teardown is ours. terminate() calls DELETE nodes/{id}/. Power-off does not stop E2E billing, only delete does. The in-VM guard only
  ends the worker (self_stop.wrap_exit). The watchdog lists this provider's nodes by the studio name prefix.
- Quotes come from the live plan list (GET images/); it has no stock field, so availability is only known when create succeeds, and the
  broker tries the next candidate when it fails.
Shapes as the Java version took them from docs.e2enetworks.com on 22 Nov 2026. NOT exercised against the live API in this port.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import struct
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote as urlquote

import httpx

from studio.agents.jfmt import fixed
from studio.gpu import self_stop
from studio.gpu.broker.provider import CreateRequest, GpuProvider, GpuSpec, Instance, InstanceStatus, PlanUnavailableError, Preflight, Quote, State
from studio.gpu.broker.runpod import LABEL_PREFIX, USER_AGENT, abbreviate, http_client, safe_env, shell_quote
from studio.gpu.pods import Pod

log = logging.getLogger(__name__)

# E2E rejects a start_scripts entry over 2048 characters (HTTP 400, 22 Nov 2026). Slices are cut so a whole entry stays below the limit.
MAX_ENTRY = 2048
SLICE = 1700
_PRICE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*/\s*hour", re.IGNORECASE)
_MULTI_GPU_TEMPLATE = re.compile(r"[.\-_]([2-9]|1[0-9])x[A-Za-z]")  # a plan name that says 2x/4x/8x GPUs in its template part
_NODE_NAME = re.compile(r"[A-Za-z0-9-]{3,50}")


@dataclass
class E2ePlan:
    """One allowed 1-GPU plan: E2E plan sku_name, a short GPU label and its VRAM (the plan list has no VRAM field)."""

    sku: str
    gpu: str
    vram_gb: int
    image: str | None = None


@dataclass
class E2eConfig:
    plans: list[E2ePlan] = field(default_factory=lambda: [
        E2ePlan("GDC3.L40S-60.220GB_v1", "L40S", 48),
        # the 22.04 template of this SKU is named "2xA40-32.200GB_Ubuntu22": the clean single-GPU template is the 20.04 one
        E2ePlan("GDC3.A40-16.100GB", "A40", 48, "Ubuntu-20.04-GPU")])
    location: str = "Delhi"  # MyAccount GPU nodes exist only in Delhi
    image: str = "Ubuntu-22.04-GPU"  # 22.04 GPU images ship Docker and the NVIDIA Container Toolkit (24.04 does not)
    inr_per_usd: float = 94
    gst_percent: float = 18
    public_port: int = 443  # the default security group already opens 80 and 443
    start_timeout_seconds: int = 540
    poll_seconds: int = 10
    cache_seconds: int = 30

    @staticmethod
    def from_yaml(root: dict[str, Any]) -> E2eConfig:
        def d(x: Any) -> dict[str, Any]:
            return x if isinstance(x, dict) else {}

        v = d(d(d(d(root).get("pricing")).get("gpuBroker")).get("providers")).get("e2e")
        v = d(v)

        def num(k: str, default: float) -> float:
            x = v.get(k)
            return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else default

        c = E2eConfig()
        if isinstance(v.get("plans"), list) and v["plans"]:
            c.plans = [E2ePlan(str(p.get("sku")), str(p.get("gpu")), int(p.get("vramGb") or 0), None if p.get("image") is None else str(p["image"]))
                       for p in v["plans"] if isinstance(p, dict)]
        c.location = str(v["location"]) if v.get("location") is not None else "Delhi"
        c.image = str(v["image"]) if v.get("image") is not None else "Ubuntu-22.04-GPU"
        c.inr_per_usd, c.gst_percent = max(1.0, num("inrPerUsd", 94)), max(0.0, num("gstPercent", 18))
        c.public_port, c.start_timeout_seconds = int(num("publicPort", 443)), int(num("startTimeoutSeconds", 540))
        c.poll_seconds, c.cache_seconds = int(num("pollSeconds", 10)), int(num("cacheSeconds", 30))
        return c


class _NotFound(RuntimeError):
    pass


def _num(o: Any) -> float:
    if isinstance(o, (int, float)) and not isinstance(o, bool):
        return float(o)
    if isinstance(o, str) and re.fullmatch(r"[0-9.]+", o):
        return float(o)
    return 0.0


def _str(o: Any) -> str:
    return "" if o is None else str(o)


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _slices(s: str, size: int) -> list[str]:
    return [s[i : i + size] for i in range(0, len(s), size)]


def _append_script(file: str, piece: str) -> str:
    """Entry that appends one slice (safe characters and newlines only, so single quotes cannot be broken) to a file in /opt/projectv."""
    return f"#!/bin/bash\numask 077\nmkdir -p /opt/projectv\nprintf '%s' '{piece}' >> /opt/projectv/{file}\n"


def throwaway_key() -> str:
    """OpenSSH public key from a fresh Ed25519 pair; the private key is not kept. Without the `cryptography` package, 32 random bytes
    in the same wire format: a public key whose private half cannot exist."""
    raw = None
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        raw = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    except Exception:  # noqa: BLE001
        import secrets

        raw = secrets.token_bytes(32)
    kind = b"ssh-ed25519"
    blob = struct.pack(">I", len(kind)) + kind + struct.pack(">I", len(raw)) + raw
    return "ssh-ed25519 " + base64.b64encode(blob).decode() + " projectv-no-login"


class E2eProvider(GpuProvider):
    def __init__(self, cfg: E2eConfig, base_url: str, api_key: str, auth_token: str, project_id: str,
                 client: httpx.Client | None = None, poll_millis: int | None = None):
        self._cfg = cfg
        self._base = base_url.rstrip("/")
        self._key, self._token, self._project = (api_key or "").strip(), (auth_token or "").strip(), (project_id or "").strip()
        self._http = client or http_client(60, USER_AGENT)
        self._poll = (cfg.poll_seconds * 1000 if poll_millis is None else poll_millis) / 1000.0
        self._throwaway_key = throwaway_key()
        self._first_seen: dict[str, datetime] = {}
        self._hourly: dict[str, float] = {}
        self._last_why = "no plan passes the filter"
        self._cached_at = 0.0
        self._cached: list[Quote] = []

    def provider(self) -> str:
        return "e2e"

    def configured(self) -> bool:
        return bool(self._key) and bool(self._token)

    def enabled_by_default(self) -> bool:
        return False  # off until the owner has seen a real node run

    def guard(self, job_argv, max_runtime_minutes, idle_minutes, created_at):
        """No account key on the node: the guard only ends the worker; the control plane deletes the node."""
        return self_stop.wrap_exit(job_argv, max_runtime_minutes, idle_minutes, created_at)

    def preflight(self, spec: GpuSpec, probe: CreateRequest) -> Preflight:
        """Local checks on the real create body (start-script sizes, env characters) plus the read-only plan list (plan/image pairing)."""
        if not self.configured():
            return Preflight(False, ["not configured (E2E_API_KEY / E2E_AUTH_TOKEN missing)"])
        checks: list[str] = []
        ok = True
        try:
            scripts = self.start_scripts(probe)
            longest = max((len(s) for s in scripts), default=0)
            checks.append(f"start scripts: {len(scripts)} piece(s), longest {longest} chars (limit used here {MAX_ENTRY})")
            if longest > MAX_ENTRY:
                ok = False
                checks.append(f"FAIL: a start script is longer than {MAX_ENTRY}")
        except Exception as e:  # noqa: BLE001
            ok = False
            checks.append(f"FAIL: start script cannot be built: {e}")
        plans = self.quotes(spec)
        if not plans:
            ok = False
            checks.append(f"FAIL: no allowed plan with image '{self._cfg.image}' in the live plan list: {self._last_why}")
        else:
            checks.append(f"plan/image pairing: {len(plans)} plan(s) offered with image '{self._cfg.image}', cheapest ${fixed(plans[0].hourly_usd, 2)}/h")
        return Preflight(ok, checks)

    # ---- quote

    def quote(self, spec: GpuSpec) -> Quote:
        all_quotes = self.quotes(spec)
        return all_quotes[0] if all_quotes else Quote.unavailable("e2e", self._last_why)

    def quotes(self, spec: GpuSpec) -> list[Quote]:
        now = time.time()
        if now - self._cached_at < self._cfg.cache_seconds and self._cached:
            return self._cached
        fresh = self._search(spec)
        self._cached = fresh
        self._cached_at = 0 if not fresh else now
        return fresh

    def _image_of(self, plan: E2ePlan) -> str:
        return plan.image if plan.image is not None else self._cfg.image

    def _search(self, spec: GpuSpec) -> list[Quote]:
        r = self._call(lambda: self._http.get(self._url("images/", {"os": "Ubuntu", "category": "GPU"}), headers=self._auth()))
        best: dict[str, Quote] = {}
        why: dict[str, str] = {}
        if isinstance(r, dict) and isinstance(r.get("data"), list):
            for row in r["data"]:
                if not isinstance(row, dict):
                    continue
                plan = next((p for p in self._cfg.plans if p.sku == _str(row.get("name"))), None)
                if plan is None or self._image_of(plan) != _str(row.get("image")) or _MULTI_GPU_TEMPLATE.search(_str(row.get("plan"))):
                    continue  # E2E names some single-GPU plans after a 2x/4x template: never rent one whose name says several GPUs
                s = row.get("specs") if isinstance(row.get("specs"), dict) else {}
                reject = self._problem(plan, s, spec)
                if reject is not None:
                    why[f"{plan.gpu}: {reject}"] = ""
                    continue
                inr = _num(s.get("price_per_hour"))
                usd = inr * (1 + self._cfg.gst_percent / 100.0) / self._cfg.inr_per_usd
                plan_name = _str(row.get("plan"))
                detail = (f"{plan.sku}, {fixed(_num(s.get('cpu')), 0)} vCPU, {fixed(_num(s.get('ram')), 0)} GB RAM, "
                          f"{fixed(_num(s.get('disk_space')), 0)} GB disk, Rs {fixed(inr, 0)}/h + {fixed(self._cfg.gst_percent, 0)}% GST = "
                          f"${fixed(usd, 2)}/h (Rs {fixed(self._cfg.inr_per_usd, 0)} per $); stock unknown until create")
                q = Quote("e2e", True, "ON-DEMAND", f"{plan.gpu}|{plan.sku}|{plan_name}|{self._image_of(plan)}", usd, detail)
                prev = best.get(plan.sku)  # several OS templates per card: prefer the CUDA 12 one
                best[plan.sku] = q if prev is None or (not ("Cuda12" in prev.gpu) and "Cuda12" in q.gpu) else prev
        out = sorted(best.values(), key=lambda q: q.hourly_usd)
        self._last_why = ("" if out else "no allowed GPU plan in the E2E plan list" if not why
                          else f"every plan was rejected: {list(why)}")
        return out

    @staticmethod
    def _problem(plan: E2ePlan, s: dict[str, Any], spec: GpuSpec) -> str | None:
        if plan.vram_gb < spec.min_vram_gb:
            return "too little VRAM"
        if _num(s.get("ram")) < spec.min_ram_gb:
            return "too little RAM"
        if _num(s.get("disk_space")) < spec.disk_gb:
            return "not enough disk"
        if _num(s.get("price_per_hour")) <= 0:
            return "no live price"
        return None

    # ---- create

    def create(self, req: CreateRequest) -> Instance:
        if not req.name.startswith(LABEL_PREFIX):
            raise ValueError("node name must carry the studio prefix so the watchdog can see it")
        g = req.quote.gpu.split("|")
        if len(g) < 3:
            raise ValueError(f"not an E2E quote: {req.quote.gpu}")
        body = self.create_body(req, g[2], g)
        r = self._call(lambda: self._http.post(self._url("nodes/", {}), headers=self._auth(), json=body))
        data = r.get("data") if isinstance(r, dict) else None
        if isinstance(data, list) and data and isinstance(data[0], dict):
            data = data[0]  # we ask for exactly one node, but tolerate a one-element list
        if not isinstance(data, dict) or not isinstance(data.get("id"), (int, float)):
            raise RuntimeError("E2E create returned no node id: " + ("empty reply" if not isinstance(r, dict) else abbreviate(_str(r.get("message")))))
        id_ = str(int(data["id"]))
        self._first_seen[id_] = datetime.now(UTC)
        self._hourly[id_] = req.quote.hourly_usd
        endpoint = self._wait_for_endpoint(id_)
        return Instance("e2e", id_, req.name, req.quote.hourly_usd, g[0], endpoint)

    def create_body(self, req: CreateRequest, plan: str, g: list[str]) -> dict[str, Any]:
        """The create body. No account key, no password, no usable SSH key."""
        if _NODE_NAME.fullmatch(req.name) is None:
            raise ValueError(f"E2E node names are 3-50 letters, digits and hyphens: {req.name}")
        return {
            "name": req.name, "label": "default", "plan": plan, "image": g[3] if len(g) > 3 else self._cfg.image,
            "ssh_keys": [self._throwaway_key], "number_of_instances": 1, "default_public_ip": True, "disable_password": True,
            "start_scripts": self.start_scripts(req),
        }

    def start_scripts(self, req: CreateRequest) -> list[str]:
        """Cloud-init scripts. E2E rejects a start_scripts entry over 2048 characters, so the payload goes over many small entries: the
        env file in plain slices (its values are base64 text, already safe), the run script (docker wait + docker run + the guard)
        base64-encoded in slices, and a last entry that checks both sha256 sums on the node and only then starts the worker. Nothing is
        fetched from anywhere; no account key is in any entry."""
        env = ""
        for k, v in (req.env or {}).items():
            if not safe_env(k, v):
                raise ValueError(f"env {k} has characters that cannot go into the E2E start script")
            env += f"{k}={v}\n"
        run = ("#!/bin/bash\n"
               "for i in $(seq 1 90); do docker info >/dev/null 2>&1 && break; sleep 2; done\n"
               "docker run -d --name projectv-worker --gpus all --shm-size=8g --env-file /opt/projectv/worker.env"
               f" -p {self._cfg.public_port}:{req.port} {shell_quote(req.image)}")
        for a in req.boot_argv:
            run += " " + shell_quote(a)
        run += "\n"
        env_b, run_b = env.encode(), run.encode()
        scripts = [_append_script("worker.env", piece) for piece in _slices(env, SLICE)]
        scripts += [_append_script("run.b64", piece) for piece in _slices(base64.b64encode(run_b).decode(), SLICE)]
        scripts.append("#!/bin/bash\ncd /opt/projectv || exit 1\n"
                       f"if echo '{sha256(env_b)}  worker.env' | sha256sum -c - && base64 -d run.b64 > run.sh"
                       f" && echo '{sha256(run_b)}  run.sh' | sha256sum -c -; then bash run.sh; "
                       "else echo 'projectv: payload check failed, worker not started' >&2; exit 1; fi\n")
        for sc in scripts:
            if len(sc) > MAX_ENTRY:
                raise RuntimeError(f"E2E start script entry of {len(sc)} chars exceeds the {MAX_ENTRY} limit")
        return scripts

    def _wait_for_endpoint(self, id_: str) -> str:
        """Polls the new node until it runs with a public IP; deletes it when it dies or takes too long."""
        deadline = time.time() + self._cfg.start_timeout_seconds
        state = "no status yet"
        while True:
            node = None
            try:
                node = self._fetch(id_)
            except Exception as e:  # noqa: BLE001
                state = f"status unreadable: {e}"
            if node is not None:
                state = _str(node.get("status"))
                ep = self._endpoint_of(node)
                if state.lower() == "running" and ep is not None:
                    return ep
                if self._is_dead(state):
                    raise self._failed_start(id_, f"node state '{state}' (it will never run)")
            if time.time() >= deadline:
                raise self._failed_start(id_, f"not running with a public IP after {self._cfg.start_timeout_seconds} s (last state: {state})")
            time.sleep(self._poll)

    def _failed_start(self, id_: str, why: str) -> RuntimeError:
        try:
            self.terminate(id_)
            return RuntimeError(f"E2E node {id_} deleted: {why}")
        except Exception as e:  # noqa: BLE001
            # E2E refuses delete while a node is still being created; the watchdog (name prefix) retries.
            return RuntimeError(f"E2E node {id_} {why} and deleting it failed ({e}); the watchdog will retry")

    @staticmethod
    def _is_dead(status: str) -> bool:
        s = status.lower()
        return any(w in s for w in ("error", "fail", "delet", "terminat", "power", "stopp")) or s == "off"

    # ---- status / list / delete

    def status(self, instance_id: str) -> InstanceStatus:
        try:
            node = self._fetch(instance_id)
        except _NotFound:
            return InstanceStatus(State.GONE, None, "404")
        if not node:
            return InstanceStatus(State.GONE, None, "not found")
        st = _str(node.get("status"))
        if st.lower() == "running":
            ep = self._endpoint_of(node)
            return InstanceStatus(State.STARTING, None, "running, no public IP yet") if ep is None else InstanceStatus(State.RUNNING, ep, st)
        if self._is_dead(st):
            return InstanceStatus(State.GONE, None, st)  # a powered-off node still bills: the broker deletes it
        born = self._first_seen.setdefault(instance_id, datetime.now(UTC))
        if datetime.now(UTC) > born + timedelta(seconds=self._cfg.start_timeout_seconds * 3):
            return InstanceStatus(State.GONE, None, f"never became running ({st})")
        return InstanceStatus(State.STARTING, None, st)

    def endpoint_for(self, instance_id: str, port: int) -> str | None:
        try:
            node = self._fetch(instance_id)
            return None if node is None else self._endpoint_of(node)
        except Exception as e:  # noqa: BLE001
            log.warning("E2E: could not read the address of node %s: %s", instance_id, e)
            return None

    def list_pods(self) -> list[Pod]:
        """All nodes of the account (the watchdog filters by the studio name prefix)."""
        r = self._call(lambda: self._http.get(self._url("nodes/", {}), headers=self._auth()))
        if not isinstance(r, dict) or not isinstance(r.get("data"), list):
            raise RuntimeError("E2E list nodes returned no data array")
        out = []
        for node in r["data"]:
            if isinstance(node, dict):
                id_ = str(int(_num(node.get("id"))))
                born = self._born(id_, node)
                out.append(Pod(id_, _str(node.get("name")), _str(node.get("status")).upper() or "LOADING", self._hourly_of(id_, node), born, born))
        return out

    def terminate(self, pod_id: str) -> None:
        try:
            r = self._call(lambda: self._http.delete(self._url(f"nodes/{int(pod_id)}/", {}), headers=self._auth()))
            if isinstance(r, dict) and isinstance(r.get("code"), (int, float)) and r["code"] >= 400:
                raise RuntimeError(f"E2E delete {pod_id} refused: {abbreviate(_str(r.get('message')))}")
        except _NotFound:
            pass  # already gone
        self._first_seen.pop(pod_id, None)
        self._hourly.pop(pod_id, None)

    # ---- helpers

    def _fetch(self, id_: str) -> dict[str, Any] | None:
        r = self._call(lambda: self._http.get(self._url(f"nodes/{int(id_)}/", {}), headers=self._auth()))
        return r["data"] if isinstance(r, dict) and isinstance(r.get("data"), dict) else None

    def _endpoint_of(self, node: dict[str, Any]) -> str | None:
        ip = _str(node.get("public_ip_address")).strip()
        return None if not ip or ip in ("[]", "null") else f"http://{ip}:{self._cfg.public_port}"

    def _born(self, id_: str, node: dict[str, Any]) -> datetime:
        c = _str(node.get("created_at"))
        if c:
            try:
                return datetime.fromisoformat(c)
            except ValueError:
                pass  # fall through to first-seen
        return self._first_seen.setdefault(id_, datetime.now(UTC))

    def _hourly_of(self, id_: str, node: dict[str, Any]) -> float:
        """Our own quote when we created it; else 'Rs. 102.0/Hour' from the node (plus GST, in dollars); else 0 (the watchdog does not need it)."""
        known = self._hourly.get(id_)
        if known is not None:
            return known
        m = _PRICE.search(_str(node.get("price")))
        return float(m.group(1)) * (1 + self._cfg.gst_percent / 100.0) / self._cfg.inr_per_usd if m else 0.0

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _url(self, path: str, extra: dict[str, str]) -> str:
        """The URL with apikey, location and project; built only here. It carries the key, so it is never logged or put in a message."""
        q = f"apikey={urlquote(self._key, safe='')}&location={urlquote(self._cfg.location, safe='')}"
        if self._project:
            q += f"&project_id={urlquote(self._project, safe='')}"
        for k, v in extra.items():
            q += f"&{k}={urlquote(v, safe='')}"
        return f"{self._base}/{path}?{q}"

    def _call(self, send) -> Any:
        """Runs one E2E call and returns its JSON; error text is scrubbed of the key and token (a connection error message contains the full URL)."""
        try:
            resp = send()
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"E2E call failed: {type(e).__name__}: {self._scrub(str(e))}") from None
        if resp.status_code == 404:
            raise _NotFound("404")
        if resp.status_code >= 400:
            body = resp.text
            if resp.status_code == 400 and "temporarily not available" in body.lower():
                raise PlanUnavailableError(f"E2E plan out of stock (HTTP 400): {self._scrub(abbreviate(body))}")
            raise RuntimeError(f"E2E refused the call (HTTP {resp.status_code}): {self._scrub(abbreviate(body))}"
                               + (" (check E2E_API_KEY / E2E_AUTH_TOKEN in the MyAccount API settings)" if resp.status_code in (401, 403) else ""))
        try:
            return resp.json() if resp.content else None
        except ValueError:
            return None

    def _scrub(self, s: str) -> str:
        out = s
        for secret in (self._key, self._token):
            if secret:
                out = out.replace(secret, "***").replace(urlquote(secret, safe=""), "***")
        return out
