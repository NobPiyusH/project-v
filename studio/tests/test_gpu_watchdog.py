"""Ports of PodWatchdogTest and PodSelfStopTest. RunPod is a fake (httpx MockTransport): no network, no real pods."""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from studio.gpu import self_stop
from studio.gpu.leases import Lease
from studio.gpu.pods import GpuPodLimits, RunPodApi
from studio.gpu.watchdog import PodWatchdog, WatchdogLoop

NOW = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)


def clock():
    return NOW


class FakeRunPod:
    def __init__(self):
        self.pods_json = {"pods": [], "pagination": {"nextCursor": None, "hasNextPage": False}}
        self.list_status = 200
        self.deleted: list[str] = []
        self.auth: list[str] = []
        self.raise_connect = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.raise_connect:
            raise httpx.ConnectError("refused")
        self.auth.append(request.headers.get("authorization"))
        if request.method == "DELETE":
            self.deleted.append(request.url.path.rsplit("/", 1)[1])
            return httpx.Response(204)
        if self.list_status != 200:
            return httpx.Response(self.list_status, json={"error": "down"})
        return httpx.Response(200, json=self.pods_json)

    def api(self, key="test-key") -> RunPodApi:
        return RunPodApi("http://fake/v2", key, client=httpx.Client(transport=httpx.MockTransport(self.handler)))

    def pods(self, *pods):
        self.pods_json = {"pods": list(pods), "pagination": {"nextCursor": None, "hasNextPage": False}}


def pod(id, name, age_min, cost):
    created = (NOW - timedelta(minutes=age_min)).isoformat().replace("+00:00", "Z")
    return {"id": id, "name": name, "status": "RUNNING", "cost": cost, "createdAt": created, "startedAt": created}


class FakeLeases:
    def __init__(self):
        self.beats, self.released = {}, {}

    def open(self, provider, pod_id, name, purpose):
        self.beats[pod_id] = NOW

    def release(self, pod_id):
        self.released[pod_id] = NOW

    def find(self, pod_id):
        return Lease(pod_id, "x", self.beats[pod_id], self.released.get(pod_id)) if pod_id in self.beats else None


class FakeEvents:
    def __init__(self):
        self.evidence, self.booked, self.last = [], [], None

    def terminated(self, provider, p, reason, hours, usd):
        self.evidence.append(f"{p.id}|{reason}|{usd}")
        self.booked.append(f"kill|{hours}")

    def last_orphan_booking(self, pod_id):
        return self.last

    def orphan_billed(self, provider, p, reason, hours, usd):
        self.booked.append(f"orphan|{hours}")
        self.last = NOW


def limits(dry_run=False, prefix="projectv-", max_pods=2, max_usd=2.0):
    return GpuPodLimits(prefix, 120, 15, 10, max_pods, max_usd, dry_run)


@pytest.fixture
def env():
    fake, leases, events = FakeRunPod(), FakeLeases(), FakeEvents()
    return fake, leases, events, lambda dry=False: PodWatchdog(fake.api(), leases, events, limits(dry), clock)


def test_kills_over_age_pod_even_with_a_fresh_lease(env):
    fake, leases, events, wd = env
    fake.pods(pod("old1", "projectv-wan-1", 130, 0.9))
    leases.open("runpod", "old1", "projectv-wan-1", "wan")
    r = wd().sweep()
    assert fake.deleted == ["old1"] and r.actions[0].done and r.actions[0].reason.startswith("over max lifetime")
    assert len(events.evidence) == 1 and events.evidence[0].endswith(f"|{130 / 60.0 * 0.9}")
    assert leases.released["old1"] is not None and fake.auth[0] == "Bearer test-key"


def test_spares_young_leased_pod(env):
    fake, leases, _, wd = env
    fake.pods(pod("young", "projectv-wan-2", 30, 0.9))
    leases.open("runpod", "young", "projectv-wan-2", "wan")
    r = wd().sweep()
    assert not fake.deleted and not r.actions and r.studio_pods == 1


def test_spares_young_pod_inside_the_lease_grace_but_kills_an_unleased_one(env):
    fake, _, _, wd = env
    fake.pods(pod("new", "projectv-a", 5, 0.5), pod("orphan", "projectv-b", 11, 0.5))
    wd().sweep()
    assert fake.deleted == ["orphan"]


def test_kills_idle_and_released_pods(env):
    fake, leases, _, wd = env
    fake.pods(pod("idle", "projectv-a", 60, 0.5), pod("done", "projectv-b", 60, 0.5))
    leases.open("runpod", "idle", "projectv-a", "x")
    leases.beats["idle"] = NOW - timedelta(minutes=16)
    leases.open("runpod", "done", "projectv-b", "x")
    leases.release("done")
    wd().sweep()
    assert fake.deleted == ["idle", "done"]


def test_ignores_pods_without_the_prefix(env):
    fake, _, events, wd = env
    fake.pods(pod("mine", "my-own-jupyter", 900, 2.5), pod("almost", "projectv", 900, 1), pod("x", "Projectv-x", 900, 1))
    r = wd().sweep()
    assert not fake.deleted and r.studio_pods == 0 and not events.evidence


def test_dry_run_terminates_nothing(env):
    fake, _, events, wd = env
    fake.pods(pod("old1", "projectv-wan-1", 500, 0.9))
    r = wd(True).sweep()
    assert not fake.deleted and not events.evidence and len(r.actions) == 1 and not r.actions[0].done and r.dry_run


def test_api_failure_never_raises_out_of_the_sweep_or_the_loop(env):
    fake, _, _, wd = env
    fake.list_status = 500
    w = wd()
    r = w.sweep()
    assert r.error and not fake.deleted
    WatchdogLoop([w]).tick()
    fake.raise_connect = True
    assert w.sweep().error


def test_no_key_means_no_calls_at_all(env):
    fake, leases, events, _ = env
    fake.pods(pod("old1", "projectv-wan-1", 500, 0.9))
    r = PodWatchdog(fake.api(""), leases, events, limits(), clock).sweep()
    assert not r.configured and not fake.auth


def test_follows_pagination_cursor():
    def handler(request):
        cursor = parse_qs(urlparse(str(request.url)).query).get("cursor")
        if cursor == ["c2"]:
            body = {"pods": [pod("p2", "projectv-2", 200, 1)], "pagination": {"nextCursor": None, "hasNextPage": False}}
        else:
            body = {"pods": [pod("p1", "projectv-1", 200, 1)], "pagination": {"nextCursor": "c2", "hasNextPage": True}}
        return httpx.Response(200, json=body)

    api = RunPodApi("http://fake/v2", "k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(api.list_pods()) == 2


def test_warns_over_limits_and_start_guard_refuses(env):
    fake, _, _, wd = env
    fake.pods(pod("a", "projectv-a", 5, 1.2), pod("b", "projectv-b", 5, 1.2), pod("c", "projectv-c", 5, 1.2))
    w = wd(True)
    assert len(w.sweep().warnings) == 2
    with pytest.raises(RuntimeError):
        w.check_can_start(0.5)
    fake.pods(pod("a", "projectv-a", 5, 1.2), pod("other", "someone-else", 5, 9))
    with pytest.raises(RuntimeError, match=r"\$2\.10/h"):
        w.check_can_start(0.9)
    w.check_can_start(0.7)


def test_dry_run_books_an_orphan_in_steps_and_the_real_kill_books_only_the_rest(env):
    fake, leases, events, _ = env
    fake.pods(pod("orphan", "projectv-orphan", 60, 0.6))  # no lease, 60 min old: past the grace period
    PodWatchdog(fake.api(), leases, events, limits(True), clock).sweep()
    assert events.booked == ["orphan|1.0"] and not fake.deleted
    PodWatchdog(fake.api(), leases, events, limits(True), clock).sweep()
    assert events.booked == ["orphan|1.0"], "the same instant is not booked twice"
    PodWatchdog(fake.api(), leases, events, limits(False), clock).sweep()
    assert events.booked == ["orphan|1.0", "kill|0.0"], "the real kill books only what dry-run had not"


def test_a_failed_terminate_is_reported_not_hidden(env):
    fake, leases, events, _ = env
    fake.pods(pod("old1", "projectv-a", 500, 0.9))

    class Boom(RunPodApi):
        def terminate(self, pod_id):
            raise RuntimeError("provider says no")

    r = PodWatchdog(Boom("http://fake/v2", "k", client=fake.api()._http), leases, events, limits(), clock).sweep()
    assert not r.actions[0].done and r.actions[0].error == "provider says no" and not events.evidence


def test_report_json_shape_matches_the_java_dashboard_contract(env):
    fake, _, _, wd = env
    fake.pods(pod("old1", "projectv-a", 500, 0.9))
    j = wd(True).sweep().to_json()
    assert set(j) == {"at", "dryRun", "configured", "studioPods", "studioHourlyUsd", "actions", "warnings", "error"}
    assert j["at"] == "2026-11-19T12:00:00Z" and set(j["actions"][0]) == {"podId", "name", "reason", "done", "error"}
    assert set(limits().to_json()) == {"prefix", "maxRuntimeMinutes", "idleMinutes", "leaseGraceMinutes", "maxPods", "maxHourlyUsd", "dryRun"}


# ---- PodSelfStopTest

CREATED = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)


def test_wraps_the_job_with_an_absolute_deadline_and_idle_limit():
    argv = self_stop.wrap(["python", "-m", "worker", "--batch", "it's"], 120, 15, CREATED)
    assert argv[:2] == ["bash", "-c"] and argv[3] == "projectv-guard"
    assert argv[4:] == ["python", "-m", "worker", "--batch", "it's"], "the job's argv is passed through untouched"
    s = argv[2]
    assert f"DEADLINE={int(CREATED.timestamp()) + 7200};" in s and "IDLE=900;" in s
    assert 'runpodctl pod delete "$RUNPOD_POD_ID"' in s, "terminate first, not stop"
    assert s.index("pod delete") < s.index("pod stop"), "stop only as the last resort"
    assert "DELETE" in s and "/v2/pods/$RUNPOD_POD_ID" in s
    assert '"$@"\nrc=$?\nselfterm "job finished' in s, "the pod ends when the job ends"
    assert "nvidia-smi" in s and "%" not in s.replace("%FT%TZ", "").replace("%s", "").replace("%Y", ""), "no leftover format markers"


def test_exit_guard_holds_no_provider_key_and_only_ends_the_worker():
    argv = self_stop.wrap_exit(["python", "-c", "x"], 120, 15, CREATED)
    assert argv[:2] == ["bash", "-c"] and argv[4:] == ["python", "-c", "x"]
    s = argv[2]
    assert f"DEADLINE={int(CREATED.timestamp()) + 7200};" in s and "IDLE=900;" in s
    for forbidden in ["RUNPOD", "runpodctl", "curl", "API_KEY", "Authorization", "vast"]:
        assert forbidden not in s, f"no provider call or key in the in-pod guard: {forbidden}"
    assert 'kill -TERM "$JOB"' in s and 'wait "$JOB"' in s and 'exit "$rc"' in s and "nvidia-smi" in s
    with pytest.raises(ValueError):
        self_stop.wrap_exit([], 10, 5, CREATED)


def test_shell_string_quotes_safely():
    assert self_stop.as_shell_string(["bash", "-c", "echo 'hi'"]) == "'bash' '-c' 'echo '\"'\"'hi'\"'\"''"


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        self_stop.wrap([], 10, 5, CREATED)
    with pytest.raises(ValueError):
        self_stop.wrap(["x"], 0, 5, CREATED)
    with pytest.raises(ValueError):
        GpuPodLimits("", 1, 1, 1, 1, 1, True)


def test_the_guard_scripts_are_valid_shell(bash):
    import subprocess

    for argv in (self_stop.wrap(["true"], 5, 5, CREATED), self_stop.wrap_exit(["true"], 5, 5, CREATED)):
        r = subprocess.run([bash, "-n", "-c", argv[2]], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_json_dump_of_limits_is_serialisable():
    assert json.loads(json.dumps(limits().to_json()))["prefix"] == "projectv-"
