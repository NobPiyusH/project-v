"""Port of RunPodProviderTest: a fake REST v2 server (httpx MockTransport), no real pods."""

import json

import httpx
import pytest

from studio.gpu.broker.provider import CreateRequest, GpuSpec, Quote, State
from studio.gpu.broker.runpod import RunPodProvider, abbreviate, safe_env
from studio.gpu.pods import RunPodApi

SPEC = GpuSpec(["NVIDIA GeForce RTX 4090"], 24, 48, 80)


class Fake:
    def __init__(self):
        self.requests, self.community_stock, self.created_body = [], "NONE", ""

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(f"{request.method} {request.url}")
        path = request.url.path
        if "/catalog/gpus" in path:
            secure = "cloud=SECURE" in str(request.url)
            return httpx.Response(200, json={"availability": "HIGH" if secure else self.community_stock, "memory": 24,
                                             "price": {"community": 0.34, "secure": 0.89}})
        if request.method == "POST":
            self.created_body = request.content.decode()
            return httpx.Response(201, json={"id": "pod123", "cost": 0.89})
        if path.endswith("/gone"):
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"id": "pod123", "status": "RUNNING"})

    def provider(self, key="k", **kw):
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return RunPodProvider(RunPodApi("http://fake/v2", key, client=client), "http://fake/v2", key, client=client, **kw)


def test_unconfigured_without_key():
    assert not Fake().provider("").configured() and Fake().provider("k").configured()


def test_community_preferred_when_in_stock():
    f = Fake()
    f.community_stock = "LOW"
    q = f.provider().quote(SPEC)
    assert q.available and q.hourly_usd == 0.34 and q.gpu.endswith("|COMMUNITY")


def test_secure_only_never_quotes_or_asks_for_community():
    f = Fake()
    f.community_stock = "HIGH"
    quotes = f.provider(clouds=("SECURE",)).quotes(SPEC)
    assert [q.gpu for q in quotes] == ["NVIDIA GeForce RTX 4090|SECURE"] and quotes[0].hourly_usd == 0.89
    assert not any("cloud=COMMUNITY" in r for r in f.requests)


def test_falls_back_to_secure_when_community_has_no_instances():
    f = Fake()
    q = f.provider().quote(SPEC)
    assert q.available and q.hourly_usd == 0.89 and q.gpu.endswith("|SECURE")
    assert not any(r.startswith(("POST", "DELETE")) for r in f.requests), "a quote never creates"


def test_both_clouds_are_listed_cheapest_first_and_no_stock_says_why():
    f = Fake()
    f.community_stock = "MEDIUM"
    assert [q.gpu for q in f.provider().quotes(SPEC)] == ["NVIDIA GeForce RTX 4090|COMMUNITY", "NVIDIA GeForce RTX 4090|SECURE"]
    f.community_stock = "NONE"
    small = GpuSpec(["NVIDIA GeForce RTX 4090"], 48, 48, 80)  # needs more memory than the 24 GB offered: nothing qualifies
    p = f.provider()
    assert p.quotes(small) == [] and p.quote(small).detail == "no GPU type in stock"


def test_create_sends_prefixed_name_wrapped_cmd_and_returns_proxy_endpoint():
    f = Fake()
    p = f.provider()
    q = Quote("runpod", True, "HIGH", "NVIDIA GeForce RTX 4090|SECURE", 0.89, "")
    i = p.create(CreateRequest("projectv-runpod-abc", "img", 80, ["bash", "-c", "guard"], {"K": "V"}, 8000, "vol1", q))
    assert i.id == "pod123" and i.endpoint == "https://pod123-8000.proxy.runpod.net" and i.hourly_usd == 0.89
    body = json.loads(f.created_body)
    assert body["name"] == "projectv-runpod-abc" and body["cloud"] == "SECURE" and body["cmd"] == ["bash", "-c", "guard"]
    assert body["mounts"] == {"global": [{"volumeId": "vol1", "path": "/workspace"}]} and "volumeId" not in body
    assert body["gpu"] == {"id": "NVIDIA GeForce RTX 4090", "count": 1, "minCudaVersion": "12.8"} and body["ports"] == ["8000/http"]
    with pytest.raises(ValueError):
        p.create(CreateRequest("other-pod", "img", 80, ["x"], {}, 8000, None, q))


def test_status_maps_running_and_gone():
    p = Fake().provider()
    assert p.status("pod123").state == State.RUNNING and p.status("gone").state == State.GONE


def test_helpers():
    assert safe_env("SV_PORT", "8000") and not safe_env("", "x") and not safe_env("K", "a b") and not safe_env("K", "a;b")
    assert abbreviate(None) == "" and len(abbreviate("x" * 500)) == 200
