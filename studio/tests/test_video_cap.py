"""Per-video spend cap and cost view (owner, 25 Nov 2026). Scratch Postgres with the real schema, fake providers and fake broker
pieces: nothing is rented, nothing is paid."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from studio.config import REPO_ROOT
from studio.gpu.broker.broker import GpuBroker, NoGpuAvailableError
from studio.gpu.broker.config import BrokerConfig
from studio.gpu.broker.records import PgBrokerRecords
from studio.gpu.broker.wallet import GpuWallet
from studio.gpu.pods import GpuPodLimits
from studio.ledger import BudgetExceededError, BudgetGuard, CostLedger, Pricing, VideoBudgetExceededError
from studio.ledger.cost_view import CostView
from studio.routing import Candidate, Router
from studio.tests.pworld import seed_video
from studio.tests.test_gpu_broker import NOW, Env, NoEvents, fixed_clock
from studio.tests.test_pricing import at

LIVE = Pricing(REPO_ROOT / "config")  # the real config: default cap 150 INR
FX = LIVE.fx()


@pytest.fixture
def db(scratch_db):
    for t in ("routing_decision", "provider_health", "cost_entry"):
        scratch_db.execute(f"DELETE FROM {t}")
    return scratch_db


def spend(db, video, inr, provider="gemini_api", note="x", ts=None):
    CostLedger(db).record(video, provider, Decimal(str(inr)), None, None, True, note)
    if ts is not None:
        db.execute("UPDATE cost_entry SET ts = %(t)s WHERE video_id = %(v)s AND note = %(n)s", {"t": ts, "v": video, "n": note})


# ---------------------------------------------------------------------------------------------------------- the guard

def test_the_default_cap_comes_from_config_and_is_150_rupees():
    assert LIVE.video_default_cap_inr() == Decimal(150)
    assert at("2026-11-19").video_default_cap_inr() == Decimal(0), "a config without the section means no cap, not a crash"


def test_a_paid_stage_that_would_pass_the_cap_is_refused_and_holds_for_the_owner(db):
    v = seed_video(db)
    g = BudgetGuard(db, LIVE)
    spend(db, v, 120)
    g.require(Decimal(30), "MUSIC (1 Lyria generation)", v)  # 120 + 30 = 150: exactly at the cap is allowed
    with pytest.raises(VideoBudgetExceededError) as ex:
        g.require(Decimal("30.5"), "MUSIC (1 Lyria generation)", v)
    msg = str(ex.value)
    assert "Video cap of ₹150" in msg and "MUSIC" in msg and "₹120 spent" in msg and "Retry" in msg and "Continue without it" in msg
    assert isinstance(ex.value, BudgetExceededError), "so the workflow's non-retryable budget type puts the stage on hold"
    with pytest.raises(VideoBudgetExceededError):
        g.require_gpu(Decimal(31), "3 clips", v)


def test_a_call_that_names_no_video_is_not_held_by_a_video_cap(db):
    BudgetGuard(db, LIVE).require(Decimal(500), "a one-off")  # only the monthly cap speaks (no video id)


def test_failed_attempts_and_aborted_pods_count_as_spent(db):
    v = seed_video(db)
    g = BudgetGuard(db, LIVE)
    spend(db, v, 40, note="KEYFRAME failed attempt (billed, no usable image)")
    PgBrokerRecords(db, LIVE).cost(v, "runpod", 0.5, 0.69, "GPU rental p1 (4090) ended: community attempt 1 aborted: CUDA too old")
    spent = g.video_spent_inr(v)
    assert spent > Decimal(40), "the aborted pod is a CostEntry on the video"
    b = g.video_budget(v)
    assert b["spentInr"] == spent and b["remainingInr"] == Decimal(150) - spent and b["capInr"] == Decimal(150)


def test_a_video_can_carry_its_own_cap_and_zero_means_none(db):
    v = seed_video(db)
    g = BudgetGuard(db, LIVE)
    spend(db, v, 60)
    assert g.set_video_budget(v, Decimal(100)) and g.video_cap_inr(v) == Decimal(100)
    with pytest.raises(VideoBudgetExceededError):
        g.require(Decimal(50), "x", v)
    g.set_video_budget(v, Decimal(300))
    g.require(Decimal(50), "x", v)  # raising the cap lets the held stage go on after Retry
    g.set_video_budget(v, Decimal(0))
    assert g.video_budget(v)["remainingInr"] is None and g.video_cap_inr(v) == 0
    g.require(Decimal(900), "x", v)  # over the default 150 but under the monthly Gemini cap
    g.set_video_budget(v, None)
    assert g.video_cap_inr(v) == Decimal(150), "None goes back to the default"
    assert not g.set_video_budget(uuid.uuid4(), Decimal(1))


def test_a_dream_short_has_its_cap_too(db):
    d = uuid.uuid4()
    db.execute("INSERT INTO dream_short (id, idea, status, budget_inr) VALUES (%(i)s, 'moon', 'RUNNING', 90)", {"i": d})
    db.execute("INSERT INTO cost_entry (id, dream_short_id, provider_id, amount_inr, estimated) VALUES (%(i)s, %(d)s, 'gemini_api', 80, true)",
               {"i": uuid.uuid4(), "d": d})
    g = BudgetGuard(db, LIVE)
    assert g.video_spent_inr(d) == Decimal(80)
    with pytest.raises(VideoBudgetExceededError):
        g.require(Decimal("11.28"), "Dream Short music (1 Lyria generation)", d)


def test_the_router_skips_a_paid_route_over_the_video_cap_but_never_a_free_one(db):
    from studio.tests.test_router import provider

    cap = "video.i2v.final"
    provider(db, "gemini_omni", 8, "gemini", True, f'["{cap}"]')
    provider(db, "free_route", 3, "none", True, f'["{cap}"]')
    v = seed_video(db)
    router = Router(db, BudgetGuard(db, LIVE), LIVE)
    spend(db, v, 140)
    c = router.choose(v, "CLIPS", cap, [Candidate("gemini_omni", Decimal(20)), Candidate("free_route", Decimal(0))], None, False)
    assert c.provider_id == "free_route"
    assert any("Video cap" in str(r.get("why")) for r in c.considered if r["provider"] == "gemini_omni")
    with pytest.raises(BudgetExceededError):
        router.choose(v, "CLIPS", cap, [Candidate("gemini_omni", Decimal(20))], None, False)
    assert router.choose(None, "CLIPS", cap, [Candidate("gemini_omni", Decimal(20))], None, False).provider_id == "gemini_omni"


# ---------------------------------------------------------------------------------------------------------- the broker

def guard_fn(cap):
    def f(short, spent, est, reserved, strict):
        if cap <= 0 or (est <= 0 and not strict):
            return None
        if spent + reserved + est > cap:
            return f"Video cap of ₹{cap:.0f} would be exceeded by this GPU rental (≈₹{est:.0f}; ₹{spent:.0f} spent)."
        return None
    return f


def broker_with(e, cap, spent_inr=None, clock=None):
    """A broker whose per-video check reads what the fake Records booked (hours x hourly x fx) plus any extra rupees."""
    spent_inr = spent_inr or {}
    check = guard_fn(cap)

    def video(short, est_usd, reserved_usd, strict):
        booked = sum(float(c.split("|")[1]) * float(c.split("|")[2]) for c in e.records.costs) * 90.0 + spent_inr.get(short, 0.0)
        return check(short, booked, est_usd * 90.0, reserved_usd * 90.0, strict)

    cfg = e.cfg()
    limits = GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, False)
    return GpuBroker([e.runpod, e.jarvis, e.modal], cfg, GpuWallet(cfg, lambda p: 0.0, lambda: 0.0, video), e.records, e.leases, NoEvents(),
                     limits, clock or fixed_clock())


def test_a_pod_rental_that_would_pass_the_video_cap_is_refused_before_anything_is_rented():
    e = Env()
    b = broker_with(e, cap=150, spent_inr={e.short: 100.0})  # $0.50/h x 1 h x 90 = 45 INR; 100 + 45 <= 150 passes
    assert e.acquire(b).instance.id and len(e.runpod.created) == 1
    e2 = Env()
    b2 = broker_with(e2, cap=150, spent_inr={e2.short: 110.0})  # 110 + 45 > 150
    with pytest.raises(NoGpuAvailableError) as ex:
        e2.acquire(b2)
    assert str(ex.value).startswith("Video cap of ₹150") and not (e2.runpod.created or e2.jarvis.created)
    assert len(e2.records.decisions) == 1, "the refusal is a routing decision too (rule 6)"


def test_what_a_released_pod_booked_counts_toward_the_next_rental_of_the_same_video():
    e = Env()
    b = broker_with(e, cap=60)  # one 1 h rental needs 45 INR and fits an empty video
    e.acquire(b)
    b.release(e.short, "stage done")
    e.records.costs.append("runpod|0.5|0.5")  # an aborted attempt: half an hour at $0.50/h = 22.5 INR booked on this video
    with pytest.raises(NoGpuAvailableError, match="Video cap"):
        e.acquire(b)


def test_a_video_at_its_cap_is_not_given_more_time_on_the_shared_pod():
    e = Env()
    clock = [NOW]
    b = broker_with(e, cap=100, clock=lambda: clock[0])
    e.acquire(b)  # nothing spent yet, a rental fits
    clock[0] = NOW + timedelta(hours=1)  # $0.50 running = 45 INR reserved, not booked yet
    e.acquire(b)  # 45 <= 100: the pod is reused
    clock[0] = NOW + timedelta(hours=3)  # $1.50 running = 135 INR reserved
    with pytest.raises(NoGpuAvailableError, match="Video cap"):
        e.acquire(b)
    assert len(e.runpod.created) == 1, "no second pod was rented"


def test_no_video_cap_leaves_the_daily_cap_logic_alone():
    e = Env()
    cfg = e.cfg(daily=3.50)
    wallet = GpuWallet(cfg, lambda p: 0.0, lambda: 3.20)  # the default video check: no cap
    b = GpuBroker([e.runpod, e.jarvis, e.modal], cfg, wallet, e.records, e.leases, NoEvents(),
                  GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, False), fixed_clock())
    with pytest.raises(NoGpuAvailableError) as ex:
        e.acquire(b)
    assert str(ex.value).startswith("Daily GPU cap reached")


def test_the_real_wallet_reads_the_ledger_for_a_video_and_converts_dollars(db):
    v = seed_video(db)
    cfg = BrokerConfig(["runpod"], {"runpod": 12.0}, 0, ["NVIDIA GeForce RTX 4090"], 24, 48, 80, 0.75, 8000, 0)
    w = GpuWallet.from_db(cfg, db, LIVE)
    assert w.video_refusal(v, 1.0) is None  # about 90 INR on an empty video
    spend(db, v, 100)
    why = w.video_refusal(v, 1.0)
    assert why is not None and why.startswith("Video cap of ₹150") and "this GPU rental" in why
    assert w.video_refusal(v, 0.0) is None and w.video_refusal(v, 0.0, 0.0, True) is None
    assert w.video_refusal(v, 0.0, 60.0 / FX, True) is not None, "a running pod's unbooked cost is reserved"


# ---------------------------------------------------------------------------------------------------------- the cost view

def test_the_cost_view_splits_stages_providers_pods_and_prices_a_finished_second(db):
    v = seed_video(db, status="COMPLETED")
    t0 = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
    for stage, a, b in [("MUSIC", 0, 10), ("KEYFRAMES", 10, 30)]:
        db.execute("INSERT INTO stage_run (id, video_id, stage, attempt, mode, status, started_at, finished_at) "
                   "VALUES (%(i)s, %(v)s, %(s)s, 1, 'AUTO', 'APPROVED', %(a)s, %(b)s)",
                   {"i": uuid.uuid4(), "v": v, "s": stage, "a": t0 + timedelta(minutes=a), "b": t0 + timedelta(minutes=b)})
    spend(db, v, 11.28, note="MUSIC (lyria)", ts=t0 + timedelta(minutes=5))
    spend(db, v, 6, note="KEYFRAME hero", ts=t0 + timedelta(minutes=15))
    spend(db, v, 3, provider="runpod", note="GPU rental pod-a (4090) ended: stage done", ts=t0 + timedelta(minutes=40))
    spend(db, v, 2, provider="runpod", note="GPU rental pod-b (4090) ended: community attempt 1 aborted: slow", ts=t0 + timedelta(minutes=41))
    spend(db, v, 1, note="after the stages", ts=t0 + timedelta(minutes=50))
    asset_id = uuid.uuid4()
    db.execute("INSERT INTO asset (id, kind, duration_sec) VALUES (%(i)s, 'video', 20)", {"i": asset_id})
    db.execute("UPDATE video SET master_asset_id = %(a)s WHERE id = %(v)s", {"a": asset_id, "v": v})
    out = CostView(db, BudgetGuard(db, LIVE)).for_video(v)
    stages = {s["key"]: s["amountInr"] for s in out["perStage"]}
    assert stages == {"MUSIC": Decimal("11.28"), "KEYFRAMES": Decimal(6), "GPU_RENTAL": Decimal(5), "OTHER": Decimal(1)}
    assert {p["key"]: p["amountInr"] for p in out["perPod"]} == {"pod-a": Decimal(3), "pod-b": Decimal(2)}
    assert {p["key"] for p in out["perProvider"]} == {"gemini_api", "runpod"}
    assert out["totalInr"] == Decimal("23.28") and out["capInr"] == Decimal(150) and out["overCap"] is False
    assert out["remainingInr"] == Decimal("126.72") and out["finishedSeconds"] == 20.0
    assert out["costPerSecondInr"] == Decimal("1.1640")


def test_the_cost_view_of_an_unfinished_video_has_no_per_second_figure(db):
    v = seed_video(db, status="RUNNING")
    out = CostView(db, BudgetGuard(db, LIVE)).for_video(v)
    assert out["totalInr"] == 0 and out["costPerSecondInr"] is None and out["perStage"] == []


# ---------------------------------------------------------------------------------------------------------- the API

def test_the_api_exposes_budget_in_the_detail_the_cost_view_and_a_raise_of_the_cap(scratch_db):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from studio.api.app import create_app
    from studio.api.video_wiring import build_video_context
    from studio.config import Settings
    from studio.tests.dworld import FakeGateway, FakeLlm, make_world
    from studio.tests.test_video_api import Urls

    db = scratch_db
    make_world(db)
    s = Settings(video_routes=True)
    v = build_video_context(db, s, llm=FakeLlm(), gateway=FakeGateway(), media_urls=Urls(), researcher=object())
    c = TestClient(create_app(db=db, settings=s, video_ctx=v), raise_server_exceptions=False)
    vid = seed_video(db)
    spend(db, vid, 20)
    d = c.get(f"/api/videos/{vid}").json()
    assert d["budget"]["capInr"] == 150 and d["budget"]["spentInr"] == 20 and d["budget"]["remainingInr"] == 130
    assert c.get(f"/api/videos/{vid}/cost").json()["totalInr"] == 20
    r = c.put(f"/api/videos/{vid}/budget", json={"budgetInr": 400}).json()
    assert r["capInr"] == 400 and r["remainingInr"] == 380
    assert c.put(f"/api/videos/{vid}/budget", json={"budgetInr": -1}).status_code == 400
    assert c.put(f"/api/videos/{uuid.uuid4()}/budget", json={"budgetInr": 5}).status_code == 404
    made = c.post("/api/videos", json={"prompt": "a calm sea", "budgetInr": 80}).json()["id"]
    assert c.get(f"/api/videos/{made}").json()["budget"]["capInr"] == 80
    assert c.post("/api/videos", json={"prompt": "x", "budgetInr": "lots"}).status_code == 400
    default = c.post("/api/videos", json={"prompt": "no override"}).json()["id"]
    assert c.get(f"/api/videos/{default}").json()["budget"]["capInr"] == 150
