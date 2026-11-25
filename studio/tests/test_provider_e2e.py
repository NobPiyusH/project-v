"""Port of E2eProviderTest: a fake E2E REST server (httpx MockTransport). No real nodes."""

import base64
import json
import re
from datetime import UTC, datetime

import httpx
import pytest

from studio.gpu import self_stop
from studio.gpu.broker.e2e import E2eConfig, E2eProvider, sha256, throwaway_key
from studio.gpu.broker.provider import CreateRequest, GpuSpec, PlanUnavailableError, State

KEY = "e2e-api-key-must-never-reach-a-node"
TOKEN = "e2e-bearer-must-never-reach-a-node"
SPEC = GpuSpec(["x"], 24, 48, 80)


def row(sku, plan, cpu, ram, disk, inr, image="Ubuntu-22.04-GPU"):
    return {"name": sku, "plan": plan, "image": image, "specs": {"cpu": cpu, "ram": f"{ram}.00", "disk_space": disk, "price_per_hour": inr}}


class Fake:
    def __init__(self):
        self.requests, self.queries, self.create_body = [], [], ""
        self.node_status, self.node_ip, self.delete_status = "Running", "9.8.7.6", 200
        self.create_error = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, m = request.url.path, request.method
        self.requests.append(f"{m} {path}")
        self.queries.append(request.url.query.decode())
        if path.endswith("/images/"):
            return httpx.Response(200, json={"code": 200, "data": [
                row("GDC3.L40S-60.220GB_v1", "p-l40s", 60, 220, 250, 102), row("GDC3.L4-25.110GB_v1", "p-l4", 25, 110, 250, 49),
                row("GDC3.A40-16.100GB", "GDC3-16vCPU-100RAM-750DISK-GDC3.A40-16.100GB_Ubuntu20-Delhi", 16, 100, 750, 96, "Ubuntu-20.04-GPU"),
                row("GDC3.A40-16.100GB", "GDC3-16vCPU-100RAM-750DISK-GDC3.2xA40-32.200GB_Ubuntu22-Delhi", 16, 100, 750, 90, "Ubuntu-22.04-GPU")]})
        if "/nodes/" in path:
            if m == "POST":
                if self.create_error:
                    return httpx.Response(self.create_error[0], json=self.create_error[1])
                self.create_body = request.content.decode()
                return httpx.Response(200, json={"code": 200, "data": {"id": 555, "name": "n"}})
            if m == "DELETE":
                return httpx.Response(self.delete_status, json={"code": self.delete_status, "message": "x"})
            if path.endswith("/nodes/"):
                return httpx.Response(200, json={"code": 200, "data": [{"id": 555, "name": "projectv-e2e-aaaa", "status": "Running",
                                                                        "price": "Rs. 102.0/Hour", "public_ip_address": "9.8.7.6",
                                                                        "created_at": "2026-11-22T01:00:00Z"}]})
            if "/nodes/999/" in path:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"code": 200, "data": {"id": 555, "status": self.node_status, "public_ip_address": self.node_ip}})
        return httpx.Response(404)

    def provider(self, start_timeout=2, key=KEY, token=TOKEN):
        c = E2eConfig(start_timeout_seconds=start_timeout, cache_seconds=0)
        return E2eProvider(c, "http://fake/api/v1", key, token, "", httpx.Client(transport=httpx.MockTransport(self.handler)), 5)


def req(q):
    return CreateRequest("projectv-e2e-aaaa", "img/worker:1", 80, ["python", "-c", "print('hi')"], {"SV_BOOT_00": "QUJD", "SV_TOKEN": "tok123"}, 8000, None, q)


def decoded_run_script(scripts):
    """Reassembles run.b64 from the entries the way the node does."""
    b64 = ""
    for sc in scripts:
        if ">> /opt/projectv/run.b64" in sc:
            b64 += sc[sc.index("printf '%s' '") + 13 : sc.index("' >> /opt/projectv/run.b64")]
    return base64.b64decode(b64).decode()


def test_quotes_allowed_plans_cheapest_first_in_dollars_with_gst():
    f = Fake()
    qs = f.provider().quotes(SPEC)
    assert len(qs) == 2, "L4 has too little VRAM and is not in the allowed plans"
    assert qs[0].gpu.split("|")[0] == "A40" and qs[0].hourly_usd == pytest.approx(96 * 1.18 / 94)
    assert qs[1].gpu.split("|")[0] == "L40S" and "apikey=" in f.queries[0]
    assert "stock unknown until create" in qs[0].detail


def test_create_body_has_no_secrets_and_no_login():
    f = Fake()
    p = f.provider()
    q = p.quote(SPEC)
    p.create(req(q))
    b = f.create_body
    assert KEY not in b and TOKEN not in b and '"disable_password":true' in b.replace(" ", "") and "ssh-ed25519 " in b
    assert "SV_TOKEN=tok123" in "".join(json.loads(b)["start_scripts"]), "the per-rental worker token is the only secret on the node"
    assert json.loads(b)["image"] == "Ubuntu-20.04-GPU", "the A40 uses its single-GPU template image"
    run = decoded_run_script(p.start_scripts(req(q)))
    assert "--gpus all" in run and "-p 443:8000" in run and "'img/worker:1'" in run and "'print('\\''hi'\\'')'" in run


def test_every_start_script_entry_stays_under_the_e2e_limit_of_2048_chars():
    f = Fake()
    p = f.provider()
    env = {f"SV_BOOT_{i:02d}": "A" * 900 for i in range(23)}  # the real worker payload: ~23 chunks of 900 chars
    env["SV_TOKEN"] = "t" * 64
    guard = self_stop.wrap_exit(["python", "-c", "print('x')"], 120, 15, datetime.now(UTC))
    scripts = p.start_scripts(CreateRequest("projectv-e2e-aaaa", "ghcr.io/x/worker:latest", 80, guard, env, 8000, None, p.quote(SPEC)))
    assert len(scripts) > 10 and all(len(s) <= 2048 for s in scripts)
    last = scripts[-1]
    assert "sha256sum -c" in last and sha256(decoded_run_script(scripts).encode()) in last


def test_the_node_side_script_actually_verifies_and_rebuilds_the_payload(tmp_path, bash):
    """Run the entries in order the way cloud-init would, with /opt/projectv redirected to a temp dir, and check the files."""
    import subprocess

    p = Fake().provider()
    scripts = p.start_scripts(req(p.quote(SPEC)))
    work = tmp_path / "opt"
    for sc in scripts[:-1]:
        subprocess.run([bash, "-c", sc.replace("/opt/projectv", str(work).replace("\\", "/"))], check=True)
    check = scripts[-1].replace("cd /opt/projectv", f"cd {work!s}".replace("\\", "/")).replace("then bash run.sh;", "then echo VERIFIED;")
    out = subprocess.run([bash, "-c", check], capture_output=True, text=True)
    assert out.returncode == 0 and "VERIFIED" in out.stdout, out.stderr
    assert (work / "worker.env").read_text() == "SV_BOOT_00=QUJD\nSV_TOKEN=tok123\n"


def test_bad_node_names_are_refused_before_calling():
    f = Fake()
    p = f.provider()
    with pytest.raises(ValueError):
        p.create(CreateRequest("projectv_e2e_aaaa", "img", 80, ["x"], {}, 8000, None, p.quote(SPEC)))
    with pytest.raises(ValueError):
        p.create(CreateRequest("not-studio", "img", 80, ["x"], {}, 8000, None, p.quote(SPEC)))
    assert not any(r.startswith("POST") for r in f.requests)


def test_multi_gpu_template_names_are_never_quoted():
    assert all("2xA40" not in q.gpu for q in Fake().provider().quotes(SPEC))


def test_large_env_is_split_under_the_script_limit_and_unsafe_env_is_refused():
    p = Fake().provider()
    q = p.quote(SPEC)
    scripts = p.start_scripts(CreateRequest("projectv-e2e-aaaa", "img", 80, ["x"], {f"SV_BOOT_{i:02d}": "A" * 900 for i in range(25)}, 8000, None, q))
    assert len(scripts) >= 3 and all(len(s) <= 2048 for s in scripts)
    with pytest.raises(ValueError):
        p.start_scripts(CreateRequest("projectv-e2e-aaaa", "img", 80, ["x"], {"K": "a\nb"}, 8000, None, q))


def test_create_returns_endpoint_on_public_ip():
    i = Fake().provider().create(req(Fake().provider().quote(SPEC)))
    assert i.id == "555" and i.endpoint == "http://9.8.7.6:443"


def test_never_ready_node_is_deleted():
    f = Fake()
    f.node_status, f.node_ip = "Creating", ""
    p = f.provider(1)
    with pytest.raises(RuntimeError):
        p.create(req(p.quote(SPEC)))
    assert "DELETE /api/v1/nodes/555/" in f.requests


def test_powered_off_counts_as_gone_so_it_gets_deleted_and_missing_is_gone():
    f = Fake()
    f.node_status = "Powered off"
    p = f.provider()
    assert p.status("555").state == State.GONE and p.status("999").state == State.GONE
    f.node_status = "Running"
    assert p.status("555").state == State.RUNNING and p.status("555").endpoint == "http://9.8.7.6:443"
    f.node_ip = ""
    assert p.status("555").state == State.STARTING


def test_list_and_terminate():
    f = Fake()
    p = f.provider()
    pods = p.list_pods()
    assert pods[0].name == "projectv-e2e-aaaa" and pods[0].cost_per_hr_usd == pytest.approx(102 * 1.18 / 94)
    assert pods[0].created_at == datetime(2026, 11, 22, 1, tzinfo=UTC)
    p.terminate("555")
    assert "DELETE /api/v1/nodes/555/" in f.requests
    f.delete_status = 404
    p.terminate("999")  # already gone: fine
    f.delete_status = 500
    with pytest.raises(RuntimeError):
        p.terminate("555")


def test_error_messages_never_carry_the_key():
    def bad(request):
        return httpx.Response(401, json={"message": f"bad {KEY}"})

    p = E2eProvider(E2eConfig(cache_seconds=0), "http://fake/api/v1", KEY, TOKEN, "", httpx.Client(transport=httpx.MockTransport(bad)), 5)
    with pytest.raises(RuntimeError) as e:
        p.quote(SPEC)
    assert KEY not in str(e.value) and "E2E_API_KEY" in str(e.value)

    def boom(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    p2 = E2eProvider(E2eConfig(cache_seconds=0), "http://fake/api/v1", KEY, TOKEN, "", httpx.Client(transport=httpx.MockTransport(boom)), 5)
    with pytest.raises(RuntimeError) as e2:
        p2.quote(SPEC)
    assert KEY not in str(e2.value) and TOKEN not in str(e2.value)


def test_a_plan_out_of_stock_is_the_typed_signal_the_canary_may_fall_through_on():
    f = Fake()
    p = f.provider()
    q = p.quote(SPEC)
    f.create_error = (400, {"message": "This plan is temporarily not available"})
    with pytest.raises(PlanUnavailableError):
        p.create(req(q))
    f.create_error = (402, {"message": "insufficient credit"})
    with pytest.raises(RuntimeError) as e:
        p.create(req(q))
    assert not isinstance(e.value, PlanUnavailableError), "a credit error never falls through to the next plan"


def test_preflight_checks_scripts_and_the_plan_list():
    f = Fake()
    p = f.provider()
    r = p.preflight(SPEC, req(None))
    assert r.ok and any("plan/image pairing" in c for c in r.checks)
    assert not Fake().provider(key="").preflight(SPEC, req(None)).ok


def test_not_configured_without_keys_and_the_throwaway_ssh_key_is_well_formed():
    assert not Fake().provider(key="", token="").configured()
    k = throwaway_key()
    kind, blob, comment = k.split(" ")
    raw = base64.b64decode(blob)
    assert kind == "ssh-ed25519" and comment == "projectv-no-login" and raw[:4] == b"\x00\x00\x00\x0b" and len(raw) == 4 + 11 + 4 + 32
    assert throwaway_key() != k, "a fresh pair each time"


def test_config_reads_the_yaml_and_defaults():
    import yaml

    from studio.tests.test_pricing import TEST_CONFIG

    c = E2eConfig.from_yaml(yaml.safe_load((TEST_CONFIG / "providers.yaml").read_text(encoding="utf-8")))
    assert c.public_port > 0 and c.plans and re.match(r"\d+", str(c.start_timeout_seconds))
    d = E2eConfig.from_yaml({})
    assert (d.location, d.image, d.inr_per_usd, d.gst_percent, d.public_port) == ("Delhi", "Ubuntu-22.04-GPU", 94, 18, 443)
