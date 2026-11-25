"""Community-cloud abort-and-retry rule: the pure decision (exhaustive) and the broker I/O with fakes. No network, nothing is rented."""

import uuid

import pytest

from studio.gpu.broker import community
from studio.gpu.broker.broker import GpuBroker, NoGpuAvailableError
from studio.gpu.broker.community import AttemptLimits, AttemptState, Verdict, decide, normalise_preference, parse_cuda
from studio.gpu.broker.provider import Quote
from studio.gpu.broker.wallet import GpuWallet
from studio.gpu.pods import GpuPodLimits
from studio.tests.test_gpu_broker import Env, NoEvents, Tick

L = AttemptLimits()  # 540 s boot, 360 s stall
BOOT = "[pod-worker 10:00:01] listening on [::]:8000"
S0 = AttemptState()


def v(elapsed, log="", cuda=None, state=S0, limits=L):
    return decide(state, elapsed, log, cuda, limits)


# ------------------------------------------------------------------ the pure function

def test_cuda_false_aborts_at_once_even_early_or_without_boot_line():
    assert v(0, "", False).verdict is Verdict.ABORT
    assert v(5, BOOT, False).verdict is Verdict.ABORT
    assert v(9999, BOOT, False).verdict is Verdict.ABORT


def test_boot_line_and_cuda_true_keeps():
    assert v(120, BOOT, True).verdict is Verdict.KEEP
    assert v(539, BOOT, True).verdict is Verdict.KEEP
    assert v(900, BOOT, True).verdict is Verdict.KEEP  # a good pod is never aborted late


def test_cuda_true_without_a_boot_line_is_not_enough():
    assert v(10, "Pulling fs layer", True).verdict is Verdict.WAIT
    assert v(540, "Pulling fs layer", True).verdict is Verdict.ABORT


def test_waits_before_the_limit_with_nothing_seen():
    assert v(0).verdict is Verdict.WAIT
    assert v(539.9).verdict is Verdict.WAIT


def test_no_boot_line_by_nine_minutes_aborts():
    d = v(540)
    assert d.verdict is Verdict.ABORT and "no boot log line" in d.reason
    assert v(541).verdict is Verdict.ABORT


def test_boot_line_but_cuda_never_reported_waits_then_aborts():
    assert v(300, BOOT, None).verdict is Verdict.WAIT
    d = v(540, BOOT, None)
    assert d.verdict is Verdict.ABORT and "never reported" in d.reason


def test_pull_with_progress_keeps_waiting():
    s = decide(S0, 30, "Pulling a", None, L).state
    s = decide(s, 200, "Pulling a\nPulling b", None, L).state  # grew at 200
    d = decide(s, 500, "Pulling a\nPulling b", None, L)  # 300 s since growth: under the 360 s stall
    assert d.verdict is Verdict.WAIT


def test_pull_with_no_progress_aborts_before_the_boot_timeout():
    s = decide(S0, 30, "Pulling a", None, L).state
    d = decide(s, 30 + 360, "Pulling a", None, L)
    assert d.verdict is Verdict.ABORT and "no progress" in d.reason and 390 < 540


def test_a_silent_pod_is_not_a_stalled_pull():
    assert v(400).verdict is Verdict.WAIT  # no output at all: only the boot timeout applies


def test_stall_rule_does_not_apply_once_booted():
    s = decide(S0, 30, BOOT, None, L).state
    assert decide(s, 450, BOOT, None, L).verdict is Verdict.WAIT


def test_negative_elapsed_is_treated_as_zero_and_state_is_immutable():
    assert v(-5).verdict is Verdict.WAIT
    s = AttemptState(3, 1.0)
    decide(s, 10, "abcdef", None, L)
    assert s == AttemptState(3, 1.0)


def test_state_tracks_growth_only():
    s = decide(S0, 10, "abc", None, L).state
    assert s == AttemptState(3, 10)
    assert decide(s, 20, "abc", None, L).state == s
    assert decide(s, 20, "abcd", None, L).state == AttemptState(4, 20)


def test_parse_cuda_takes_the_last_answer():
    assert parse_cuda("") is None and parse_cuda("x") is None
    assert parse_cuda("cuda_available=True") is True
    assert parse_cuda("cuda_available=True\n[pod-worker] cuda_available=False (X)") is False


def test_flag_values():
    assert normalise_preference(None) == "secure" and normalise_preference("") == "secure"
    assert normalise_preference("Community_Then_Secure") == "community_then_secure"
    assert normalise_preference("community") == "secure"  # anything unknown is today's behaviour


def test_is_community():
    assert community.is_community("NVIDIA GeForce RTX 4090|COMMUNITY") and not community.is_community("x|SECURE")
    assert not community.is_community(None) and not community.is_community("COMMUNITY")


def test_limits_validate():
    with pytest.raises(ValueError):
        AttemptLimits(boot_timeout_s=0)
    with pytest.raises(ValueError):
        AttemptLimits(max_community_attempts=0)


# ------------------------------------------------------------------ the broker with fakes

class Probe:
    """Per pod id: a list of log texts returned look by look (the last one repeats); an Exception is raised."""

    def __init__(self, script):
        self.script, self.looks = script, {}

    def observe(self, pod_id):
        n = self.looks.get(pod_id, 0)
        self.looks[pod_id] = n + 1
        seq = self.script.get(pod_id) or [""]
        item = seq[min(n, len(seq) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


GOOD = BOOT + "\n[pod-worker 10:00:02] cuda_available=True"
BAD_CUDA = BOOT + "\n[pod-worker 10:00:02] cuda_available=False"
COMM, SEC = "NVIDIA GeForce RTX 4090|COMMUNITY", "NVIDIA GeForce RTX 4090|SECURE"


class Setup:
    def __init__(self, script, quotes=((COMM, 0.34), (SEC, 0.89)), daily=0.0, caps=None, prefer="community_then_secure"):
        self.e = Env()
        self.clock = Tick()
        self.e.runpod.quotes = lambda spec: [Quote("runpod", True, "HIGH", g, p, "") for g, p in quotes]
        self.e.runpod.quote = lambda spec: self.e.runpod.quotes(spec)[0]
        self.created_gpus = []
        orig = self.e.runpod.create

        def create(req):
            self.created_gpus.append(req.quote.gpu)
            inst = orig(req)
            return type(inst)(inst.provider, inst.id, inst.name, req.quote.hourly_usd, inst.gpu, inst.endpoint)

        self.e.runpod.create = create
        self.probe = Probe(script)
        self.sleeps = []

        def sleep(s):
            self.sleeps.append(s)
            from datetime import timedelta
            self.clock.now += timedelta(seconds=s)

        cfg = self.e.cfg(daily, order=("runpod",), caps={"runpod": 5.0} if caps is None else caps)
        self.spent = lambda p: sum(float(c.split("|")[1]) * float(c.split("|")[2]) for c in self.e.records.costs if c.startswith(p))
        wallet = GpuWallet(cfg, self.spent, lambda: 0.0)
        limits = GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, True)
        self.broker = GpuBroker([self.e.runpod], cfg, wallet, self.e.records, self.e.leases, NoEvents(), limits, self.clock,
                                cloud_preference=prefer, boot_probe=self.probe, sleep=sleep)

    def acquire(self):
        return self.e.acquire(self.broker)


def pods(n):
    return [f"runpod-pod-{i}" for i in range(1, n + 1)]


def test_good_community_host_is_kept_first_try():
    s = Setup({"runpod-pod-1": [GOOD]})
    r = s.acquire()
    assert r.instance.id == "runpod-pod-1" and s.created_gpus == [COMM]
    assert s.e.runpod.terminated == [] and s.e.leases.released == []
    assert s.e.records.decisions[0].startswith("runpod|")


def test_cuda_false_aborts_then_second_community_host_is_kept():
    s = Setup({"runpod-pod-1": [BAD_CUDA], "runpod-pod-2": [GOOD]})
    r = s.acquire()
    assert r.instance.id == "runpod-pod-2" and s.created_gpus == [COMM, COMM]
    assert s.e.runpod.terminated == ["runpod-pod-1"] and s.e.leases.released == ["runpod-pod-1"]
    assert len(s.e.records.costs) == 1, "the failed attempt is booked"


def test_no_boot_line_in_nine_minutes_aborts_after_exactly_that_wait_then_retries():
    s = Setup({"runpod-pod-1": [""], "runpod-pod-2": [GOOD]})
    r = s.acquire()
    assert r.instance.id == "runpod-pod-2"
    assert sum(s.sleeps) == 540 and s.e.runpod.terminated == ["runpod-pod-1"]
    cost = s.e.records.costs[0].split("|")
    assert float(cost[1]) == pytest.approx(540 / 3600) and float(cost[2]) == 0.34


def test_stuck_pull_aborts_early():
    s = Setup({"runpod-pod-1": ["Pulling fs layer"], "runpod-pod-2": [GOOD]})
    s.acquire()
    assert sum(s.sleeps) < 540 and s.e.runpod.terminated == ["runpod-pod-1"]


def test_two_bad_community_hosts_fall_back_to_secure_without_probing_it():
    s = Setup({"runpod-pod-1": [BAD_CUDA], "runpod-pod-2": [""], "runpod-pod-3": [BAD_CUDA]})  # pod 3 must never be judged
    r = s.acquire()
    assert s.created_gpus == [COMM, COMM, SEC] and r.instance.id == "runpod-pod-3"
    assert s.e.runpod.terminated == ["runpod-pod-1", "runpod-pod-2"]
    assert "runpod-pod-3" not in s.probe.looks
    assert len(s.e.records.costs) == 2 and sorted(s.e.leases.map) == pods(3)  # every attempt had a lease


def test_never_a_third_community_try_even_when_secure_is_absent():
    s = Setup({"runpod-pod-1": [BAD_CUDA], "runpod-pod-2": [BAD_CUDA]}, quotes=((COMM, 0.34),))
    with pytest.raises(NoGpuAvailableError):
        s.acquire()
    assert s.created_gpus == [COMM, COMM] and s.e.runpod.terminated == ["runpod-pod-1", "runpod-pod-2"]
    assert s.e.leases.released == ["runpod-pod-1", "runpod-pod-2"]


def test_second_attempt_uses_another_offer_when_there_is_one():
    other = "NVIDIA GeForce RTX 5090|COMMUNITY"
    s = Setup({"runpod-pod-1": [BAD_CUDA], "runpod-pod-2": [GOOD]}, quotes=((COMM, 0.34), (other, 0.50), (SEC, 0.89)))
    s.acquire()
    assert s.created_gpus == [COMM, other]


def test_at_most_one_pod_alive_at_any_time():
    s = Setup({"runpod-pod-1": [BAD_CUDA], "runpod-pod-2": [BAD_CUDA], "runpod-pod-3": [GOOD]})
    alive = set()
    orig_create, orig_term = s.e.runpod.create, s.e.runpod.terminate
    peak = [0]

    def create(req):
        inst = orig_create(req)
        alive.add(inst.id)
        peak[0] = max(peak[0], len(alive))
        return inst

    def terminate(pod_id):
        orig_term(pod_id)
        alive.discard(pod_id)

    s.e.runpod.create, s.e.runpod.terminate = create, terminate
    s.acquire()
    assert peak[0] == 1 and alive == {"runpod-pod-3"}


def test_cost_cap_counts_failed_attempts_and_blocks_the_retry():
    # cap leaves room for the first attempt's estimate (1 h x 0.34) but not for a second once the failed one is booked
    s = Setup({"runpod-pod-1": [""], "runpod-pod-2": [GOOD]}, quotes=((COMM, 0.34),), caps={"runpod": 0.38})
    with pytest.raises(NoGpuAvailableError) as ex:
        s.acquire()
    assert s.created_gpus == [COMM] and "retry not started" in str(ex.value)
    assert s.e.runpod.terminated == ["runpod-pod-1"] and len(s.e.records.costs) == 1


def test_terminate_failure_stops_renting_anything_else():
    s = Setup({"runpod-pod-1": [BAD_CUDA], "runpod-pod-2": [GOOD]})
    s.e.runpod.terminate_error = "api down"
    with pytest.raises(NoGpuAvailableError):
        s.acquire()
    assert s.created_gpus == [COMM], "no second pod while the first could not be ended"
    assert s.e.leases.released == ["runpod-pod-1"], "lease released so the watchdog ends it"


def test_unreadable_log_still_ends_at_the_timeout():
    s = Setup({"runpod-pod-1": [RuntimeError("log api down")], "runpod-pod-2": [GOOD]})
    r = s.acquire()
    assert r.instance.id == "runpod-pod-2" and sum(s.sleeps) == 540


def test_exception_inside_the_wait_still_terminates_the_pod():
    s = Setup({"runpod-pod-1": [""]})

    def boom(_):
        raise KeyboardInterrupt

    s.broker._sleep = boom
    with pytest.raises(KeyboardInterrupt):
        s.acquire()
    assert s.e.runpod.terminated == ["runpod-pod-1"] and s.e.leases.released == ["runpod-pod-1"]
    assert not s.broker.has_open_pod()


def test_default_secure_never_probes_and_changes_nothing():
    s = Setup({"runpod-pod-1": [BAD_CUDA]}, prefer="secure")
    r = s.acquire()
    assert r.instance.id == "runpod-pod-1" and s.probe.looks == {} and s.sleeps == [] and s.e.runpod.terminated == []


def test_flag_without_a_probe_stays_secure():
    s = Setup({})
    b = GpuBroker([s.e.runpod], s.e.cfg(0, order=("runpod",)), GpuWallet(s.e.cfg(0), lambda p: 0.0, lambda: 0.0), s.e.records, s.e.leases,
                  NoEvents(), GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, True), s.clock, cloud_preference="community_then_secure")
    assert b._community is False


def test_second_acquire_reuses_the_kept_pod_without_new_attempts():
    s = Setup({"runpod-pod-1": [GOOD]})
    s.acquire()
    s.e.acquire(s.broker, short=uuid.uuid4())
    assert s.created_gpus == [COMM]
