"""Vast.ai adapter (REST v0 https://console.vast.ai/api/v0, bearer key). Port of VastProvider and VastConfig.

Vast hosts are other people's machines, so the design is defensive (docs/m3-gpu-router-runbook.md, "Vast.ai"):
- No secret goes to the instance. The create body holds the image, the studio label, the guard command and the worker env (boot
  payload, port, root, the per-rental worker token). Never this key, the RunPod key, S3 or Gemini keys. SSH is off (launch mode
  `args`), so no SSH key is registered either.
- Teardown is ours. terminate() calls DELETE /instances/{id}/; the in-pod guard only ends the worker (self_stop.wrap_exit). The
  watchdog lists this provider's instances by the studio label.
- Offers: on-demand, verified, reliable, 1 GPU, enough VRAM, RAM, disk and download speed, a direct public port, price from the live
  offer plus the host's own internet price per GB (see VastConfig).

Shapes as the Java version checked them on 21-22 Nov 2026. NOT exercised against the live API in this port (no spend allowed). With a
2FA account, instance and account calls need a 2FA session key (`vastai tfa login`, saved in ~/.config/vastai/vast_tfa_key, or our own
TOTP login from VAST_TOTP_SECRET); the normal key is used only for offer search. Instance list/show use GET /api/v1/instances/ (the v0
list is gone: 410); create and destroy stay /api/v0.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from studio.agents.jfmt import fixed
from studio.gpu import self_stop
from studio.gpu.broker.provider import CreateRequest, GpuProvider, GpuSpec, Instance, InstanceStatus, Quote, State
from studio.gpu.broker.runpod import LABEL_PREFIX, USER_AGENT, abbreviate, http_client, safe_env
from studio.gpu.broker.support import millis_to_next_window, totp_code
from studio.gpu.pods import Pod

log = logging.getLogger(__name__)

SESSION_HELP = "Vast 2FA session expired: run `vastai tfa login --method-type totp -c <code>`"


@dataclass
class VastConfig:
    """Offer filter and timing from pricing.gpuBroker.providers.vast: limits and estimates only (the hourly price always comes from the
    live offer, rule 7). Every field has a default so a missing key never disables a safety limit."""

    gpu_names: list[str] = field(default_factory=lambda: ["RTX 4090", "RTX 5090", "L40S", "RTX A6000"])
    min_reliability: float = 0.97  # host reliability score, 0..1
    min_down_mbps: float = 500  # measured download speed of the host, the weights are ~42 GB
    disk_gb: int = 90  # container disk to rent (and the minimum free disk an offer must have)
    max_bandwidth_usd: float = 0.60  # an offer whose estimated internet bill is above this is skipped
    download_gb: float = 50
    upload_gb: float = 3
    amortize_hours: float = 0.75  # the bandwidth bill is spread over this many hours to give the effective hourly price
    start_timeout_seconds: int = 480  # an instance not running with its port mapped after this long is destroyed
    poll_seconds: int = 5
    offer_attempts: int = 2  # offers tried per create (an offer may be taken by someone else between search and create)
    price_slack: float = 1.25  # a replacement offer may cost at most this many times the quoted price
    cache_seconds: int = 15  # search results are reused this long (Vast rate-limits)

    @staticmethod
    def from_yaml(root: dict[str, Any]) -> VastConfig:
        def d(x: Any) -> dict[str, Any]:
            return x if isinstance(x, dict) else {}

        v = d(d(d(d(root).get("pricing")).get("gpuBroker")).get("providers")).get("vast")
        v = d(v)

        def num(k: str, default: float) -> float:
            x = v.get(k)
            return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else default

        c = VastConfig()
        if isinstance(v.get("gpuNames"), list) and v["gpuNames"]:
            c.gpu_names = [str(x) for x in v["gpuNames"]]
        c.min_reliability, c.min_down_mbps, c.disk_gb = num("minReliability", 0.97), num("minDownMbps", 500), int(num("diskGb", 90))
        c.max_bandwidth_usd, c.download_gb, c.upload_gb = num("maxBandwidthUsd", 0.60), num("downloadGb", 50), num("uploadGb", 3)
        c.amortize_hours, c.start_timeout_seconds = max(0.1, num("amortizeHours", 0.75)), int(num("startTimeoutSeconds", 480))
        c.poll_seconds, c.offer_attempts = int(num("pollSeconds", 5)), max(1, int(num("offerAttempts", 2)))
        c.price_slack, c.cache_seconds = max(1.0, num("priceSlack", 1.25)), int(num("cacheSeconds", 15))
        return c


class VastSessionExpired(RuntimeError):
    """The account key was refused: the 2FA session is missing or has expired. Never carries a key."""


class StartFailed(RuntimeError):
    """An instance was created but did not come up; it has been destroyed (or destroying it failed and is in the message)."""

    def __init__(self, message: str, destroyed: bool):
        super().__init__(message)
        self.destroyed = destroyed


def session_key_source(setting: str | None, file: Path) -> Callable[[], str]:
    """The 2FA session key: the setting if given, else the file `vastai tfa login` writes. The file is read on every call so a
    fresh login is picked up without a restart. The key is never logged."""
    fixed_key = (setting or "").strip()

    def get() -> str:
        if fixed_key:
            return fixed_key
        try:
            return file.read_text(encoding="utf-8").strip() if file.is_file() else ""
        except Exception:  # noqa: BLE001
            return ""

    return get


def default_session_file() -> Path:
    return Path.home() / ".config" / "vastai" / "vast_tfa_key"


def _num(o: Any) -> float:
    return float(o) if isinstance(o, (int, float)) and not isinstance(o, bool) else 0.0


def _str(o: Any) -> str:
    return "" if o is None else str(o)


def _long(o: Any) -> int:
    return int(_num(o))


def endpoint_of(inst: dict[str, Any], port: int) -> str | None:
    """http://<public ip>:<mapped port> from ports["<port>/tcp"][0].HostPort; None until the mapping exists."""
    ip = _str(inst.get("public_ipaddr")).strip()
    ports = inst.get("ports")
    if not ip or not isinstance(ports, dict):
        return None
    mapped = ports.get(f"{port}/tcp")
    if isinstance(mapped, list) and mapped and isinstance(mapped[0], dict) and mapped[0].get("HostPort") is not None:
        return f"http://{ip}:{mapped[0]['HostPort']}"
    return None


def _is_dead(status: str) -> bool:
    return status in ("exited", "offline", "unknown")


class VastProvider(GpuProvider):
    def __init__(self, cfg: VastConfig, base_url: str, api_key: str, session_key: Callable[[], str], totp_secret: str = "",
                 client: httpx.Client | None = None, poll_millis: int | None = None):
        self._cfg = cfg
        self._base = base_url.rstrip("/")
        # the instance list lives under /api/v1 (the v0 list answers 410); create and destroy are still /api/v0
        self._v1 = self._base[:-2] + "v1" if self._base.endswith("/api/v0") else self._base
        self._key = (api_key or "").strip()
        self._session_key = session_key
        self._totp = (totp_secret or "").strip()
        self._http = client or http_client(30, USER_AGENT)
        self._poll = (cfg.poll_seconds * 1000 if poll_millis is None else poll_millis) / 1000.0
        self._auto_session = ""  # session key from our own TOTP login: memory only, never written to disk or logged
        self._last_login_at = 0.0
        self._login_blocked_until = 0.0  # after a rejected code (twice) automatic login pauses, so a wrong seed cannot lock the account
        self.min_login_gap_millis = 5_000  # test knobs: gap between two logins, wait before the one retry, pause after a double rejection
        self.retry_wait_millis: Callable[[], int] = lambda: millis_to_next_window(int(time.time() * 1000)) + 500
        self.block_millis = 10 * 60_000
        self._login_lock = threading.RLock()
        self._first_seen: dict[str, datetime] = {}
        self._last_why = "no offer passes the filter"
        self._last_spec: GpuSpec | None = None
        self._worker_port = 8000  # the worker port of the last create (status() has no port argument)
        self._cached_at = 0.0
        self._cached: list[Quote] = []

    # ------------------------------------------------------------------------------------------------ keys and login

    def provider(self) -> str:
        return "vast"

    def configured(self) -> bool:
        return bool(self._key) or bool(self._session_key())

    def enabled_by_default(self) -> bool:
        return True

    def guard(self, job_argv, max_runtime_minutes, idle_minutes, created_at):
        """No provider key on the machine: the guard only ends the worker; the control plane destroys the instance."""
        return self_stop.wrap_exit(job_argv, max_runtime_minutes, idle_minutes, created_at)

    def _search_key(self) -> str:
        return self._session_key() if not self._key else self._key

    def _account_key(self) -> str:
        """Key for instance and account calls: our own TOTP session when VAST_TOTP_SECRET is set (logged in lazily here), else the 2FA
        session key from the setting or the CLI file, else the normal key (accounts without 2FA)."""
        if self._totp:
            k = self._auto_session
            return k if k else self._login(False)
        k = self._session_key()
        return k if k else self._key

    def _login(self, force: bool) -> str:
        """POST /api/v0/tfa/ with the normal key as Bearer and {"tfa_method":"totp","code":"123456"}; the reply has session_key. At most
        two attempts: a rejected code is retried once in the next 30 s window, then login pauses (block_millis): repeated failed 2FA
        attempts can lock the account. Nothing secret is logged."""
        with self._login_lock:
            if not force and self._auto_session:
                return self._auto_session  # another thread logged in while this one waited for the lock
            now = time.time()
            if now < self._login_blocked_until:
                raise VastSessionExpired(
                    "Vast 2FA login paused after the code was rejected twice (check VAST_TOTP_SECRET and the laptop clock); "
                    f"not retrying for {int(self._login_blocked_until - now)} s to avoid locking the account. "
                    "Or run `vastai tfa login --method-type totp -c <code>`")
            gap = self.min_login_gap_millis / 1000.0 - (now - self._last_login_at)
            if gap > 0:
                time.sleep(gap)
            why = None
            for attempt in range(2):
                if attempt > 0:
                    time.sleep(self.retry_wait_millis() / 1000.0)  # the next window gives a different code
                self._last_login_at = time.time()
                code = totp_code(self._totp, int(self._last_login_at), 6)
                try:
                    resp = self._http.post(f"{self._base}/tfa/", headers={"Authorization": f"Bearer {self._key}"},
                                           json={"tfa_method": "totp", "code": code})
                    if 400 <= resp.status_code < 500:
                        why = f"HTTP {resp.status_code}"
                        continue
                    resp.raise_for_status()
                    sk = resp.json().get("session_key") if isinstance(resp.json(), dict) else None
                    if isinstance(sk, str) and sk.strip():
                        self._auto_session = sk.strip()
                        self._login_blocked_until = 0.0
                        log.info("Vast: logged in to 2FA with the TOTP seed")
                        return self._auto_session
                    why = "login answered without a session_key"
                except Exception as e:  # noqa: BLE001
                    why = type(e).__name__  # network trouble: not a rejected code, but do not hammer either
                    break
            self._login_blocked_until = time.time() + self.block_millis / 1000.0
            log.warning("Vast: 2FA login failed (%s); automatic login paused for %d s", why, self.block_millis // 1000)
            raise VastSessionExpired(
                f"Vast 2FA login failed ({why}): the TOTP code was rejected twice. Check VAST_TOTP_SECRET and the laptop clock; "
                f"automatic login is paused {self.block_millis // 60_000} min to avoid locking the account. "
                "Or run `vastai tfa login --method-type totp -c <code>`")

    def _call(self, account: bool, send: Callable[[], httpx.Response]) -> httpx.Response:
        """Runs one Vast call. account: an instance/account call (needs the 2FA session key); a 401/403 there means the session is
        missing or expired, and the message says how to log in again (it reaches the dashboard card, and the broker rents nothing)."""
        resp = send()
        if resp.status_code not in (401, 403):
            return resp
        if not account:
            raise RuntimeError(f"Vast.ai refused the key (HTTP {resp.status_code}): {abbreviate(resp.text)} "
                               "(check the key on cloud.vast.ai/manage-keys)")
        refused = resp
        if self._totp and self._auto_session:
            # the session expired: log in again (at most once per call) and retry this call once
            stale = self._auto_session
            with self._login_lock:
                if stale == self._auto_session:
                    self._auto_session = ""
            self._login(False)
            refused = send()
            if refused.status_code not in (401, 403):
                return refused
        raise VastSessionExpired(f"{SESSION_HELP} (HTTP {refused.status_code}: {abbreviate(refused.text)})")

    def _get(self, url: str, account: bool, params: dict[str, Any] | None = None) -> httpx.Response:
        return self._call(account, lambda: self._http.get(url, params=params, headers={"Authorization": f"Bearer {self._account_key() if account else self._search_key()}"}))

    # ------------------------------------------------------------------------------------------------ search

    def quote(self, spec: GpuSpec) -> Quote:
        all_quotes = self.quotes(spec)
        return all_quotes[0] if all_quotes else Quote.unavailable("vast", self._last_why)

    def quotes(self, spec: GpuSpec) -> list[Quote]:
        """The cheapest passing offer for each allowed GPU model, cheapest first. Results are reused for a few seconds."""
        self._last_spec = spec
        now = time.time()
        if now - self._cached_at < self._cfg.cache_seconds and self._cached:
            return self._cached
        fresh = self._search(spec)
        self._cached = fresh
        self._cached_at = 0 if not fresh else now
        return fresh

    def _search(self, spec: GpuSpec) -> list[Quote]:
        c = self._cfg
        body = {
            "verified": {"eq": True}, "rentable": {"eq": True}, "rented": {"eq": False}, "num_gpus": {"eq": 1},
            "gpu_ram": {"gte": spec.min_vram_gb * 1000},  # MB in the REST API
            "cpu_ram": {"gte": spec.min_ram_gb * 1000}, "reliability": {"gte": c.min_reliability},
            "direct_port_count": {"gte": 1}, "disk_space": {"gte": c.disk_gb}, "inet_down": {"gte": float(c.min_down_mbps)},
            "gpu_name": {"in": c.gpu_names}, "type": "on-demand",
            "allocated_storage": c.disk_gb,  # dph_total then includes the disk we will rent
            "order": [["dph_total", "asc"]], "limit": 40,
        }
        resp = self._call(False, lambda: self._http.post(f"{self._base}/bundles/", headers={"Authorization": f"Bearer {self._search_key()}",
                                                                                          "Content-Type": "application/json"},
                                                         content=json.dumps(body, separators=(",", ":"))))
        resp.raise_for_status()
        r = resp.json()
        out: list[Quote] = []
        why: dict[str, int] = {}
        if isinstance(r, dict) and isinstance(r.get("offers"), list):
            cheapest: dict[str, Quote] = {}
            for offer in r["offers"]:
                if not isinstance(offer, dict):
                    continue
                reject = self._offer_problem(offer, spec)
                if reject is not None:
                    why[reject] = why.get(reject, 0) + 1
                    continue
                q = self._quote_of(offer)
                name = str(offer.get("gpu_name"))
                if name not in cheapest or q.hourly_usd < cheapest[name].hourly_usd:
                    cheapest[name] = q
            out.extend(cheapest.values())
        out.sort(key=lambda q: q.hourly_usd)
        self._last_why = ("" if out else "no on-demand offer in stock that fits the filter" if not why
                          else f"every offer was rejected: {[f'{k} x{v}' for k, v in why.items()]}")
        return out

    def _offer_problem(self, o: dict[str, Any], spec: GpuSpec) -> str | None:
        """Why this offer must not be rented, or None. Also checked here (not only in the query) so a server that ignores a filter
        cannot slip an offer through."""
        c = self._cfg
        if str(o.get("verification")) != "verified":
            return "not a verified host"
        if o.get("is_bid") is True or o.get("rented") is True or o.get("rentable") is False:
            return "not an on-demand offer you can rent"
        if _num(o.get("num_gpus")) != 1:
            return "not exactly 1 GPU"
        if str(o.get("gpu_name")) not in c.gpu_names:
            return "GPU model not allowed"
        if _num(o.get("gpu_ram")) < spec.min_vram_gb * 1000.0:
            return "too little VRAM"
        if _num(o.get("cpu_ram")) < spec.min_ram_gb * 1000.0:
            return "too little RAM"
        if _num(o.get("reliability")) < c.min_reliability:
            return "host reliability too low"
        if _num(o.get("disk_space")) < c.disk_gb:
            return "not enough free disk"
        if _num(o.get("inet_down")) < c.min_down_mbps:
            return "download speed too low"
        if _num(o.get("direct_port_count")) < 1 or not _str(o.get("public_ipaddr")).strip():
            return "no direct public port"
        if not isinstance(o.get("dph_total"), (int, float)) or _num(o.get("dph_total")) <= 0:
            return "no live price"
        if self.bandwidth_usd(o) > c.max_bandwidth_usd:
            return "host internet price too high"
        return None

    def bandwidth_usd(self, o: dict[str, Any]) -> float:
        """What the host charges for the data this rental moves (Vast bills internet per GB, set by each host; some charge a lot)."""
        return _num(o.get("inet_down_cost")) * self._cfg.download_gb + _num(o.get("inet_up_cost")) * self._cfg.upload_gb

    def _quote_of(self, o: dict[str, Any]) -> Quote:
        hourly = _num(o.get("dph_total")) + self.bandwidth_usd(o) / self._cfg.amortize_hours
        gpu, country, machine = str(o.get("gpu_name")), _str(o.get("geolocation")), str(_long(o.get("machine_id")))
        # "<gpu>|<offer id>|<country>|m<machine>": the broker's key and RoutingDecision rows show where the GPU is (strangers' machines)
        key = f"{gpu}|{_long(o.get('id'))}|{country.replace('|', '/')}|m{machine}"
        detail = (f"offer {_long(o.get('id'))}, host {_long(o.get('host_id'))} machine {machine}, {country}, "
                  f"reliability {fixed(_num(o.get('reliability')), 3)}, {fixed(_num(o.get('inet_down')), 0)} Mb/s down, "
                  f"GPU+disk ${fixed(_num(o.get('dph_total')), 3)}/h + ~${fixed(self.bandwidth_usd(o), 2)} internet "
                  f"({fixed(self._cfg.download_gb, 0)} GB in, {fixed(self._cfg.upload_gb, 0)} GB out)")
        return Quote("vast", True, "ON-DEMAND", key, hourly, detail)

    # ------------------------------------------------------------------------------------------------ create

    def create(self, req: CreateRequest) -> Instance:
        if not req.name.startswith(LABEL_PREFIX):
            raise ValueError("instance label must carry the studio prefix so the watchdog can see it")
        g = req.quote.gpu.split("|")
        if len(g) < 2:
            raise ValueError(f"not a Vast quote: {req.quote.gpu}")
        ask = int(g[1])
        self._worker_port = req.port
        ceiling = req.quote.hourly_usd * self._cfg.price_slack
        tried: list[int] = []
        last: Exception | None = None
        for _attempt in range(self._cfg.offer_attempts):
            tried.append(ask)
            try:
                body = self.create_body(req)
                resp = self._call(True, lambda a=ask, b=body: self._http.put(
                    f"{self._base}/asks/{a}/", headers={"Authorization": f"Bearer {self._account_key()}",
                                                         "Content-Type": "application/json"}, content=json.dumps(b, separators=(",", ":"))))
                if resp.status_code in (404, 410):
                    # the offer was taken or withdrawn between search and create. Nothing was created.
                    last = httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                else:
                    resp.raise_for_status()
                    r = resp.json()
                    n = r.get("new_contract") if isinstance(r, dict) else None
                    if not isinstance(n, (int, float)) or isinstance(n, bool):
                        raise RuntimeError("Vast create returned no new_contract: "
                                           + ("empty reply" if not isinstance(r, dict) else _str(r.get("msg"))))
                    id_ = str(int(n))
                    self._first_seen[id_] = datetime.now(UTC)
                    endpoint = self._wait_for_endpoint(id_, req.port)
                    return Instance("vast", id_, req.name, req.quote.hourly_usd, g[0], endpoint)
            except StartFailed as e:
                if not e.destroyed:
                    raise  # never rent a second machine while this one could not be destroyed
                last = e  # it never came up and was destroyed: try the next offer
            nxt = self._next_offer(tried, ceiling)
            if nxt is None:
                break
            ask = int(nxt.gpu.split("|")[1])
            log.warning("Vast: offer %s failed (%s); trying offer %s", tried[-1], last, ask)
        raise last if last is not None else RuntimeError("Vast create failed")

    def _next_offer(self, tried: list[int], ceiling: float) -> Quote | None:
        spec = self._last_spec
        if spec is None:
            return None
        try:
            self._cached_at = 0
            return next((q for q in self._search(spec) if q.hourly_usd <= ceiling and int(q.gpu.split("|")[1]) not in tried), None)
        except Exception:  # noqa: BLE001
            return None

    def _wait_for_endpoint(self, id_: str, port: int) -> str:
        """Polls the new instance until it runs with its port mapped; destroys it when it exits or takes too long."""
        deadline = time.time() + self._cfg.start_timeout_seconds
        state = "no status yet"
        while True:
            inst = None
            try:
                inst = self._fetch(id_)
            except Exception as e:  # noqa: BLE001
                state = f"status unreadable: {e}"
            if inst is not None:
                state = str(inst.get("actual_status"))
                ep = endpoint_of(inst, port)
                if state == "running" and ep is not None:
                    return ep
                if _is_dead(state):
                    raise self._failed_start(id_, f"instance state '{state}' (it will never run)")
            if time.time() >= deadline:
                raise self._failed_start(id_, f"not running with its port mapped after {self._cfg.start_timeout_seconds} s (last state: {state})")
            time.sleep(self._poll)

    def _failed_start(self, id_: str, why: str) -> StartFailed:
        try:
            self.terminate(id_)
            return StartFailed(f"Vast instance {id_} destroyed: {why}", True)
        except Exception as e:  # noqa: BLE001
            # The pod list adoption in GpuBroker and the watchdog (label prefix) cover an instance we could not destroy now.
            return StartFailed(f"Vast instance {id_} {why} and destroying it failed ({e}); the watchdog will retry", False)

    def create_body(self, req: CreateRequest) -> dict[str, Any]:
        """The create body. No SSH, no key, no provider secret: env carries only what the worker needs."""
        env = ""
        for k, v in (req.env or {}).items():
            if not safe_env(k, v):
                raise ValueError(f"env {k} has characters that cannot go into Vast's env string")
            env += f"-e {k}={v} "
        env += f"-p {req.port}:{req.port}"
        return {
            "image": req.image, "label": req.name, "disk": max(req.disk_gb, self._cfg.disk_gb),
            "runtype": "args",  # entrypoint mode: no SSH, no Jupyter, nothing else listens
            "args": req.boot_argv, "env": env,
            "cancel_unavail": True,  # never queue behind a taken offer
        }

    # ------------------------------------------------------------------------------------------------ status / list / destroy

    def status(self, instance_id: str) -> InstanceStatus:
        try:
            inst = self._fetch(instance_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return InstanceStatus(State.GONE, None, "404")
            raise
        if not inst:
            return InstanceStatus(State.GONE, None, "not found")
        st = str(inst.get("actual_status"))
        if st == "running":
            return InstanceStatus(State.RUNNING, endpoint_of(inst, self._worker_port), st)
        if _is_dead(st):
            return InstanceStatus(State.GONE, None, st)  # exited / unknown / offline never reach running: destroy and replace
        born = self._born(instance_id, inst)
        if datetime.now(UTC) > born + timedelta(seconds=self._cfg.start_timeout_seconds * 3):
            return InstanceStatus(State.GONE, None, f"never became running ({st})")
        return InstanceStatus(State.STARTING, None, st)

    def endpoint_for(self, instance_id: str, port: int) -> str | None:
        try:
            inst = self._fetch(instance_id)
            return None if inst is None else endpoint_of(inst, port)
        except Exception as e:  # noqa: BLE001 - a broker restart must not fail because Vast is slow
            log.warning("Vast: could not read the address of instance %s: %s", instance_id, e)
            return None

    def list_pods(self) -> list[Pod]:
        """All instances of the account (the watchdog filters by the studio label). GET /api/v1/instances/, keyset-paged."""
        out: list[Pod] = []
        after: str | None = None
        for _page in range(40):  # 25 per page: 1000 instances is far beyond any sane account
            params: dict[str, Any] = {"limit": 25}
            if after:
                params["after_token"] = after
            resp = self._get(f"{self._v1}/instances/", True, params)
            resp.raise_for_status()
            r = resp.json()
            if not isinstance(r, dict) or not isinstance(r.get("instances"), list):
                raise RuntimeError("Vast list instances returned no instances array")
            for inst in r["instances"]:
                if isinstance(inst, dict):
                    id_ = str(_long(inst.get("id")))
                    born = self._born(id_, inst)
                    status = _str(inst.get("actual_status")).upper() or "LOADING"
                    out.append(Pod(id_, _str(inst.get("label")), status, _num(inst.get("dph_total")), born, born))
            after = None if r.get("next_token") is None else str(r["next_token"])
            if not after or not after.strip() or not r["instances"]:
                return out
        raise RuntimeError("Vast list instances did not end after 40 pages")

    def terminate(self, pod_id: str) -> None:
        resp = self._call(True, lambda: self._http.delete(f"{self._base}/instances/{pod_id}/",
                                                           headers={"Authorization": f"Bearer {self._account_key()}"}))
        if resp.status_code != 404:  # 404: already gone, nothing more to destroy
            resp.raise_for_status()
            r = resp.json() if resp.content else None
            if isinstance(r, dict) and r.get("success") is False:
                raise RuntimeError(f"Vast destroy {pod_id} refused: {r.get('msg')}")
        self._first_seen.pop(pod_id, None)

    # ------------------------------------------------------------------------------------------------ helpers

    def _fetch(self, id_: str) -> dict[str, Any] | None:
        """One instance by id through the v1 list (select_filters on id): v1 has no by-id route (404) and the v0 list is gone (410)."""
        f = json.dumps({"id": {"eq": int(id_)}}, separators=(",", ":"))
        resp = self._get(f"{self._v1}/instances/", True, {"limit": 5, "select_filters": f})
        resp.raise_for_status()
        r = resp.json()
        if not isinstance(r, dict) or not isinstance(r.get("instances"), list):
            return None
        for m in r["instances"]:
            if isinstance(m, dict) and id_ == str(_long(m.get("id"))):
                return m
        return None

    def _born(self, id_: str, inst: dict[str, Any]) -> datetime:
        sd = inst.get("start_date")
        if isinstance(sd, (int, float)) and not isinstance(sd, bool) and sd > 0:
            return datetime.fromtimestamp(int(sd * 1000) / 1000.0, UTC)
        return self._first_seen.setdefault(id_, datetime.now(UTC))
