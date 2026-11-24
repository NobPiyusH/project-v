"""podkit: spend ledger, rent logic, boot script, fail-closed guards. No network, no Docker, no pods."""

import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from podkit import boot, guard, rent  # noqa: E402
from podkit.ledger import CapExceeded, Ledger  # noqa: E402


# --- ledger -----------------------------------------------------------------
def test_ledger_reserves_the_worst_case_and_refuses_what_does_not_fit(tmp_path):
    led = Ledger(tmp_path / "l.json", cap_usd=4.0)
    led.add_fixed("keyframes", 0.22)
    assert led.reserve("a", 0.89, 75) == pytest.approx(1.1125)
    led.reserve("b", 0.89, 75)
    led.reserve("c", 0.89, 75)  # 0.22 + 3 x 1.1125 = 3.5575
    with pytest.raises(CapExceeded):
        led.reserve("d", 0.89, 75)
    assert led.remaining() == pytest.approx(4.0 - 3.5575)


def test_ledger_books_the_actual_cost_and_frees_the_rest(tmp_path):
    now = [1000.0]
    led = Ledger(tmp_path / "l.json", 4.0, clock=lambda: now[0])
    led.reserve("a", 0.60, 60)  # ceiling 0.60
    now[0] += 30 * 60  # ran 30 minutes
    assert led.close("a") == pytest.approx(0.30)
    assert led.committed() == pytest.approx(0.30)
    led.reserve("b", 0.60, 60)  # fits again because "a" ended early


def test_ledger_never_books_more_than_one_and_a_half_times_the_ceiling(tmp_path):
    now = [0.0]
    led = Ledger(tmp_path / "l.json", 10.0, clock=lambda: now[0])
    led.reserve("a", 1.0, 60)
    now[0] += 10 * 3600  # a clock that ran away
    assert led.close("a") == pytest.approx(1.5)


def test_ledger_max_minutes_fits_the_remaining_budget(tmp_path):
    led = Ledger(tmp_path / "l.json", 1.0)
    assert led.max_minutes(0.89, 75) == 67  # 1.00 / 0.89 h
    led.add_fixed("x", 1.0)
    assert led.max_minutes(0.89, 75) == 0


def test_two_shards_share_one_ledger_file(tmp_path):
    a, b = Ledger(tmp_path / "l.json", 2.0), Ledger(tmp_path / "l.json", 2.0)
    a.reserve("pod-a", 1.0, 60)
    with pytest.raises(CapExceeded):
        b.reserve("pod-b", 1.5, 60)
    assert "pod-a" in b.summary()


# --- rent -------------------------------------------------------------------
class FakeGql:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, q, v=None):
        self.calls.append((q, v))
        return self.answers.pop(0)


def _ok(pid, price):
    return {"data": {"podFindAndDeployOnDemand": {"id": pid, "costPerHr": price}}}


NO_STOCK = {"errors": [{"message": "There are no longer any instances available"}]}


def test_rent_falls_back_from_community_to_secure():
    g = FakeGql([NO_STOCK] * 4 + [_ok("p1", 0.89)])
    pod = rent.rent_pod(g, lambda cloud, gpu: {"cloud": cloud, "gpu": gpu}, say=lambda m: None)
    assert pod.id == "p1" and pod.cloud == "SECURE" and pod.cost_per_hr == 0.89


def test_rent_terminates_a_pod_above_the_tier_ceiling_and_keeps_looking():
    g = FakeGql([_ok("dear", 0.95), {"data": {}}, _ok("ok", 0.40)])
    pod = rent.rent_pod(
        g, lambda c, gpu: {}, gpus=("A", "B"), tiers=(("COMMUNITY", 0.55),), say=lambda m: None, sleep=lambda s: None
    )
    assert pod.id == "ok"
    assert any("podTerminate" in q for q, _ in g.calls)


def test_rent_returns_none_when_nothing_is_in_stock():
    g = FakeGql([NO_STOCK] * 8)
    assert rent.rent_pod(g, lambda c, gpu: {}, say=lambda m: None) is None


# --- boot -------------------------------------------------------------------
def test_boot_script_never_ends_in_a_bare_wait():
    """A bare `wait` waits for ComfyUI, which never exits: BOOT COMPLETE would never print (25 min of pod time lost, 24 Nov)."""
    script = boot.boot_script()
    assert "wait $DLPID" in script
    assert not any(line.strip() == "wait" for line in script.splitlines())
    assert "BOOT COMPLETE" in script and "ComfyUI-KJNodes" in script and "RealESRGAN_x4plus.pth" in script


def test_boot_script_can_leave_out_the_upscaler():
    assert "RealESRGAN" not in boot.boot_script(esrgan=False)


def test_docker_args_round_trip():
    args = boot.docker_args()
    payload = args.split("echo ")[1].split(" |")[0]
    assert base64.b64decode(payload).decode() == boot.boot_script()


def test_pod_env_carries_files_and_token():
    env = {e["key"]: e["value"] for e in boot.pod_env("tok", "https://kj", {"f": ["repo", "p", "loras", 1]})}
    assert env["HF_TOKEN"] == "tok" and env["KJ_URL"] == "https://kj"
    assert "repo" in base64.b64decode(env["FILES_B64"]).decode()


# --- guard ------------------------------------------------------------------
def test_guards_confirm_the_lease_row():
    started = []
    ok = guard.start_guards(
        "pod1",
        "projectv-x",
        "t",
        "s.json",
        60,
        "lease.py",
        "killer.py",
        popen=lambda *a, **k: started.append(a[0][1]),
        query=lambda sql: "1",
        confirm_s=5,
        sleep=lambda s: None,
    )
    assert ok and started == ["lease.py", "killer.py"]


def test_guards_fail_closed_when_the_database_is_down():
    t = [0.0]

    def down(sql):
        raise RuntimeError("docker is not running")

    def sleep(s):
        t[0] += s

    ok = guard.start_guards(
        "pod1",
        "projectv-x",
        "t",
        "s.json",
        60,
        "l.py",
        "k.py",
        popen=lambda *a, **k: None,
        query=down,
        confirm_s=20,
        sleep=sleep,
        clock=lambda: t[0],
    )
    assert ok is False  # the caller must terminate the pod


def test_lease_open_is_false_for_an_empty_count():
    assert guard.lease_open("pod1", lambda sql: "0") is False
