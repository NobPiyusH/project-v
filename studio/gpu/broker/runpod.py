"""RunPod adapter on REST v2. Port of RunPodProvider and ProviderHttp.

Listing and terminating reuse RunPodApi (the watchdog's client). Stock and price come from
GET /catalog/gpus/{id}?include=AVAILABILITY&product=POD&cloud=SECURE|COMMUNITY (availability HIGH/MEDIUM/LOW/NONE,
price.secure/community USD per hour). Create uses the v2 body of docs/wan-fp8-4090-runbook.md. NOT exercised against the live API
in this port (no spend allowed): the first paid test must confirm create/status. Community cloud and Secure are both quoted; the
broker's wallet decides whether the higher price fits.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote as urlquote

import httpx

from studio.gpu.broker.provider import CreateRequest, GpuProvider, GpuSpec, Instance, InstanceStatus, Quote, State
from studio.gpu.pods import Pod, RunPodApi

SAFE_ENV = re.compile(r"[A-Za-z0-9+/=._:@-]*")  # characters allowed in a pod env key or value passed through a provider's start script
USER_AGENT = "projectv-studio/1.0"


def safe_env(key: str, value: str) -> bool:
    """True when both the key (non-empty) and the value are safe to put in a start script."""
    return bool(key) and SAFE_ENV.fullmatch(key) is not None and SAFE_ENV.fullmatch(value) is not None


LABEL_PREFIX = "projectv-"  # every studio pod/instance/node name starts with this: the watchdog only touches such names


def shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def abbreviate(s: str | None) -> str:
    """At most 200 characters of a provider's answer, for logs and errors ('' for None)."""
    return "" if s is None else s[:200]


def http_client(read_timeout_seconds: float, user_agent: str | None = None) -> httpx.Client:
    """One client recipe: 10 s connect timeout, the given read timeout and, when set, a User-Agent header."""
    return httpx.Client(timeout=httpx.Timeout(read_timeout_seconds, connect=10), headers={"User-Agent": user_agent} if user_agent else None)


def proxy_url(pod_id: str, port: int) -> str:
    """RunPod's HTTP proxy address for an exposed port."""
    return f"https://{pod_id}-{port}.proxy.runpod.net"


def create_body(req: CreateRequest) -> dict[str, Any]:
    """The create body. quote.gpu is '<gpu id>|<COMMUNITY|SECURE>'."""
    g = req.quote.gpu.split("|")
    body: dict[str, Any] = {
        "name": req.name,
        "cloud": g[1] if len(g) > 1 else "SECURE",
        "gpu": {"id": g[0], "count": 1, "minCudaVersion": "12.8"},
        "image": req.image,
        "disk": req.disk_gb,
        "cmd": req.boot_argv,
    }
    if req.env:
        body["env"] = req.env
    body["ports"] = [f"{req.port}/http"]
    if req.volume_id and req.volume_id.strip():
        # REST v2 rejects unknown top-level fields; a global volume goes in mounts.global (one entry, optional path).
        path = (req.env or {}).get("SV_VOLUME") or "/workspace"
        body["mounts"] = {"global": [{"volumeId": req.volume_id, "path": path}]}
    return body


class RunPodProvider(GpuProvider):
    def __init__(self, base: RunPodApi, base_url: str, api_key: str, client: httpx.Client | None = None,
                 clouds: tuple[str, ...] = ("COMMUNITY", "SECURE")):
        self._base = base
        self._clouds = tuple(c.upper() for c in clouds)  # ("SECURE",) = never quote a Community offer (POD_CLOUD_PREFERENCE=secure_only)
        self._url = base_url.rstrip("/")
        self._key = (api_key or "").strip()
        self._http = client or http_client(30)
        self._last_why = "no GPU type in stock"

    def provider(self) -> str:
        return "runpod"

    def configured(self) -> bool:
        return bool(self._key)

    def enabled_by_default(self) -> bool:
        return True

    def list_pods(self) -> list[Pod]:
        return self._base.list_pods()

    def terminate(self, pod_id: str) -> None:
        self._base.terminate(pod_id)

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}

    def quote(self, spec: GpuSpec) -> Quote:
        all_quotes = self.quotes(spec)
        return all_quotes[0] if all_quotes else Quote.unavailable("runpod", self._last_why)

    def quotes(self, spec: GpuSpec) -> list[Quote]:
        """Every GPU in stock, Community and Secure, cheapest first."""
        out: list[Quote] = []
        why: list[str] = []
        for cloud in self._clouds:
            for gpu in spec.gpu_preference:
                try:
                    resp = self._http.get(f"{self._url}/catalog/gpus/{urlquote(gpu, safe='')}?include=AVAILABILITY&product=POD&cloud={cloud}",
                                          headers=self._auth())
                    resp.raise_for_status()
                    r = resp.json()
                except Exception as e:  # noqa: BLE001
                    why.append(f"{gpu}/{cloud}: {e}; ")
                    continue
                if not isinstance(r, dict):
                    continue
                stock = str(r.get("availability", "NONE")).upper()
                mem = r.get("memory")
                if isinstance(mem, (int, float)) and not isinstance(mem, bool) and int(mem) < spec.min_vram_gb:
                    continue
                price = r.get("price") if isinstance(r.get("price"), dict) else {}
                p = price.get(cloud.lower())
                if not isinstance(p, (int, float)) or isinstance(p, bool) or stock in ("NONE", "OUT", "UNAVAILABLE"):
                    why.append(f"{gpu}/{cloud}: {stock}; ")
                    continue
                out.append(Quote("runpod", True, stock, f"{gpu}|{cloud}", float(p), ""))
        out.sort(key=lambda q: q.hourly_usd)
        self._last_why = "".join(why) or "no GPU type in stock"
        return out

    def create(self, req: CreateRequest) -> Instance:
        if not req.name.startswith("projectv-"):
            raise ValueError("pod name must carry the studio prefix so the watchdog can see it")
        resp = self._http.post(f"{self._url}/pods", headers=self._auth(), json=create_body(req))
        if resp.status_code >= 400:
            # keep the exception type, but say WHY RunPod refused (the plain "400 Bad Request" hid it on 25 Nov 2026)
            body = " ".join((resp.text or "").split())[:300]
            raise httpx.HTTPStatusError(f"RunPod refused the pod ({resp.status_code}): {body or 'no reason given'}", request=resp.request, response=resp)
        r = resp.json()
        if not isinstance(r, dict) or r.get("id") is None:
            raise RuntimeError("RunPod create pod returned no id")
        pod_id = str(r["id"])
        cost = r.get("cost")
        hourly = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else req.quote.hourly_usd
        return Instance("runpod", pod_id, req.name, hourly, req.quote.gpu.split("|")[0], proxy_url(pod_id, req.port))

    def status(self, instance_id: str) -> InstanceStatus:
        resp = self._http.get(f"{self._url}/pods/{urlquote(instance_id, safe='')}", headers=self._auth())
        if resp.status_code == 404:
            return InstanceStatus(State.GONE, None, "404")
        resp.raise_for_status()
        r = resp.json()
        st = str(r.get("status", "")) if isinstance(r, dict) else ""
        up = st.upper()
        state = State.RUNNING if up == "RUNNING" else State.GONE if up in ("TERMINATED", "EXITED", "") else State.STARTING
        return InstanceStatus(state, None, st)

    def endpoint_for(self, instance_id: str, port: int) -> str | None:
        return proxy_url(instance_id, port)
