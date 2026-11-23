"""Ports of GpuBrokerTest, ProviderGateTest, CanaryFallbackTest and BootTimingsTest. Fake providers, wallet and ledger: no network,
no database, nothing is ever rented."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from studio.gpu.broker import support
from studio.gpu.broker.broker import AUTO, GpuBroker, NoGpuAvailableError, Pick, PodState, pick_key
from studio.gpu.broker.config import BrokerConfig
from studio.gpu.broker.provider import GpuProvider, Instance, InstanceStatus, PlanUnavailableError, Quote, State
from studio.gpu.broker.wallet import GpuWallet
from studio.gpu.leases import Lease, OpenLease
from studio.gpu.pods import GpuPodLimits, Pod

NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)


class Fake(GpuProvider):
    def __init__(self, id_):
        self.id = id_
        self.is_configured, self.stock, self.fail_create, self.hourly = True, True, False, 0.5
        self.created, self.terminated = [], []
        self.last_argv = self.last_env = None
        self.state = State.RUNNING
        self.create_then_fail = self.list_fails = self.list_fails_after_create = False
        self.pods = []
        self.status_error = self.terminate_error = None

    def provider(self): return self.id
    def configured(self): return self.is_configured
    def enabled_by_default(self): return True

    def list_pods(self):
        if self.list_fails:
            raise RuntimeError("list down")
        return self.pods

    def endpoint_for(self, instance_id, port): return f"https://{instance_id}:{port}"

    def terminate(self, pod_id):
        if self.terminate_error:
            raise RuntimeError(self.terminate_error)
        self.terminated.append(pod_id)

    def quote(self, spec):
        return Quote(self.id, True, "HIGH", "NVIDIA GeForce RTX 4090|SECURE", self.hourly, "") if self.stock else Quote.unavailable(self.id, "no instances")

    def create(self, req):
        if self.fail_create:
            raise RuntimeError("boom")
        self.created.append(req.name)
        self.last_argv, self.last_env = req.boot_argv, req.env
        pod_id = f"{self.id}-pod-{len(self.created)}"
        if self.create_then_fail:  # the provider made the pod, the reply timed out
            self.pods.append(Pod(pod_id, req.name, "RUNNING", self.hourly, NOW, NOW))
            self.list_fails = self.list_fails_after_create
            raise RuntimeError("read timed out")
        return Instance(self.id, pod_id, req.name, self.hourly, "4090", "https://x")

    def status(self, instance_id):
        if self.status_error:
            raise RuntimeError(self.status_error)
        return InstanceStatus(self.state, None, self.state.name)


class Leases:
    def __init__(self):
        self.map: dict[str, Lease] = {}
        self.released: list[str] = []
        self.shorts: list[OpenLease] = []
        self.attached_map: dict[str, list] = {}

    def open(self, provider, pod_id, name, purpose, short=None, hourly=None, gpu=None):
        self.map[pod_id] = Lease(pod_id, purpose, NOW, None)
        if short is not None:
            self.shorts.append(OpenLease(pod_id, provider, name, short, hourly, gpu, NOW))

    def open_for_shorts(self): return [s for s in self.shorts if s.pod_id not in self.released]
    def heartbeat(self, pod_id): pass
    def release(self, pod_id): self.released.append(pod_id)
    def find(self, pod_id): return self.map.get(pod_id)
    def attach(self, pod_id, short): self.attached_map.setdefault(pod_id, []).append(short)
    def detach(self, pod_id, short): pass
    def attached(self, pod_id): return self.attached_map.get(pod_id, [])


class Records:
    def __init__(self):
        self.decisions, self.costs, self.considered_log, self.hours_by_short = [], [], [], {}

    def decision(self, d, cap, considered, chosen, reason):
        self.decisions.append(f"{chosen}|{reason}")
        self.considered_log.append(considered)

    def cost(self, d, provider, hours, hourly_usd, note):
        self.costs.append(f"{provider}|{hours}|{hourly_usd}")
        self.hours_by_short[d] = self.hours_by_short.get(d, 0) + hours


class Tick:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class NoEvents:
    def terminated(self, *a): pass
    def last_orphan_booking(self, pod_id): return None
    def orphan_billed(self, *a): pass


def fixed_clock(t=NOW):
    return lambda: t


class Env:
    def __init__(self):
        self.runpod, self.jarvis, self.modal = Fake("runpod"), Fake("jarvislabs"), Fake("modal")
        self.leases, self.records = Leases(), Records()
        self.spent: dict[str, float] = {}
        self.caps = {"runpod": 5.0, "jarvislabs": 5.0, "modal": 0.0}
        self.short = uuid.uuid4()

    def cfg(self, daily=0.0, order=("runpod", "jarvislabs", "modal"), caps=None):
        return BrokerConfig(list(order), self.caps if caps is None else caps, 0, ["NVIDIA GeForce RTX 4090"], 24, 48, 80, 1.0, 8000, daily)

    def broker(self, clock=None, daily=0.0, spent_today=0.0, dry_run=True, **kw):
        cfg = self.cfg(daily)
        wallet = GpuWallet(cfg, lambda p: self.spent.get(p, 0.0), lambda: spent_today)
        limits = GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, dry_run)
        return GpuBroker([self.runpod, self.jarvis, self.modal], cfg, wallet, self.records, self.leases, NoEvents(), limits,
                         clock or fixed_clock(), **kw)

    def acquire(self, b, short=None, **kw):
        return b.acquire(short or self.short, "video.i2v.dream", "img", ["python", "worker.py"], {}, None, **kw)


@pytest.fixture
def e():
    return Env()


def test_worker_token_goes_into_the_env_and_the_address_for_token_providers(e):
    auth = support.WorkerAuth(b"test-secret", {"runpod"})
    b = e.broker(auth=auth)
    r = b.acquire(e.short, "video.i2v.dream", "img", ["python", "worker.py"], {"SV_PORT": "8000"}, None)
    name = r.instance.name
    assert e.runpod.last_env["SV_TOKEN"] == auth.token(name) and e.runpod.last_env["SV_PORT"] == "8000"
    assert r.instance.endpoint == f"https://worker:{auth.token(name)}@x"
    assert auth.token(name) != auth.token(name + "x")


def test_no_token_for_providers_that_did_not_ask_for_one(e):
    b = e.broker(auth=support.WorkerAuth(b"test-secret", {"vast"}))
    r = b.acquire(e.short, "video.i2v.dream", "img", ["python", "worker.py"], {"SV_PORT": "8000"}, None)
    assert "SV_TOKEN" not in e.runpod.last_env and r.instance.endpoint == "https://x"


def test_cheapest_live_price_wins_over_config_order(e):
    e.runpod.hourly, e.jarvis.hourly = 0.89, 0.45
    assert e.acquire(e.broker()).instance.provider == "jarvislabs" and not e.runpod.created


def test_cheapest_fails_create_then_next_cheapest(e):
    e.runpod.hourly, e.jarvis.hourly, e.jarvis.fail_create = 0.89, 0.45, True
    assert e.acquire(e.broker()).instance.provider == "runpod"


def test_equal_price_keeps_config_order_with_lease_decision_and_self_stop(e):
    r = e.acquire(e.broker())
    assert r.instance.provider == "runpod" and e.runpod.created[0].startswith("projectv-runpod-")
    assert e.runpod.last_argv[0] == "bash" and "worker.py" in e.runpod.last_argv  # wrapped by the in-pod guard
    assert r.instance.id in e.leases.map, "lease opened right after create"
    assert len(e.records.decisions) == 1 and e.records.decisions[0].startswith("runpod|") and not e.jarvis.created


def test_falls_through_no_stock_unconfigured_and_failed_create(e):
    e.runpod.stock, e.jarvis.fail_create, e.caps["modal"] = False, True, 5.0
    r = e.acquire(e.broker())
    assert r.instance.provider == "modal"
    c = e.records.considered_log[0]
    assert "no stock" in str(c[0]["skipped"]) and "create failed" in str(c[1]["skipped"]) and c[2]["chosen"] is True


def test_wallet_cap_blocks_and_zero_cap_means_blocked(e):
    e.spent["runpod"], e.caps["jarvislabs"] = 4.9, 0.0  # $0.10 left, a rental needs $0.50
    with pytest.raises(NoGpuAvailableError) as ex:
        e.acquire(e.broker())
    assert "wallet" in str(ex.value) and "blocked" in str(ex.value)
    assert not (e.runpod.created or e.jarvis.created or e.modal.created)
    assert len(e.records.decisions) == 1 and e.records.decisions[0].startswith("None|")


def test_second_acquire_reuses_the_open_rental(e):
    b = e.broker()
    a = e.acquire(b)
    assert e.acquire(b).instance.id == a.instance.id and len(e.runpod.created) == 1


def test_gone_pod_is_released_and_rented_again_instead_of_reused(e):
    b = e.broker()
    first = e.acquire(b)
    e.runpod.state = State.GONE  # the pod self-stopped (idle guard) or was killed
    second = e.acquire(b)
    assert first.instance.id != second.instance.id and len(e.runpod.created) == 2
    assert e.leases.released == [first.instance.id] and len(e.records.costs) == 1


def test_pod_state_tells_the_worker_open_gone_or_released(e):
    b = e.broker()
    assert b.pod_state(e.short) == PodState.NONE
    e.acquire(b)
    assert b.pod_state(e.short) == PodState.OPEN
    e.runpod.state = State.GONE  # deleted from outside while the clips step waits
    assert b.pod_state(e.short) == PodState.GONE and len(e.runpod.created) == 1, "asking never rents"
    b.release(e.short, "test")
    assert b.pod_state(e.short) == PodState.NONE


def test_two_shorts_share_one_pod_and_it_ends_only_when_the_last_one_lets_go(e):
    clock = Tick()
    b = e.broker(clock)
    a, c = uuid.uuid4(), uuid.uuid4()
    ra, rc = e.acquire(b, a), e.acquire(b, c)
    assert ra.instance.id == rc.instance.id and len(e.runpod.created) == 1 and len(b.open_short_ids()) == 2 and b.has_open_pod()
    clock.now = NOW + timedelta(minutes=30)
    b.release(a, "A done")
    assert not e.runpod.terminated and not e.records.costs, "B still uses it; nothing is booked until the pod ends"
    assert b.pod_state(a) == PodState.NONE and b.pod_state(c) == PodState.OPEN
    clock.now = NOW + timedelta(minutes=60)
    b.release(c, "C done")
    assert e.runpod.terminated == [ra.instance.id] and not b.has_open_pod() and len(e.records.costs) == 2
    ha, hc = e.records.hours_by_short[a], e.records.hours_by_short[c]
    assert ha + hc == pytest.approx(1.0) and ha == pytest.approx(1 / 3) and hc == pytest.approx(2 / 3)


def test_turning_the_shared_pod_off_ends_it_for_every_short(e):
    clock = Tick()
    b = e.broker(clock)
    a, c = uuid.uuid4(), uuid.uuid4()
    r = e.acquire(b, a)
    e.acquire(b, c)
    clock.now = NOW + timedelta(minutes=10)
    b.release_all("turned off by the owner")
    assert e.runpod.terminated == [r.instance.id] and b.pod_state(a) == PodState.NONE and b.pod_state(c) == PodState.NONE
    assert len(e.records.costs) == 2
    b.release_all("again")  # safe with no pod
    assert len(e.runpod.terminated) == 1


def test_when_the_shared_pod_is_gone_every_short_moves_to_the_replacement(e):
    b = e.broker()
    a, c = uuid.uuid4(), uuid.uuid4()
    first = e.acquire(b, a)
    e.acquire(b, c)
    e.runpod.state = State.GONE
    second = e.acquire(b, a)  # A's worker asks for a replacement
    assert first.instance.id != second.instance.id and len(e.runpod.created) == 2
    assert len(b.open_short_ids()) == 2 and b.open(c) is not None, "C did not lose its place"
    assert e.leases.released == [first.instance.id] and len(e.records.costs) == 2


def test_only_one_shared_pod_is_kept_after_a_restart_and_extras_are_ended_and_booked(e):
    other = uuid.uuid4()
    e.leases.shorts.append(OpenLease("runpod-pod-1", "runpod", "projectv-runpod-a", e.short, 0.5, "4090", NOW))
    e.leases.shorts.append(OpenLease("runpod-pod-2", "runpod", "projectv-runpod-b", other, 0.5, "4090", NOW))
    b = e.broker(fixed_clock(NOW + timedelta(hours=1)))
    b.recover()
    assert b.open(e.short) is not None and b.open(other) is None, "the oldest pod is the shared one"
    assert e.runpod.terminated == ["runpod-pod-2"]
    assert e.records.hours_by_short[other] == pytest.approx(1.0), "booked to the Short that rented it"


def test_unreadable_status_keeps_the_rental(e):
    e.runpod.status_error = "api blip"
    b = e.broker()
    first = e.acquire(b)
    assert e.acquire(b).instance.id == first.instance.id and len(e.runpod.created) == 1


def test_rentals_come_back_after_a_restart_so_release_still_terminates_and_books(e):
    r = e.acquire(e.broker())
    restarted = e.broker(fixed_clock(NOW + timedelta(hours=1)))  # same leases, empty memory
    assert restarted.open(e.short) is None
    restarted.recover()
    assert restarted.open(e.short).instance.id == r.instance.id
    assert e.acquire(restarted).instance.id == r.instance.id and len(e.runpod.created) == 1, "reused, not a second pod"
    restarted.release(e.short, "Short done")
    assert e.runpod.terminated == [r.instance.id] and e.records.costs == ["runpod|1.0|0.5"]


def test_create_timeout_adopts_the_pod_the_provider_made_instead_of_renting_another(e):
    e.runpod.create_then_fail = True
    r = e.acquire(e.broker())
    assert r.instance.id == "runpod-pod-1" and not e.jarvis.created and len(e.runpod.created) == 1
    assert "runpod-pod-1" in e.leases.map, "adopted pod is leased"


def test_create_failure_with_unreadable_pod_list_stops_instead_of_renting_elsewhere(e):
    e.runpod.create_then_fail = e.runpod.list_fails_after_create = True
    with pytest.raises(NoGpuAvailableError):
        e.acquire(e.broker())
    assert not (e.jarvis.created or e.modal.created)


def test_release_terminates_books_cost_and_releases_lease(e):
    clock = Tick()
    b = e.broker(clock)
    r = e.acquire(b)
    clock.now = NOW + timedelta(seconds=1800)
    b.release(e.short, "Short done")
    assert e.runpod.terminated == [r.instance.id] and e.leases.released == [r.instance.id]
    assert e.records.costs == ["runpod|0.5|0.5"] and b.open(e.short) is None
    b.release(e.short, "twice is harmless")
    assert len(e.runpod.terminated) == 1


def test_release_of_a_pod_already_gone_books_only_up_to_its_last_heartbeat(e):
    clock = Tick()
    b = e.broker(clock)
    r = e.acquire(b)
    e.runpod.state = State.GONE  # deleted from outside 10 min in; the step only failed at 30 min
    e.leases.map[r.instance.id] = Lease(r.instance.id, "p", NOW + timedelta(seconds=600), None)
    clock.now = NOW + timedelta(seconds=1800)
    b.release(e.short, "step failed")
    assert len(e.records.costs) == 1 and float(e.records.costs[0].split("|")[1]) == pytest.approx(600 / 3600.0)


def test_terminate_failure_still_releases_lease_so_watchdog_finishes_the_job(e):
    e.runpod.terminate_error = "api down"
    b = e.broker()
    r = e.acquire(b)
    b.release(e.short, "cancelled")
    assert e.leases.released == [r.instance.id] and len(e.records.costs) == 1


class Multi(Fake):
    """A provider offering several GPUs, like RunPod: 4090 cheap, A6000 mid, 5090 dear."""

    def quotes(self, spec):
        return [Quote(self.id, True, "HIGH", "4090|COMMUNITY", 0.34, ""), Quote(self.id, True, "LOW", "A6000|SECURE", 0.49, ""),
                Quote(self.id, True, "HIGH", "5090|SECURE", 0.89, "")]

    def quote(self, spec):
        return self.quotes(spec)[0]


def test_options_are_sorted_by_price_available_first_with_reasons_for_the_rest(e):
    e.runpod = Multi("runpod")
    e.jarvis.is_configured = False
    o = e.broker().options()
    assert [x.key for x in o if x.available] == ["runpod|4090|COMMUNITY", "runpod|A6000|SECURE", "runpod|5090|SECURE"]
    blocked = next(x for x in o if x.provider == "jarvislabs")
    assert not blocked.available and "not configured" in blocked.why


def test_manual_pick_rents_exactly_that_option_even_if_dearer(e):
    e.runpod = Multi("runpod")
    r = e.broker().acquire(e.short, "video.i2v.dream", "img", ["w"], {}, None, Pick("manual", "runpod|5090|SECURE"))
    assert r.instance.provider == "runpod" and "your manual pick" in e.records.decisions[0]


def test_manual_pick_that_is_gone_fails_to_the_owner_without_falling_back(e):
    b = e.broker()
    with pytest.raises(NoGpuAvailableError, match="your pick"):
        b.acquire(e.short, "video.i2v.dream", "img", ["w"], {}, None, Pick("manual", "runpod|nope|X"))
    assert not (e.runpod.created or e.jarvis.created), "no silent fallback after a manual pick"
    with pytest.raises(NoGpuAvailableError):
        b.acquire(uuid.uuid4(), "video.i2v.dream", "img", ["w"], {}, None, Pick("manual", None))


class Learned:
    def __init__(self, key): self.key = key
    def learned_key(self): return self.key
    def record_choice(self, *a): pass


def test_auto_follows_a_learned_preference_within_the_premium_limit(e):
    e.runpod = Multi("runpod")
    e.broker(prefs=Learned("runpod|A6000|SECURE")).acquire(e.short, "video.i2v.dream", "img", ["w"], {}, None)
    assert "learned from your last picks" in e.records.decisions[0]
    chosen = next(r for r in e.records.considered_log[0] if r.get("chosen"))
    assert f"{chosen['provider']}|{chosen['gpu']}" == "runpod|A6000|SECURE"
    # 5090 at $0.89 is 2.6x the cheapest ($0.34): over the 1.5x premium limit, so it would not be followed
    e.runpod.created.clear()
    e.records.decisions.clear()
    e.leases = Leases()
    e.broker(prefs=Learned("runpod|5090|SECURE")).acquire(uuid.uuid4(), "video.i2v.dream", "img", ["w"], {}, None)
    assert "cheapest eligible" in e.records.decisions[0]


def test_daily_cap_refuses_a_rental_past_it_with_a_clear_message(e):
    b = e.broker(daily=3.50, spent_today=3.20, dry_run=False)  # $0.30 left, a rental needs about $0.50
    with pytest.raises(NoGpuAvailableError) as ex:
        e.acquire(b)
    msg = str(ex.value)
    assert msg.startswith("Daily GPU cap reached") and "$3.20 spent today (UTC) of the $3.50 cap" in msg and "waits for you" in msg
    assert not (e.runpod.created or e.jarvis.created) and len(e.records.decisions) == 1, "the refusal is a routing decision too (rule 6)"


def test_under_the_daily_cap_it_rents_and_today_counts_the_running_pod(e):
    clock = Tick()
    b = e.broker(clock, daily=3.50, spent_today=1.00, dry_run=False)
    e.acquire(b)
    clock.now = NOW + timedelta(hours=1)  # one hour at $0.50/h
    t = b.today()
    assert (t["spentUsd"], t["runningUsd"], t["capUsd"]) == (1.50, 0.50, 3.50)


def test_a_pod_that_never_became_ready_is_retired_booked_and_replaced(e):
    b = e.broker(daily=3.50, dry_run=False)
    other = uuid.uuid4()
    first = e.acquire(b)
    e.acquire(b, other)
    assert not b.retire_unready("https://somewhere-else", "late"), "only the pod the worker waited on"
    assert b.retire_unready("https://x/", "not ready after 30 min")
    assert e.runpod.terminated == [first.instance.id] and e.leases.released == [first.instance.id] and e.records.costs
    second = e.acquire(b)
    assert first.instance.id != second.instance.id and b.open(other) is not None, "the other Short moved to the new pod"


def test_the_daily_cap_uses_this_shorts_own_estimate_and_still_refuses_when_it_does_not_fit(e):
    e.runpod.hourly, e.jarvis.stock = 0.69, False  # 21 Nov 2026: an A6000 at $0.69/h with $0.42 left today
    b = e.broker(daily=3.50, spent_today=3.08, dry_run=False)
    with pytest.raises(NoGpuAvailableError):
        e.acquire(b)  # the fixed 1 h fallback ($0.69) does not fit
    with pytest.raises(NoGpuAvailableError, match=r"this rental needs about \$0\.48"):
        e.acquire(b, pick=AUTO, expected_hours=0.70)
    assert not e.runpod.created
    assert e.acquire(b, pick=AUTO, expected_hours=0.39).instance.provider == "runpod"  # 1 SVI scene, 4 clips: about $0.27


class Market(Fake):
    """A marketplace: one quote per live offer, each with its own offer id in the key (Vast's shape)."""

    def __init__(self, id_):
        super().__init__(id_)
        self.offers = []  # "gpuName|offerId|price"

    def quotes(self, spec):
        return [Quote(self.id, True, "ON-DEMAND", f"{a[0]}|{a[1]}|US|m1", float(a[2]), "") for a in (o.split("|") for o in self.offers)]

    def quote(self, spec):
        return self.quotes(spec)[0]

    def create(self, req):
        self.created.append(req.name)
        return Instance(self.id, f"{self.id}-pod-{len(self.created)}", req.name, req.quote.hourly_usd, req.quote.gpu, "https://x")


def test_marketplace_options_are_gpu_types_not_offer_ids(e):
    vast = Market("vast")
    vast.offers += ["RTX 4090|111|0.40", "RTX 4090|222|0.35", "RTX 5090|333|0.80"]
    e.runpod.stock = False
    caps = {"runpod": 5.0, "vast": 5.0}
    cfg = e.cfg(order=("runpod", "vast"), caps=caps)
    b = GpuBroker([e.runpod, vast], cfg, GpuWallet(cfg, lambda p: 0.0), e.records, e.leases, NoEvents(),
                  GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, True), fixed_clock())
    assert [o.key for o in b.options() if o.available] == ["vast|RTX 4090", "vast|RTX 5090"]
    assert b.options()[0].hourly_usd == 0.35, "the cheapest offer of the type"
    # the offer the owner once saw is gone: a type pick still rents the cheapest live offer of it
    r = b.acquire(e.short, "video.i2v.dream", "img", ["w"], {}, None, Pick("manual", "vast|RTX 4090"))
    assert r.instance.provider == "vast" and r.instance.hourly_usd == 0.35
    # an old stored pick naming a vanished offer id falls back to its GPU type
    b.release(e.short, "test")
    vast.offers.remove("RTX 4090|222|0.35")
    old = b.acquire(e.short, "video.i2v.dream", "img", ["w"], {}, None, Pick("manual", "vast|RTX 4090|999|DE|m7"))
    assert old.instance.hourly_usd == 0.40
    assert pick_key("vast|RTX 4090|999|DE|m7") == "vast|RTX 4090"
    assert pick_key("runpod|4090|SECURE") == "runpod|4090|SECURE", "other providers' keys are unchanged"


# ---- ProviderGateTest: unproven providers are never searched; the canary and the preflight


class Gate:
    def __init__(self):
        self.runpod, self.jarvis = Fake("runpod"), Fake("jarvislabs")
        self.runpod.hourly, self.jarvis.hourly = 0.80, 0.30  # jarvis cheaper, but unproven
        self.leases, self.records = Leases(), Records()
        self.states = support.MemoryStates("runpod")

    def broker(self):
        cfg = BrokerConfig(["runpod", "jarvislabs"], {"runpod": 5.0, "jarvislabs": 5.0}, 0, ["NVIDIA GeForce RTX 4090"], 24, 48, 80, 1.0, 8000)
        return GpuBroker([self.runpod, self.jarvis], cfg, GpuWallet(cfg, lambda p: 0.0), self.records, self.leases, NoEvents(),
                         GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, True), lambda: datetime.now(UTC), states=self.states)

    def acquire(self, b):
        return b.acquire(uuid.uuid4(), "video.i2v.dream", "img", ["python", "w.py"], {}, None)

    def canary(self, b, health=lambda ep: None):
        return b.canary("jarvislabs", "img", ["python", "w.py"], {}, None, health, 1, 1)


@pytest.fixture
def g():
    return Gate()


def test_auto_skips_an_unproven_provider_even_when_it_is_cheaper(g):
    b = g.broker()
    assert g.acquire(b).instance.provider == "runpod" and not g.jarvis.created
    assert any(o.provider == "jarvislabs" and not o.available and "not proven" in o.why for o in b.options())


def test_auto_still_falls_through_to_the_next_cheapest_when_a_create_fails(g):
    g.states.mark_proven("jarvislabs", "test")
    g.jarvis.fail_create = True
    assert g.acquire(g.broker()).instance.provider == "runpod" and len(g.runpod.created) == 1


def test_canary_success_terminates_books_and_marks_proven(g):
    b = g.broker()
    r = g.canary(b)
    assert r["proven"] is True, r
    assert g.states.proven("jarvislabs") and g.jarvis.terminated == ["jarvislabs-pod-1"] and len(g.records.costs) == 1
    assert "jarvislabs-pod-1" in g.leases.released and b.provider_status("jarvislabs")["proven"] is True
    assert g.acquire(b).instance.provider == "jarvislabs", "now cheapest and searched"


def test_canary_with_an_unhealthy_worker_stays_unproven_but_still_terminates(g):
    r = g.canary(g.broker(), lambda ep: "HTTP 503")
    assert r["proven"] is False and not g.states.proven("jarvislabs") and len(g.jarvis.terminated) == 1 and "HTTP 503" in r["result"]


def test_canary_whose_create_fails_rents_nothing_and_stays_unproven(g):
    g.jarvis.fail_create = True
    r = g.canary(g.broker())
    assert r["proven"] is False and not g.jarvis.terminated and not g.records.costs, "nothing was rented, nothing is booked"


def test_canary_adopts_a_pod_whose_create_reply_was_lost(g):
    g.jarvis.create_then_fail = True
    r = g.canary(g.broker())
    assert r["proven"] is False and len(g.jarvis.terminated) == 1, "the pod the provider did create is terminated"


def test_failed_preflight_means_no_canary_and_nothing_rented(g):
    g.jarvis.stock = False  # the default preflight needs a live quote
    r = g.canary(g.broker())
    assert r["proven"] is False and not g.jarvis.created and g.states.get("jarvislabs").preflight_ok is False


def test_canary_is_refused_while_a_pod_is_on(g):
    b = g.broker()
    g.acquire(b)
    assert g.canary(b)["result"].startswith("not run") and not g.jarvis.created


def test_status_shows_configured_preflight_proven(g):
    b = g.broker()
    b.preflight("jarvislabs", "img", ["python"], {}, None)
    s = b.provider_status("jarvislabs")
    assert (s["configured"], s["preflightOk"], s["proven"], s["inAutoSearch"]) == (True, True, False, False)
    assert b.provider_status("runpod")["inAutoSearch"] is True


# ---- CanaryFallbackTest: the canary moves to the next cheapest plan only when a plan is out of stock


class Plans(Fake):
    def __init__(self):
        super().__init__("jarvislabs")
        self.out_of_stock, self.other_failure, self.tried = [], None, []

    def quotes(self, spec):
        return [Quote(self.id, True, "HIGH", n, usd, "") for n, usd in (("L40S", 1.2), ("A40", 0.5), ("A100", 2.0), ("H100", 4.0))]

    def create(self, req):
        self.tried.append(req.quote.gpu)
        if self.other_failure:
            raise self.other_failure
        if req.quote.gpu in self.out_of_stock:
            raise PlanUnavailableError("This GPU plan is temporarily not available")
        return super().create(req)


def run_canary(jarvis, states, records, max_usd):
    cfg = BrokerConfig(["jarvislabs"], {"jarvislabs": 5.0}, 0, ["NVIDIA GeForce RTX 4090"], 24, 48, 80, 1.0, 8000)
    b = GpuBroker([jarvis], cfg, GpuWallet(cfg, lambda p: 0.0), records, Leases(), NoEvents(),
                  GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, True), lambda: datetime.now(UTC), states=states)
    return b.canary("jarvislabs", "img", ["python", "w.py"], {}, None, lambda ep: None, 1, 1, max_usd)


@pytest.fixture
def plans():
    return Plans(), support.MemoryStates("runpod"), Records()


def test_first_plan_unavailable_then_the_next_cheapest_succeeds(plans):
    j, s, r = plans
    j.out_of_stock.append("A40")
    out = run_canary(j, s, r, 0.60)
    assert out["proven"] is True, out
    assert j.tried == ["A40", "L40S"] and len(out["attempts"]) == 2 and len(j.terminated) == 1


def test_all_three_unavailable_stops_at_three_and_rents_nothing(plans):
    j, s, r = plans
    j.out_of_stock += ["A40", "L40S", "A100", "H100"]
    out = run_canary(j, s, r, 5.0)
    assert out["proven"] is False and j.tried == ["A40", "L40S", "A100"] and len(out["attempts"]) == 3
    assert not j.terminated and not r.costs and "unavailable" in out["result"]


def test_a_credit_error_does_not_fall_through(plans):
    j, s, r = plans
    j.other_failure = RuntimeError("E2E refused the call (HTTP 402): insufficient credit")
    out = run_canary(j, s, r, 5.0)
    assert out["proven"] is False and j.tried == ["A40"] and not s.proven("jarvislabs")


def test_the_per_canary_cost_cap_refuses_an_expensive_plan(plans):
    j, s, r = plans
    out = run_canary(j, s, r, 0.01)  # even the cheapest plan costs more than a cent for the run
    assert out["proven"] is False and not j.tried and "over the approved cap" in out["result"]


def test_a_fallback_plan_over_the_cap_is_not_rented(plans):
    j, s, r = plans
    j.out_of_stock.append("A40")
    out = run_canary(j, s, r, 0.03)  # A40 $0.5/h and L40S $1.2/h for a 121 s run: cap between the two costs
    assert out["proven"] is False and j.tried == ["A40"], "L40S is over the cap, so it is never created"


# ---- BootTimingsTest


def worker(start_after_rent):
    t0 = NOW.timestamp() + start_after_rent
    return {"started_at": t0, "marks": [
        {"phase": "worker_started", "at": t0, "since_start_s": 0.0}, {"phase": "deps_installed", "at": t0 + 60, "since_start_s": 60.0},
        {"phase": "weights_done", "at": t0 + 900, "since_start_s": 900.0}, {"phase": "ready", "at": t0 + 960, "since_start_s": 960.0}],
        "downloads": [{"name": "unet.safetensors", "gb": 14.29, "seconds": 126.0, "mb_per_s": 113.4, "source": "huggingface"}]}


def test_summary_joins_rent_running_and_the_worker_timeline():
    s = support.summary(support.TimingRow("pod1", "vast", "img", NOW, NOW + timedelta(seconds=90), worker(300)))
    steps = s["steps"]
    assert [m["phase"] for m in steps] == ["rented", "provider_running", "container_started", "deps_installed", "weights_done", "ready"]
    assert steps[1]["secondsSinceRent"] == 90.0
    assert steps[2]["secondsSinceRent"] == 300.0, "image pull + container start = the gap before the worker's first mark"
    assert s["totalSeconds"] == 1260.0 and len(s["downloads"]) == 1 and s["rentedAt"] == "2026-11-20T12:00:00Z"


def test_summary_without_a_worker_report_has_no_total():
    s = support.summary(support.TimingRow("pod1", "runpod", "img", NOW))
    assert s["totalSeconds"] is None and len(s["steps"]) == 1


def test_first_running_and_first_worker_report_win():
    t = support.MemoryTimings()
    t.rented("p", "runpod", "img", NOW)
    t.running("p", NOW + timedelta(seconds=40))
    t.running("p", NOW + timedelta(seconds=400))
    t.worker_report("p", worker(100))
    t.worker_report("p", worker(999))
    row = t.get("p")
    assert row.running_at == NOW + timedelta(seconds=40) and row.worker["started_at"] == NOW.timestamp() + 100


def test_broker_records_rent_then_running_then_the_workers_report():
    timings = support.MemoryTimings()
    fake = Fake("runpod")
    cfg = BrokerConfig(["runpod"], {"runpod": 5.0}, 0, ["NVIDIA GeForce RTX 4090"], 24, 48, 80, 1.0, 8000)
    b = GpuBroker([fake], cfg, GpuWallet(cfg, lambda p: 0.0), Records(), Leases(), NoEvents(),
                  GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, True), fixed_clock(), boot_timings=timings)
    short = uuid.uuid4()
    r = b.acquire(short, "video.i2v.dream", "my/image:1", ["python", "w.py"], {}, None)
    assert timings.get(r.instance.id).rented_at == NOW and timings.get(r.instance.id).image == "my/image:1"
    assert b.boot_timing(short)["totalSeconds"] is None, "no worker report yet"
    b.pod_state(short)  # the heartbeat path reads the provider status: first RUNNING sighting
    assert timings.get(r.instance.id).running_at is not None
    b.report_boot(short, worker(120))
    assert b.boot_timing(short)["totalSeconds"] == 1080.0
    b.report_boot(uuid.uuid4(), worker(1))  # a Short with no rental: ignored, no error
    assert len(timings.recent(5)) == 1
