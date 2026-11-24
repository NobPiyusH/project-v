"""reaper.decide is pure; run() is tested with a fake API. No network, no Docker, no pods."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from podkit import reaper  # noqa: E402
from podkit.reaper import Config, Pod, decide  # noqa: E402

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def pod(id="p1", name="projectv-x", age_min=30, cost=0.5, status="RUNNING"):
    t = NOW - timedelta(minutes=age_min)
    return Pod(id, name, status, cost, t, t)


def one(plan, pid="p1"):
    return next(d for d in plan.decisions if d.pod_id == pid)


def test_young_leased_pod_is_kept():
    plan = decide([pod()], NOW, {"p1": NOW - timedelta(minutes=1)}, Config())
    assert one(plan).action == "keep"


def test_default_max_wallclock_terminates():
    d = one(decide([pod(age_min=181)], NOW, {"p1": NOW}, Config()))
    assert (d.action, d.rule) == ("terminate", "max_wallclock")


def test_name_suffix_overrides_max_hours():
    assert one(decide([pod(name="projectv-x-maxh=2", age_min=130)], NOW, {"p1": NOW}, Config())).rule == "max_wallclock"
    assert one(decide([pod(name="projectv-x-maxh=6", age_min=250)], NOW, {"p1": NOW}, Config(daily_cap_usd=99))).action == "keep"


def test_wallclock_applies_to_non_projectv_pods_too():
    assert one(decide([pod(name="mystery", age_min=200)], NOW, {}, Config())).rule == "max_wallclock"


def test_projectv_pod_without_lease_dies_after_grace_only():
    assert one(decide([pod(age_min=10)], NOW, {}, Config())).action == "keep"
    d = one(decide([pod(age_min=16)], NOW, {}, Config()))
    assert (d.action, d.rule) == ("terminate", "no_lease")


def test_stale_heartbeat_counts_as_no_lease():
    d = one(decide([pod()], NOW, {"p1": NOW - timedelta(minutes=45)}, Config()))
    assert d.rule == "no_lease"


def test_lease_with_no_heartbeat_yet_is_live():
    assert one(decide([pod()], NOW, {"p1": None}, Config())).action == "keep"


def test_non_projectv_pod_needs_no_lease():
    assert one(decide([pod(name="someone-else")], NOW, {}, Config())).action == "keep"


def test_unknown_leases_never_kill_on_rule_b():
    assert one(decide([pod(age_min=100)], NOW, None, Config())).action == "keep"


def test_stopped_pod_is_left_alone():
    d = one(decide([pod(status="EXITED", age_min=999)], NOW, {}, Config()))
    assert (d.action, d.rule) == ("keep", "not_running")


def test_daily_cap_stops_pod_that_would_pass_it():
    # 175 min at $1.15 = $3.35 today; another 10 min = +$0.19 -> over $3.50
    d = one(decide([pod(age_min=175, cost=1.15)], NOW, {"p1": NOW}, Config()))
    assert (d.action, d.rule) == ("terminate", "daily_cap")


def test_daily_cap_counts_spend_from_pods_that_already_ended():
    plan = decide([pod(age_min=20, cost=0.9)], NOW, {"p1": NOW}, Config(), spent_today={"gone": 3.40})
    assert one(plan).rule == "daily_cap"
    assert plan.today_total == pytest.approx(3.40 + 0.3)


def test_daily_cap_only_counts_since_utc_midnight():
    now = datetime(2026, 11, 25, 0, 30, tzinfo=UTC)
    t = now - timedelta(minutes=150)  # started 22:00 yesterday: only 30 min belong to today
    plan = decide([Pod("p1", "projectv-x", "RUNNING", 2.0, t, t)], now, {"p1": now}, Config())
    assert plan.today_total == pytest.approx(1.0)
    assert one(plan).action == "keep"


def test_cap_keeps_the_cheaper_pod_when_only_one_fits():
    pods = [pod("a", cost=0.4, age_min=20), pod("b", cost=3.0, age_min=20)]
    plan = decide(pods, NOW, {"a": NOW, "b": NOW}, Config(daily_cap_usd=1.5))
    assert (one(plan, "a").action, one(plan, "b").action) == ("keep", "terminate")


def test_pod_name_suffix_parsing():
    assert reaper.pod_max_hours("projectv-x-maxh2.5", 3) == 2.5
    assert reaper.pod_max_hours("projectv-x", 3) == 3
    assert reaper.pod_max_hours("projectv-x-maxh=0", 3) == 3


def test_parse_pod_accepts_runpod_timestamp_formats():
    p = reaper.parse_pod(
        {
            "id": "a",
            "name": "n",
            "desiredStatus": "RUNNING",
            "costPerHr": 0.69,
            "createdAt": "2026-11-25 10:20:30.123 +0000 UTC",
            "lastStartedAt": "2026-11-25T10:21:00Z",
        }
    )
    assert p.cost_per_hr == 0.69 and p.created_at.hour == 10 and p.started_at.minute == 21 and p.status == "RUNNING"


# --- run() ------------------------------------------------------------------------------------------------------------
class FakeApi:
    def __init__(self, pods=None, boom=False, fail_terminate=False):
        self.pods, self.boom, self.fail, self.terminated = pods or [], boom, fail_terminate, []

    def list_pods(self):
        if self.boom:
            raise OSError("secret-url")
        return self.pods

    def terminate(self, pod_id):
        if self.fail:
            raise OSError("nope")
        self.terminated.append(pod_id)


def run(api, tmp_path, apply=False, leases=None):
    lines = []
    code = reaper.run(
        api, NOW, Config(), apply, tmp_path / "s.json", tmp_path / "r.log", lambda: {} if leases is None else leases, lines.append
    )
    return code, lines


def test_dry_run_never_terminates_and_logs_would(tmp_path):
    api = FakeApi([pod(age_min=200)])
    code, lines = run(api, tmp_path)
    assert code == 0 and api.terminated == []
    assert any(line.split(" ", 1)[1].startswith("WOULD TERMINATE p1") for line in lines)
    assert (tmp_path / "r.log").read_text().count("\n") == len(lines)


def test_apply_terminates_and_second_run_is_a_noop_when_pod_is_gone(tmp_path):
    api = FakeApi([pod(age_min=200)])
    assert run(api, tmp_path, apply=True)[0] == 0 and api.terminated == ["p1"]
    api.pods = []
    assert run(api, tmp_path, apply=True)[0] == 0 and api.terminated == ["p1"]


def test_unreachable_api_exits_2_without_echoing_the_error(tmp_path):
    code, lines = run(FakeApi(boom=True), tmp_path)
    assert code == 2 and "secret-url" not in " ".join(lines)


def test_failed_terminate_exits_1(tmp_path):
    code, lines = run(FakeApi([pod(age_min=200)], fail_terminate=True), tmp_path, apply=True)
    assert code == 1 and any("FAILED" in line for line in lines)


def test_state_carries_spend_across_runs_within_a_day(tmp_path):
    api = FakeApi([pod(age_min=60, cost=1.0)])
    run(api, tmp_path, leases={"p1": NOW})
    api.pods = []
    _, lines = run(api, tmp_path)
    assert "$1.00 of $3.50" in lines[0]


def test_key_comes_from_env_file_and_is_never_in_errors(tmp_path, monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_text('OTHER=1\nRUNPOD_API_KEY="abc123"\n', encoding="utf-8")
    assert reaper.find_env_key(str(f)) == "abc123"
    f.write_text("OTHER=1\n", encoding="utf-8")
    with pytest.raises(RuntimeError) as e:
        reaper.find_env_key(str(f))
    assert "abc123" not in str(e.value)
