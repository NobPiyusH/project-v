"""Port of VastProviderTest: a fake Vast REST server (httpx MockTransport). Search filter, create, start, never-ready, disappear,
destroy, 2FA session handling and the hands-free TOTP login. No real instances."""

import json
import logging
import re
import time
from urllib.parse import unquote

import httpx
import pytest

from studio.gpu.broker import support
from studio.gpu.broker.provider import CreateRequest, GpuSpec, State
from studio.gpu.broker.vast import VastConfig, VastProvider, VastSessionExpired, session_key_source

KEY = "vast-key-must-never-reach-an-instance"
SESSION = "vast-2fa-session-key-must-never-reach-an-instance"
SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
SPEC = GpuSpec(["ignored"], 24, 48, 80)


class FakeVast:
    def __init__(self):
        self.requests, self.auth = [], []
        self.search_body = self.create_body = ""
        self.created_asks, self.destroyed, self.taken_asks = [], [], []
        self.show_calls: dict[str, int] = {}
        self.offers = "[]"
        self.script: dict[str, list[str]] = {}
        self.list_status = self.destroy_status = 200
        self.instances_list = '{"success":true,"instances":[],"next_token":null}'
        self.instances_page2 = '{"success":true,"instances":[],"next_token":null}'
        self.next_id = 777
        self.tfa_status, self.tfa_issues = 200, "sess-1"
        self.tfa_bodies, self.tfa_auth = [], []
        self.valid_session = None
        self.search_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.requests.append(f"{method} {path}")
        self.auth.append(str(request.headers.get("authorization")))
        body = request.content.decode()

        def send(status, text):
            return httpx.Response(status, content=text.encode(), headers={"Content-Type": "application/json"})

        if path == "/api/v0/bundles/":
            if self.search_status != 200:
                return send(self.search_status, '{"success":false,"msg":"bad key"}')
            self.search_body = body
            return send(200, f'{{"offers":{self.offers},"truncated":false}}')
        if path.startswith("/api/v0/asks/"):
            ask = re.sub(r".*/asks/(\d+)/?", r"\1", path)
            self.create_body = body
            if ask in self.taken_asks:
                return send(410, '{"success":false,"error":"no_such_ask","msg":"gone"}')
            self.created_asks.append(ask)
            self.next_id += 1
            return send(200, f'{{"success":true,"new_contract":{self.next_id - 1}}}')
        if path == "/api/v0/tfa/":
            self.tfa_bodies.append(body)
            self.tfa_auth.append(str(request.headers.get("authorization")))
            if self.tfa_status == 200:
                return send(200, f'{{"id":1,"tfa_status":"enabled","session_key":"{self.tfa_issues}"}}')
            return send(self.tfa_status, '{"success":false,"msg":"invalid code"}')
        if path.startswith("/api/v0/instances/"):
            id_ = re.sub(r".*/instances/?(\d*)/?", r"\1", path)
            if method != "DELETE":
                return send(410, '{"error":"deprecated_endpoint"}')
            if self.list_status != 200:
                return send(self.list_status, '{"success":false,"msg":"requires Two Factor Authentication"}')
            if self.destroy_status == 200:
                self.destroyed.append(id_)
                return send(200, '{"success":true}')
            return send(self.destroy_status, '{"success":false,"msg":"nope"}')
        if path.startswith("/api/v1/instances/"):
            q = unquote(request.url.query.decode())
            if self.valid_session is not None and request.headers.get("authorization") != f"Bearer {self.valid_session}":
                return send(401, '{"success":false,"msg":"requires Two Factor Authentication"}')
            if self.list_status != 200:
                return send(self.list_status, '{"success":false,"msg":"requires Two Factor Authentication"}')
            m = re.search(r'"id":\{"eq":(\d+)\}', q)
            if m:
                id_ = m.group(1)
                steps = self.script.get(id_)
                if steps is None:
                    return send(200, '{"success":true,"total_instances":0,"instances":[],"next_token":null}')
                n = self.show_calls.get(id_, 0)
                self.show_calls[id_] = n + 1
                return send(200, f'{{"success":true,"total_instances":1,"instances":[{{"id":{id_},{steps[min(n, len(steps) - 1)][1:]}],"next_token":null}}')
            if "after_token=p2" in q:
                return send(200, self.instances_page2)
            return send(200, self.instances_list)
        return httpx.Response(404)


def cfg(start_timeout, attempts):
    return VastConfig(["RTX 4090", "RTX 5090", "L40S", "RTX A6000"], 0.97, 500, 90, 0.60, 50, 3, 0.75, start_timeout, 1, attempts, 1.25, 0)


@pytest.fixture
def v():
    return FakeVast()


def provider(v, key=KEY, c=None, totp=""):
    client = httpx.Client(transport=httpx.MockTransport(v.handler))
    p = VastProvider(c or cfg(2, 2), "http://fake/api/v0", key, lambda: "" if (not key or totp) else SESSION, totp, client, 5)
    if totp:
        p.min_login_gap_millis, p.block_millis = 0, 60_000
        p.retry_wait_millis = lambda: 10
    return p


def offer(id_, gpu, dph, reliability, per_gb, verification="verified", gpus=1, ip="1.2.3.4", bid=False):
    return json.dumps({"id": id_, "gpu_name": gpu, "dph_total": dph, "reliability": reliability, "inet_down_cost": per_gb, "inet_up_cost": per_gb,
                       "verification": verification, "num_gpus": gpus, "public_ipaddr": ip, "is_bid": bid, "rentable": True, "rented": False,
                       "gpu_ram": 24564, "cpu_ram": 60000, "disk_space": 200, "inet_down": 900, "direct_port_count": 40,
                       "geolocation": "Vietnam, VN", "machine_id": 51178, "host_id": 55116})


def running(ip, host_port):
    return ('{"actual_status":"running","public_ipaddr":"%s","ports":{"8000/tcp":[{"HostIp":"0.0.0.0","HostPort":"%d"}]},'
            '"dph_total":0.42,"label":"projectv-vast-x"}' % (ip, host_port))


def request(q):
    env = {"SV_BOOT_00": "eJy1AAA+/=", "SV_BOOT_N": "1", "SV_PORT": "8000", "SV_ROOT": "/workspace/data", "SV_TOKEN": "0123456789abcdef"}
    return CreateRequest("projectv-vast-abcd1234-ef01", "pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime", 80,
                         ["bash", "-c", "guard", "projectv-guard", "python", "-c", "x"], env, 8000, None, q)


# ---- search


def test_unconfigured_without_key_and_search_sends_the_filter(v):
    assert not provider(v, "").configured()
    v.offers = "[" + offer(1, "RTX 4090", 0.41, 0.98, 0.001) + "]"
    p = provider(v)
    assert p.configured()
    p.quotes(SPEC)
    for must in ['"verified":{"eq":true}', '"rentable":{"eq":true}', '"rented":{"eq":false}', '"num_gpus":{"eq":1}', '"gpu_ram":{"gte":24000}',
                 '"reliability":{"gte":0.97}', '"direct_port_count":{"gte":1}', '"disk_space":{"gte":90}', '"inet_down":{"gte":500.0}',
                 '"type":"on-demand"', '"allocated_storage":90', "RTX 4090", "RTX 5090", "L40S", "RTX A6000"]:
        assert must in v.search_body, f"{must} in {v.search_body}"
    assert v.auth[0] == f"Bearer {KEY}"


def test_offer_filter_keeps_only_safe_offers_and_prices_come_from_the_live_offer(v):
    v.offers = "[" + ",".join([
        offer(1, "RTX 4090", 0.30, 0.98, 0.001, "unverified"), offer(2, "RTX 4090", 0.31, 0.90, 0.001),
        offer(3, "RTX 4090", 0.32, 0.99, 0.001, "verified", 2), offer(4, "RTX 4090", 0.33, 0.99, 0.001, "verified", 1, ""),
        offer(5, "RTX 4090", 0.34, 0.99, 0.001, "verified", 1, "1.2.3.4", True), offer(6, "RTX 3090", 0.20, 0.99, 0.001),
        offer(7, "RTX 4090", 0.35, 0.99, 0.0521),  # host charges $53/TB: 50 GB in = $2.6
        offer(8, "RTX 4090", 0.40, 0.99, 0.001), offer(9, "RTX 4090", 0.45, 0.99, 0.001), offer(10, "L40S", 0.53, 0.99, 0.001)]) + "]"
    q = provider(v).quotes(SPEC)
    assert len(q) == 2, f"one cheapest passing offer per GPU model: {q}"
    best = q[0]
    assert best.provider == "vast" and best.gpu.startswith("RTX 4090|8|")
    assert "Vietnam, VN" in best.gpu and best.gpu.endswith("|m51178"), "country and machine id in the key"
    assert best.hourly_usd == pytest.approx(0.40 + 0.053 / 0.75)  # bandwidth spread over 0.75 h on top of the offer's own price
    assert "host 55116" in best.detail and "machine 51178" in best.detail
    assert q[1].gpu.split("|")[0] == "L40S" and q[0].hourly_usd <= q[1].hourly_usd


def test_empty_or_fully_rejected_search_says_why(v):
    v.offers = "[" + offer(1, "RTX 4090", 0.41, 0.5, 0.001) + "]"
    q = provider(v).quote(SPEC)
    assert not q.available and "host reliability too low" in q.detail


# ---- create


def test_create_sends_no_secrets_waits_for_the_mapped_port_and_returns_the_direct_address(v):
    v.offers = "[" + offer(8, "RTX 4090", 0.40, 0.99, 0.001) + "]"
    p = provider(v)
    q = p.quote(SPEC)
    v.script["777"] = ['{"actual_status":null}', '{"actual_status":"loading"}', running("5.6.7.8", 41234)]
    i = p.create(request(q))
    assert i.id == "777" and i.endpoint == "http://5.6.7.8:41234" and v.created_asks == ["8"]
    assert i.hourly_usd == pytest.approx(q.hourly_usd) and i.gpu == "RTX 4090"
    b = v.create_body
    assert '"label":"projectv-vast-abcd1234-ef01"' in b and '"runtype":"args"' in b and '"disk":90' in b and "-p 8000:8000" in b
    assert "-e SV_TOKEN=0123456789abcdef" in b, "the worker token is the one secret the box needs"
    assert '"cancel_unavail":true' in b and KEY not in b and SESSION not in b, "no account key ever goes to an instance"
    for forbidden in ["RUNPOD", "API_KEY", "S3_", "GEMINI", "AWS_", "SECRET", "ssh_key", "SSH_PUBLIC_KEY", "jupyter"]:
        assert forbidden.upper() not in b.upper(), f"{forbidden} must not be in the create body"
    assert v.auth[0] == f"Bearer {KEY}", "offer search uses the normal key"
    assert all(h == f"Bearer {SESSION}" for h in v.auth[1:]), "create, show and destroy use the 2FA session key"


def test_env_values_that_could_break_the_env_string_are_refused(v):
    v.offers = "[" + offer(8, "RTX 4090", 0.40, 0.99, 0.001) + "]"
    p = provider(v)
    q = p.quote(SPEC)
    with pytest.raises(ValueError):
        p.create_body(CreateRequest("projectv-vast-a", "img", 80, ["x"], {"SV_ROOT": "/a b; rm -rf /"}, 8000, None, q))
    with pytest.raises(ValueError):
        p.create(CreateRequest("not-studio", "img", 80, ["x"], {}, 8000, None, q))
    assert not v.created_asks


def test_instance_that_exits_is_destroyed_and_the_next_offer_is_tried(v):
    v.offers = "[" + offer(8, "RTX 4090", 0.40, 0.99, 0.001) + "," + offer(9, "RTX 5090", 0.45, 0.99, 0.001) + "]"
    p = provider(v)
    q = p.quote(SPEC)
    v.script["777"] = ['{"actual_status":"loading"}', '{"actual_status":"exited"}']
    v.script["778"] = [running("9.9.9.9", 40000)]
    i = p.create(request(q))
    assert i.id == "778" and v.created_asks == ["8", "9"] and v.destroyed == ["777"], "the dead instance was destroyed, not left"


def test_instance_that_never_runs_is_destroyed_after_the_timeout(v):
    v.offers = "[" + offer(8, "RTX 4090", 0.40, 0.99, 0.001) + "]"
    p = provider(v, c=cfg(1, 1))
    q = p.quote(SPEC)
    v.script["777"] = ['{"actual_status":"loading"}']
    with pytest.raises(RuntimeError, match="not running"):
        p.create(request(q))
    assert v.destroyed == ["777"]


def test_offer_taken_between_search_and_create_falls_to_the_next_offer_inside_the_price_slack(v):
    v.offers = "[" + ",".join([offer(8, "RTX 4090", 0.40, 0.99, 0.001), offer(9, "RTX 5090", 0.45, 0.99, 0.001), offer(10, "L40S", 1.20, 0.99, 0.001)]) + "]"
    p = provider(v)
    q = p.quote(SPEC)
    v.taken_asks.append("8")
    v.script["777"] = [running("5.6.7.8", 41234)]
    i = p.create(request(q))
    assert v.created_asks == ["9"], "the dearer-than-slack L40S would never be used"
    assert i.id == "777"


def test_offer_taken_and_nothing_cheap_enough_fails(v):
    v.offers = "[" + ",".join([offer(8, "RTX 4090", 0.40, 0.99, 0.001), offer(10, "L40S", 1.20, 0.99, 0.001)]) + "]"
    p = provider(v)
    q = p.quote(SPEC)
    v.taken_asks.append("8")
    with pytest.raises(Exception):
        p.create(request(q))
    assert not v.created_asks


def test_never_rents_a_second_machine_when_the_first_could_not_be_destroyed(v):
    v.offers = "[" + ",".join([offer(8, "RTX 4090", 0.40, 0.99, 0.001), offer(9, "RTX 5090", 0.45, 0.99, 0.001)]) + "]"
    p = provider(v)
    q = p.quote(SPEC)
    v.script["777"] = ['{"actual_status":"exited"}']
    v.destroy_status = 500
    with pytest.raises(RuntimeError, match="destroying it failed"):
        p.create(request(q))
    assert v.created_asks == ["8"], "no second create"


# ---- status / list / destroy


def test_status_maps_running_starting_and_gone(v):
    p = provider(v)
    v.script["1"] = [running("5.6.7.8", 41234)]
    v.script["2"] = ['{"actual_status":"loading","start_date":%s}' % time.time()]
    v.script["3"] = ['{"actual_status":"exited"}']
    v.script["4"] = ['{"actual_status":"offline"}']
    v.script["5"] = ['{"actual_status":"loading","start_date":1000000000}']  # started long ago, never ran
    r = p.status("1")
    assert r.state == State.RUNNING and r.endpoint == "http://5.6.7.8:41234"
    assert p.status("2").state == State.STARTING
    assert p.status("3").state == State.GONE and p.status("4").state == State.GONE and p.status("5").state == State.GONE
    assert p.status("99").state == State.GONE, "it disappeared (outbid / destroyed from outside)"


def test_endpoint_for_rebuilds_the_address_after_a_restart_and_is_none_when_unknown(v):
    p = provider(v)
    v.script["1"] = [running("5.6.7.8", 41234)]
    assert p.endpoint_for("1", 8000) == "http://5.6.7.8:41234" and p.endpoint_for("99", 8000) is None


def test_list_pods_maps_label_status_and_cost(v):
    v.instances_list = ('{"success":true,"instances":[{"id":11,"label":"projectv-vast-aaaa-bbbb","actual_status":"running","dph_total":0.42,'
                        '"start_date":1795000000.5},{"id":12,"label":"my-own-box","actual_status":"loading","dph_total":1.5},'
                        '{"id":13,"label":null,"actual_status":null,"dph_total":0.1}],"next_token":null}')
    pods = provider(v).list_pods()
    assert len(pods) == 3 and pods[0].id == "11" and pods[0].name == "projectv-vast-aaaa-bbbb" and pods[0].status == "RUNNING"
    assert pods[0].cost_per_hr_usd == pytest.approx(0.42) and pods[0].created_at.timestamp() == pytest.approx(1795000000.5)
    assert pods[2].status == "LOADING" and pods[2].name == ""
    assert pods[1].created_at is not None, "never a null creation time: the watchdog would kill a fresh instance"


def test_list_pods_follows_the_pages_of_the_v1_list(v):
    v.instances_list = '{"success":true,"instances":[{"id":1,"label":"projectv-vast-a","actual_status":"running","dph_total":0.4}],"next_token":"p2"}'
    v.instances_page2 = '{"success":true,"instances":[{"id":2,"label":"projectv-vast-b","actual_status":"exited","dph_total":0.4}],"next_token":null}'
    pods = provider(v).list_pods()
    assert [p.id for p in pods] == ["1", "2"] and pods[1].status == "EXITED", "exited instances are listed so the watchdog destroys them"
    assert all(r.startswith("GET /api/v1/instances") for r in v.requests), "list uses v1, never the deprecated v0 list"


def test_expired_or_missing_two_factor_session_gives_a_clear_reason(v):
    v.list_status = 401
    with pytest.raises(VastSessionExpired) as e:
        provider(v).list_pods()
    assert str(e.value).startswith("Vast 2FA session expired: run `vastai tfa login --method-type totp -c <code>`")
    assert KEY not in str(e.value) and SESSION not in str(e.value)
    v.list_status = 403
    with pytest.raises(VastSessionExpired):
        provider(v).list_pods()
    with pytest.raises(VastSessionExpired):
        provider(v).terminate("5")
    with pytest.raises(VastSessionExpired):  # status() never turns a login problem into GONE (the broker would replace a healthy pod)
        provider(v).status("5")


def test_search_with_a_bad_normal_key_keeps_the_old_message(v):
    v.search_status = 401
    with pytest.raises(RuntimeError) as e:
        provider(v).quotes(SPEC)
    assert "refused the key" in str(e.value) and not isinstance(e.value, VastSessionExpired)


def test_session_key_comes_from_the_setting_else_the_cli_file_and_is_read_fresh_each_time(tmp_path):
    f = tmp_path / "vast_tfa_key"
    from_file = session_key_source("", f)
    assert from_file() == "", "no file, no setting: empty"
    f.write_text("abc123\r\n")
    assert from_file() == "abc123"
    f.write_text("newer")
    assert from_file() == "newer", "a fresh `vastai tfa login` is used without a restart"
    assert session_key_source(" fromsetting ", f)() == "fromsetting", "the setting wins over the file"


def test_terminate_destroys_and_treats_already_gone_as_done(v):
    p = provider(v)
    p.terminate("777")
    assert v.destroyed == ["777"] and "DELETE /api/v0/instances/777/" in v.requests
    v.destroy_status = 404
    p.terminate("778")  # already gone: fine
    v.destroy_status = 500
    with pytest.raises(Exception):
        p.terminate("779")


def test_guard_is_the_no_key_exit_guard(v):
    from datetime import UTC, datetime

    argv = provider(v).guard(["python", "w.py"], 120, 15, datetime(2026, 11, 21, 12, tzinfo=UTC))
    assert "RUNPOD" not in argv[2] and "kill -TERM" in argv[2] and "w.py" in argv


def test_config_reads_the_yaml_and_defaults():
    import yaml

    from studio.tests.test_pricing import TEST_CONFIG

    c = VastConfig.from_yaml(yaml.safe_load((TEST_CONFIG / "providers.yaml").read_text(encoding="utf-8")))
    assert c.gpu_names and c.min_reliability > 0 and c.offer_attempts >= 1 and c.price_slack >= 1
    d = VastConfig.from_yaml({})
    assert (d.min_reliability, d.min_down_mbps, d.disk_gb, d.offer_attempts, d.price_slack) == (0.97, 500, 90, 2, 1.25)


# ---- hands-free TOTP login


def code_now(offset):
    return support.totp_code(SEED, int(time.time()) + offset, 6)


def test_logs_in_lazily_once_with_the_normal_key_and_uses_the_session_key_afterwards(v):
    v.valid_session = "sess-1"
    p = provider(v, totp=SEED)
    v.offers = "[" + offer(1, "RTX 4090", 0.41, 0.98, 0.001) + "]"
    p.quotes(SPEC)
    assert not v.tfa_bodies, "offer search needs no login"
    p.list_pods()
    p.list_pods()
    assert len(v.tfa_bodies) == 1, "one login, then the session is reused"
    assert v.tfa_auth[0] == f"Bearer {KEY}", "login carries the normal API key"
    body = v.tfa_bodies[0]
    assert '"tfa_method": "totp"' in body or '"tfa_method":"totp"' in body
    assert any(f'"{c}"' in body for c in (code_now(0), code_now(-1), code_now(1))), "a 6-digit TOTP code for the current window"
    assert SEED not in body, "the seed itself is never sent"
    assert all(h == f"Bearer {KEY}" or h == "Bearer sess-1" for h in v.auth)


def test_expired_session_is_refreshed_once_and_the_call_retried(v):
    v.valid_session = "sess-1"
    p = provider(v, totp=SEED)
    p.list_pods()
    v.valid_session, v.tfa_issues = "sess-2", "sess-2"  # Vast expired the first session
    p.list_pods()
    assert len(v.tfa_bodies) == 2, "exactly one more login"
    v.valid_session = "sess-3"  # expired again and the login hands out a key that still fails: no loop
    before = len(v.tfa_bodies)
    with pytest.raises(VastSessionExpired):
        p.list_pods()
    assert len(v.tfa_bodies) - before <= 1, "at most one login per call"


def test_rejected_code_is_retried_once_in_the_next_window_then_login_pauses_with_a_clear_reason(v):
    v.tfa_status = 401
    p = provider(v, totp=SEED)
    with pytest.raises(VastSessionExpired) as e:
        p.list_pods()
    assert len(v.tfa_bodies) == 2, "the first try and one retry, never more"
    assert "rejected twice" in str(e.value) and "VAST_TOTP_SECRET" in str(e.value)
    with pytest.raises(VastSessionExpired) as again:
        p.list_pods()
    assert len(v.tfa_bodies) == 2, "paused: no further login attempts while blocked"
    assert "paused" in str(again.value) and SEED not in str(e.value) + str(again.value)


def test_no_seed_means_no_login_attempt_and_the_old_fallback_still_works(v):
    provider(v).list_pods()  # session key from the supplier, no seed
    assert not v.tfa_bodies


def test_neither_the_seed_nor_codes_nor_session_keys_are_logged(v, caplog):
    caplog.set_level(logging.DEBUG, logger="studio.gpu.broker.vast")
    v.valid_session = "sess-1"
    provider(v, totp=SEED).list_pods()
    v.tfa_status = 401
    with pytest.raises(VastSessionExpired):
        provider(v, totp=SEED).list_pods()
    assert caplog.records, "the login and the failure are logged"
    text = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (SEED, "sess-1", KEY, SESSION, code_now(-1), code_now(0), code_now(1)):
        assert secret not in text, f"must not log: {secret[:3]}..."
