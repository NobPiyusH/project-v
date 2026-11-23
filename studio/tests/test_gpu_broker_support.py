"""Ports of TotpTest, WorkerAuthTest, PodBootTest, plus wallet/config/records/states/preferences on the scratch Postgres."""

import base64
import uuid
import zlib

import pytest

from studio.gpu.broker import support
from studio.gpu.broker.config import BrokerConfig
from studio.gpu.broker.records import PgBrokerRecords
from studio.gpu.broker.wallet import GpuWallet
from studio.ledger import CostLedger
from studio.tests.test_pricing import TEST_CONFIG, at

SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32 of the RFC's ASCII secret


def test_totp_rfc6238_vectors_eight_digits():
    for t, want in [(59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"), (1234567890, "89005924"),
                    (2000000000, "69279037"), (20000000000, "65353130")]:
        assert support.totp_code(SEED, t, 8) == want


def test_totp_six_digit_codes_keep_leading_zeros():
    assert support.totp_code(SEED, 59, 6) == "287082" and support.totp_code(SEED, 1111111109, 6) == "081804"
    assert support.totp_code(SEED, 1234567890, 6) == "005924"


def test_totp_base32_ignores_case_spaces_and_padding_and_rejects_typos():
    assert support.totp_code(SEED, 59) == support.totp_code("gezd gnbv-gy3t qojq gezd gnbv gy3t qojq====", 59)
    for bad in ("GEZD1NBV", ""):
        with pytest.raises(ValueError):
            support.totp_code(bad, 59)


def test_totp_window_countdown():
    assert support.millis_to_next_window(0) == 30_000 and support.millis_to_next_window(29_999) == 1


AUTH = support.WorkerAuth(b"s", {"vast", "runpod"})


def test_worker_token_is_stable_for_a_pod_name_and_different_per_rental():
    t = AUTH.token("projectv-vast-abcd1234-ef01")
    assert len(t) == 48 and all(c in "0123456789abcdef" for c in t)
    assert t == support.WorkerAuth(b"s", {"vast"}).token("projectv-vast-abcd1234-ef01"), "same after a restart"
    assert t != AUTH.token("projectv-vast-abcd1234-ef02") and t != support.WorkerAuth(b"other", {"vast"}).token("projectv-vast-abcd1234-ef01")


def test_worker_secret_derived_from_provider_keys_random_without_keys():
    a, b = support.WorkerAuth.from_settings("", "rk", "vk"), support.WorkerAuth.from_settings("", "rk", "vk")
    assert a.token("n") == b.token("n") and "rk" not in a.token("n")
    assert support.WorkerAuth.from_settings("", "", "").token("n") != support.WorkerAuth.from_settings("", "", "").token("n")
    assert a.token("n") != support.WorkerAuth.from_settings("explicit", "rk", "vk").token("n")


def test_worker_env_and_address_only_for_enabled_providers():
    env_in = {"SV_PORT": "8000"}
    out = AUTH.env("vast", "projectv-vast-x", env_in)
    assert out["SV_PORT"] == "8000" and out["SV_TOKEN"] == AUTH.token("projectv-vast-x") and "SV_TOKEN" not in env_in
    assert AUTH.decorate("vast", "projectv-vast-x", "http://1.2.3.4:41000") == f"http://worker:{AUTH.token('projectv-vast-x')}@1.2.3.4:41000"
    assert AUTH.decorate("runpod", "p", "https://id-8000.proxy.runpod.net") == f"https://worker:{AUTH.token('p')}@id-8000.proxy.runpod.net"
    assert AUTH.env("jarvislabs", "n", env_in) is env_in and AUTH.decorate("jarvislabs", "n", "http://x") == "http://x"
    assert AUTH.decorate("vast", "n", None) is None
    once = AUTH.decorate("vast", "n", "http://1.2.3.4:1")
    assert AUTH.decorate("vast", "n", once) == once, "never decorated twice"


def test_runpod_token_can_be_switched_off_but_vast_always_has_it():
    a = support.WorkerAuth.from_settings("s", "", "", runpod=False)
    assert a.enabled_for("vast") and not a.enabled_for("runpod") and not support.WorkerAuth().enabled_for("vast")


def test_pod_boot_worker_round_trips_through_chunked_env():
    src = ("print('hello')\n" * 2000).encode()
    env = support.boot_env(src, 8000, "/workspace/data")
    n = int(env["SV_BOOT_N"])
    b64 = "".join(env[f"SV_BOOT_{i:02d}"] for i in range(n))
    assert all(len(env[f"SV_BOOT_{i:02d}"]) <= 900 for i in range(n))
    assert zlib.decompress(base64.b64decode(b64)) == src
    assert env["SV_PORT"] == "8000" and env["SV_ROOT"] == "/workspace/data" and support.boot_command()[0] == "python"
    assert "SV_VOLUME" not in env and "SV_BAKED" not in env, "warm start is off unless asked for"
    assert support.boot_env(src, 8000, "/workspace/data", "/workspace")["SV_VOLUME"] == "/workspace"
    assert support.boot_env(src, 8000, "/workspace/data", None, "/opt/projectv")["SV_BAKED"] == "/opt/projectv"


def test_the_boot_command_executes_the_payload(tmp_path):
    import os
    import subprocess
    import sys

    env = {**os.environ, **support.boot_env(b"print('worker says hi')", 8000, "/r")}
    out = subprocess.run([sys.executable, *support.boot_command()[1:]], env=env, capture_output=True, text=True)
    assert out.stdout.strip() == "worker says hi", out.stderr


# ---- config, wallet, records, states, preferences against the real schema


def test_broker_config_reads_pricing_gpu_broker():
    cfg = BrokerConfig.load(TEST_CONFIG)
    assert cfg.order and isinstance(cfg.monthly_cap_usd, dict) and cfg.port > 0
    d = BrokerConfig.from_yaml({})
    assert d.order[0] == "runpod" and d.expected_hours == 0.75 and d.cap_usd("runpod") == 0.0


def test_wallet_rules():
    cfg = BrokerConfig(["runpod"], {"runpod": 5.0, "vast": 5.0}, 8.0, ["x"], 24, 48, 80, 1.0, 8000, 3.5)
    spent = {"runpod": 4.9, "vast": 0.0}
    w = GpuWallet(cfg, lambda p: spent[p], lambda: 3.2)
    assert w.refusal("modal", 0.1).startswith("blocked: no monthly cap set")
    assert w.refusal("runpod", 0.5).startswith("wallet: $0.10 left of the $5.00 cap")
    assert w.refusal("vast", 0.5).startswith("daily GPU cap: $3.20 spent today (UTC) of the $3.50 cap")
    assert w.remaining_usd("vast") == pytest.approx(3.1), "the total cap (8.0 - 4.9) is tighter than the provider's own"
    assert GpuWallet(cfg, lambda p: 0.0).refusal("vast", 0.5) is None


def test_wallet_reads_this_months_and_todays_spend_from_the_ledger(scratch_db):
    pricing = at("2026-11-19")
    for provider, inr in (("zz_gpu", 94), ("modal_x", 47)):
        CostLedger(scratch_db).record(None, provider, __import__("decimal").Decimal(inr), None, None, True, "t")
    cfg = BrokerConfig(["zz_gpu"], {"zz_gpu": 5.0}, 0, ["x"], 24, 48, 80, 1.0, 8000, 3.5)
    w = GpuWallet.from_db(cfg, scratch_db, pricing)
    assert w.remaining_usd("zz_gpu") == pytest.approx(5.0 - 94 / pricing.fx())
    assert w.spent_today_usd() == pytest.approx((94 + 47) / pricing.fx()), "broker providers plus Modal's GPU seconds"


def test_records_book_a_short_rental_and_the_canary(scratch_db):
    pricing = at("2026-11-19")
    rec = PgBrokerRecords(scratch_db, pricing)
    rec.cost(None, "zz_canary", 0.5, 0.4, "canary rental x")  # no Short: booked without any owner id
    row = scratch_db.rows("SELECT amount_inr, units, unit_type, estimated, note, video_id, dream_short_id FROM cost_entry WHERE provider_id = 'zz_canary'")[0]
    assert float(row["amount_inr"]) == pytest.approx(0.5 * 0.4 * pricing.fx(), abs=1e-4) and float(row["units"]) == 0.5
    assert row["unit_type"] == "gpu_hour" and row["estimated"] is True and row["note"] == "canary rental x ($0.4000/h live)"
    assert row["video_id"] is None and row["dream_short_id"] is None
    rec.decision(uuid.uuid4(), "video.i2v.dream", [{"provider": "runpod"}], "runpod", "r")  # not a Short: logs nothing
    assert scratch_db.rows("SELECT count(*) AS n FROM routing_decision WHERE stage = 'DREAM_GPU'")[0]["n"] == 0


def test_provider_states_and_preferences_on_postgres(scratch_db):
    st = support.PgStates(scratch_db)
    assert st.proven("runpod") and st.proven("vast"), "seeded by the V41 migration"
    assert not st.proven("jarvislabs") and st.get("jarvislabs").preflight_ok is None
    st.record_preflight("jarvislabs", True, "ok")
    st.mark_proven("jarvislabs", "canary ok")
    s = st.get("jarvislabs")
    assert s.proven and s.proven_note == "canary ok" and s.preflight_ok is True and s.preflight_note == "ok"
    pr = support.PgPreferences(scratch_db)
    assert pr.learned_key() is None
    for _ in range(3):
        pr.record_choice(uuid.uuid4(), "runpod|A6000|SECURE", "runpod|4090|COMMUNITY", {"x": 1})
    assert pr.learned_key() == "runpod|A6000|SECURE"


def test_boot_timings_on_postgres(scratch_db):
    from datetime import UTC, datetime, timedelta

    t = support.PgTimings(scratch_db)
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    t.rented("zz-pod", "runpod", "img", now)
    t.rented("zz-pod", "runpod", "other", now)  # first rent wins
    t.running("zz-pod", now + timedelta(seconds=40))
    t.running("zz-pod", now + timedelta(seconds=400))
    t.worker_report("zz-pod", {"marks": [{"phase": "ready", "at": now.timestamp() + 100}]})
    t.worker_report("zz-pod", {"marks": []})
    row = t.get("zz-pod")
    assert row.image == "img" and row.running_at == now + timedelta(seconds=40) and row.worker["marks"][0]["phase"] == "ready"
    assert support.summary(row)["totalSeconds"] == 100.0 and t.recent(5)[0].pod_id == "zz-pod"
