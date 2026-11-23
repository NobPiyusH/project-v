"""Port of JarvislabsProviderTest: a fake helper (no SDK, no network, no instance is ever created)."""

import json

import pytest

from studio.gpu.broker.jarvislabs import JarvislabsConfig, JarvislabsProvider
from studio.gpu.broker.provider import CreateRequest, GpuSpec, State
from studio.gpu.broker.runpod import shell_quote

KEY = "jl-key-must-never-reach-an-instance"
SPEC = GpuSpec(["x"], 24, 48, 80)


class Helper:
    def __init__(self):
        self.calls, self.stdins = [], []
        self.gpus = json.dumps([
            {"gpu_type": "A100-80GB", "region": "IN2", "free": 3, "price_per_hour": 1.49, "workload_type": "container"},
            {"gpu_type": "A100", "region": "IN2", "free": 2, "price_per_hour": 0.89, "workload_type": "container"},
            {"gpu_type": "L4", "region": "IN2", "free": 5, "price_per_hour": 0.44, "workload_type": "container"},
            {"gpu_type": "RTX-PRO6000", "region": "IN1", "free": 0, "price_per_hour": 2.19, "workload_type": "container"}])
        self.balance = '{"balance":500.0,"grants":0.0,"currency":"INR"}'
        self.instance = ('{"machine_id":777,"name":"projectv-jarvislabs-aaaa","status":"Running","cost":0.89,'
                         '"endpoints":["abc123.jarvislabs.net"]}')

    def __call__(self, args, stdin):
        self.calls.append(" ".join(args))
        self.stdins.append(stdin)
        return {"gpus": self.gpus, "balance": self.balance, "create": self.instance, "get": self.instance,
                "list": f"[{self.instance}]", "destroy": '{"destroyed":true}'}.get(args[0], '{"error":"?","type":"ValueError"}')

    def provider(self, key=KEY):
        return JarvislabsProvider(JarvislabsConfig(), key, self)


def req(p):
    return CreateRequest("projectv-jarvislabs-aaaa", "img", 80, ["python", "-c", "run it's"],
                         {"SV_BOOT_00": "QUJD", "SV_BOOT_N": "1", "SV_PORT": "8000", "SV_TOKEN": "tok"}, 8000, None, p.quote(SPEC))


def test_not_configured_without_a_key():
    assert not Helper().provider("").configured() and Helper().provider().configured()


def test_quotes_are_the_allowed_gpus_in_stock_cheapest_first():
    q = Helper().provider().quotes(SPEC)
    assert len(q) == 2, "L4 is not on the allow list, RTX-PRO6000 has none free"
    assert q[0].gpu == "A100|IN2" and q[0].hourly_usd == 0.89


def test_no_stock_is_an_unavailable_quote_with_a_reason():
    h = Helper()
    h.gpus = "[]"
    q = h.provider().quote(SPEC)
    assert not q.available and q.detail == "no allowed GPU in the JarvisLabs list"


def test_create_sends_the_name_gpu_and_a_script_without_the_key():
    h = Helper()
    p = h.provider()
    i = p.create(req(p))
    assert i.id == "777" and i.endpoint == "https://abc123.jarvislabs.net"
    body = h.stdins[-1]
    assert '"gpu_type": "A100"' in body and '"region": "IN2"' in body and "projectv-jarvislabs-aaaa" in body
    assert KEY not in body, "the account key never goes into the request, let alone the script"
    script = p.start_script(req(p))
    assert "export SV_PORT=6006" in script and "SV_PORT=8000" not in script, "the worker listens on the published port"
    assert "export SV_TOKEN=tok" in script and "'run it'\\''s'" in script, "token set, args shell-quoted"


def test_shell_quote():
    assert shell_quote("run it's") == "'run it'\\''s'"


def test_create_refuses_names_without_the_studio_prefix_and_unsafe_env():
    p = Helper().provider()
    q = p.quote(SPEC)
    with pytest.raises(ValueError):
        p.create(CreateRequest("other", "i", 80, ["x"], {}, 8000, None, q))
    with pytest.raises(ValueError):
        p.start_script(CreateRequest("projectv-x", "i", 80, ["x"], {"A": "b; rm -rf /"}, 8000, None, q))


def test_status_maps_running_missing_and_paused():
    h = Helper()
    p = h.provider()
    assert p.status("777").state == State.RUNNING
    h.instance = h.instance.replace("Running", "Paused")
    assert p.status("777").state == State.GONE, "paused still bills storage: treated as gone so it is destroyed"
    h.instance = h.instance.replace("Paused", "Creating")
    assert p.status("777").state == State.STARTING
    missing = JarvislabsProvider(JarvislabsConfig(), KEY, lambda a, s: '{"error":"nope","type":"NotFoundError"}')
    assert missing.status("1").state == State.GONE


def test_list_and_terminate_use_the_helper():
    h = Helper()
    p = h.provider()
    assert p.list_pods()[0].name == "projectv-jarvislabs-aaaa"
    p.terminate("777")
    assert h.calls[-1] == "destroy 777"


def test_terminate_of_an_already_gone_instance_is_fine():
    JarvislabsProvider(JarvislabsConfig(), KEY, lambda a, s: '{"error":"gone","type":"NotFoundError"}').terminate("5")


def test_helper_errors_are_scrubbed_of_the_key():
    p = JarvislabsProvider(JarvislabsConfig(), KEY, lambda a, s: json.dumps({"error": "bad " + KEY, "type": "AuthError"}))
    with pytest.raises(RuntimeError) as e:
        p.list_pods()
    assert KEY not in str(e.value) and "***" in str(e.value)

    def boom(a, s):
        raise RuntimeError("crashed with " + KEY)

    with pytest.raises(RuntimeError) as e2:
        JarvislabsProvider(JarvislabsConfig(), KEY, boom).list_pods()
    assert KEY not in str(e2.value)


def test_preflight_checks_script_size_and_the_gpu_list():
    h = Helper()
    p = h.provider()
    r = CreateRequest("projectv-jarvislabs-preflight", "img", 80, ["python"], {"SV_BOOT_00": "A" * 800}, 8000, None, None)
    assert p.preflight(SPEC, r).ok
    big = CreateRequest("projectv-jarvislabs-preflight", "img", 80, ["python"], {"SV_BOOT_00": "A" * 70_000}, 8000, None, None)
    assert not p.preflight(SPEC, big).ok
    h.gpus = "[]"
    assert not h.provider().preflight(SPEC, r).ok
    assert not h.provider("").preflight(SPEC, r).ok


def test_inr_prices_are_converted_to_usd_and_a_low_balance_fails_the_preflight():
    h = Helper()
    h.gpus = json.dumps([{"gpu_type": "A100", "region": "IN2", "free": 2, "price_per_hour": 84.24, "workload_type": "container", "currency": "INR"}])
    p = h.provider()
    assert p.quote(SPEC).hourly_usd == pytest.approx(84.24 / 94)
    r = CreateRequest("projectv-jarvislabs-preflight", "img", 80, ["python"], {"SV_BOOT_00": "A"}, 8000, None, None)
    assert p.preflight(SPEC, r).ok and "balance" in h.calls
    h.balance = '{"balance":5.0,"grants":0.0,"currency":"INR"}'
    assert not h.provider().preflight(SPEC, r).ok


def test_config_reads_the_yaml_and_defaults():
    import yaml

    from studio.gpu.broker.config import BrokerConfig  # noqa: F401  (same yaml section family)
    from studio.tests.test_pricing import TEST_CONFIG

    cfg = JarvislabsConfig.from_yaml(yaml.safe_load((TEST_CONFIG / "providers.yaml").read_text(encoding="utf-8")))
    assert cfg.service_port == 6006 and cfg.inr_per_usd == 94 and [g.type for g in cfg.gpus][0] == "A100"
    assert JarvislabsConfig.from_yaml({}).template == "pytorch" and len(JarvislabsConfig.from_yaml({}).gpus) == 3
