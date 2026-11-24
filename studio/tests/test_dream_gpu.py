"""Dream Shorts on the GPU broker (provider = broker) against a scratch Postgres and a fake provider: nothing is rented. Ports of
DreamBrokerRouteTest (main case), DreamGpuPowerTest and WarmImageTest. Every rental here goes through GpuBroker.acquire(), which
wraps the boot command with the self-stop guard and opens the watchdog lease."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from studio.dream.gpu import DreamGpu, PickRefusedError
from studio.gpu.broker import support
from studio.gpu.broker.broker import GpuBroker
from studio.gpu.broker.config import BrokerConfig
from studio.gpu.broker.provider import GpuProvider, Instance, InstanceStatus, Quote, State
from studio.gpu.broker.records import PgBrokerRecords
from studio.gpu.broker.wallet import GpuWallet
from studio.gpu.leases import PgPodLeases
from studio.gpu.pods import GpuPodLimits
from studio.media.types import ClipOutput, ClipResult, KeyframeOutput, KeyframeResult
from studio.tests.dworld import asset, count, make_world, provider, seed_short


class Fake(GpuProvider):
    def __init__(self):
        self.created: list[str] = []
        self.terminated: list[str] = []
        self.argv: list[list[str]] = []

    def provider(self): return "runpod"
    def configured(self): return True
    def enabled_by_default(self): return True
    def list_pods(self): return []
    def endpoint_for(self, instance_id, port): return f"https://{instance_id}-{port}.proxy.runpod.net"
    def terminate(self, pod_id): self.terminated.append(pod_id)

    def quote(self, spec):
        return Quote("runpod", True, "HIGH", "NVIDIA GeForce RTX 4090|COMMUNITY", 0.34, "")

    def create(self, req):
        self.created.append(req.name)
        self.argv.append(req.boot_argv)
        assert req.name.startswith("projectv-")
        return Instance("runpod", f"pod-{len(self.created)}", req.name, 0.34, "NVIDIA GeForce RTX 4090", "https://pod-1-8000.proxy.runpod.net")

    def status(self, instance_id):
        return InstanceStatus(State.RUNNING, None, "RUNNING")


class NoEvents:
    def terminated(self, *a): pass
    def last_orphan_booking(self, pod_id): return None
    def orphan_billed(self, *a): pass


@pytest.fixture
def g(scratch_db, tmp_path):
    db = scratch_db
    for t in ("routing_decision", "gpu_pod_lease", "gpu_timing"):
        db.execute(f"DELETE FROM {t}")
    db.execute("UPDATE dream_short SET status = 'CANCELLED'")  # other tests' Shorts share this database: none may look like it waits for GPU work
    worker = tmp_path / "worker.py"
    worker.write_text("print('worker')\n")
    fake = Fake()
    w0 = make_world(db)
    cfg = BrokerConfig(["runpod"], {"runpod": 8.0}, 8, ["NVIDIA GeForce RTX 4090"], 24, 48, 80, 0.75, 8000)
    broker = GpuBroker([fake], cfg, GpuWallet.from_db(cfg, db, w0.pricing), PgBrokerRecords(db, w0.pricing), PgPodLeases(db), NoEvents(),
                       GpuPodLimits("projectv-", 120, 15, 10, 4, 2.0, True), prefs=support.PgPreferences(db))
    gpu = DreamGpu(broker, db, support.PgPreferences(db), w0.pricing, "broker", str(worker), "img", "", "", 30, 8000, 10, 76, 900, 2.0)
    provider(db, "gpu_pod", ["image.dream", "video.i2v.dream"], "gpu_pod", '{"quality":6,"budget":"none"}')
    w = make_world(db, gpu)
    w.gpu, w.fake, w.broker_ = gpu, fake, broker
    return w


def rented_short(g, hold):
    dream, shots = seed_short(g.db, shots=1)
    g.gpu.set_hold(dream, hold, None)
    g.dreams.picture_request(dream, None, None)
    assert g.gpu.is_open(dream)
    return dream, shots


def rental_rows(g, dream):
    return count(g.db, "SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND provider_id = 'runpod'", d=dream)


def test_pictures_and_clips_use_one_pod_that_is_booked_and_terminated_on_release(g):
    db = g.db
    dream, (shot,) = seed_short(db, shots=1)
    pics = g.dreams.picture_request(dream, None, None)
    assert pics.gpu_url == "https://pod-1-8000.proxy.runpod.net" and pics.ready_timeout_seconds == 1800
    assert pics.jobs[0].provider == "gpu_pod" and len(g.fake.created) == 1
    assert g.fake.argv[0][0] == "bash", "the boot command is wrapped by the self-stop guard"
    lease = db.rows("SELECT pod_id, released_at FROM gpu_pod_lease WHERE dream_short_id = %(d)s", {"d": dream})
    assert len(lease) == 1 and lease[0]["released_at"] is None, "every pod has its watchdog lease"
    g.dreams.save_pictures(dream, pics, KeyframeResult([KeyframeOutput(str(shot), "s3://b/p1.png", "gpu_pod", "z", 1, 5.0, None, None, [])], 0, 10))
    assert count(db, "SELECT count(*) FROM cost_entry WHERE dream_short_id = :d", d=dream) == 0, "no per-job entries: the rental is booked on release"

    est = g.dreams.clip_estimate(dream, None)
    assert "rented GPU pod" in est.summary
    clips = g.dreams.clip_request(dream, None)
    assert clips.provider == "pod" and clips.gpu_url == "https://pod-1-8000.proxy.runpod.net" and len(g.fake.created) == 1, "the same pod serves the clips"
    g.dreams.save_clips(dream, clips, ClipResult([ClipOutput(str(shot), 1, "dream", "s3://b/c1.mp4", 76, 57, 16, "fast", None, 0)], 1, False))
    assert count(db, "SELECT count(*) FROM routing_decision WHERE dream_short_id = :d AND chosen_provider_id = 'gpu_pod'", d=dream) == 2
    assert count(db, "SELECT count(*) FROM routing_decision WHERE dream_short_id = :d AND chosen_provider_id = 'runpod'", d=dream) == 1

    g.dreams.status(dream, "MUSIC", "RUNNING", None)  # the GPU steps are over: the pod goes
    assert g.fake.terminated == ["pod-1"]
    assert rental_rows(g, dream) == 1
    assert db.rows("SELECT released_at FROM gpu_pod_lease WHERE dream_short_id = %(d)s", {"d": dream})[0]["released_at"] is not None


def test_svi_needs_the_pod_and_the_modal_route_refuses_it(scratch_db):
    w = make_world(scratch_db)  # no GPU broker
    dream, (s1, _) = seed_short(scratch_db, svi_scenes=1, svi_scene_seconds=10)
    scratch_db.execute("UPDATE dream_shot SET scene_no = 1, beat_no = ord + 1 WHERE dream_short_id = %(d)s", {"d": dream})
    scratch_db.execute("UPDATE dream_shot SET picture_asset_id = %(a)s WHERE id = %(s)s", {"a": asset(scratch_db, "s3://b/p.png"), "s": s1})
    with pytest.raises(RuntimeError, match="SVI scenes need the rented GPU pod"):
        w.dreams.clip_estimate(dream, None)
    with pytest.raises(RuntimeError, match="SVI scenes need the rented GPU pod"):
        w.dreams.clip_request(dream, None)


def test_auto_still_turns_the_pod_off_when_the_gpu_steps_are_over(g):
    dream, _ = rented_short(g, "auto")
    assert g.gpu.off_in_seconds(dream, datetime.now(UTC)) is None, "no idle timer on Auto"
    g.dreams.status(dream, "MUSIC", "RUNNING", None)
    assert not g.gpu.is_open(dream) and rental_rows(g, dream) == 1, "the rental is booked"


def test_manual_keeps_the_pod_after_the_gpu_steps_and_turns_it_off_when_idle(g):
    dream, _ = rented_short(g, "manual")
    before = len(g.fake.terminated)
    g.dreams.status(dream, "MUSIC", "RUNNING", None)
    g.dreams.status(dream, "FINISH", "RUNNING", None)
    assert g.gpu.is_open(dream), "Manual: the pod stays on after the GPU steps are over"
    assert len(g.fake.terminated) == before
    now = datetime.now(UTC)
    left = g.gpu.off_in_seconds(dream, now)
    assert 0 < left <= 600, f"about 10 minutes left: {left}"
    g.gpu.release_idle_manual(now + timedelta(seconds=9 * 60))
    assert g.gpu.is_open(dream), "9 minutes idle is not enough"
    g.gpu.release_idle_manual(now + timedelta(seconds=11 * 60))
    assert not g.gpu.is_open(dream), "10 minutes idle: turned off"
    assert len(g.fake.terminated) == before + 1 and rental_rows(g, dream) == 1, "booked once, with the idle reason"


def test_shorts_share_one_pod_and_the_button_turns_it_off_for_all_of_them(g):
    created_before = len(g.fake.created)
    manual, _ = rented_short(g, "manual")
    auto, _ = rented_short(g, "auto")
    assert len(g.fake.created) == created_before + 1, "the second Short uses the pod that is already on: one pod only"
    assert g.gpu.users_on_pod() == 2
    g.gpu.release_idle_manual(datetime.now(UTC) + timedelta(seconds=3600))  # idle: the Manual Short lets go, the Auto one is left alone
    assert not g.gpu.is_open(manual)
    assert g.gpu.is_open(auto), "Auto Shorts are left to the broker, the pod's own guard and the watchdog"
    assert g.gpu.users_on_pod() == 1
    third, _ = rented_short(g, "manual")  # joins the same pod
    assert len(g.fake.created) == created_before + 1
    g.gpu.turn_off(third)  # the button ends the ONE pod for everyone
    assert not g.gpu.is_open(third) and not g.gpu.is_open(auto) and g.gpu.users_on_pod() == 0
    assert rental_rows(g, auto) == 1 and rental_rows(g, third) == 1, "every Short that used the pod gets its share booked"
    with pytest.raises(RuntimeError):
        g.gpu.turn_off(third)  # nothing left to turn off


def test_a_cancelled_short_always_releases_even_on_manual(g):
    dream, _ = rented_short(g, "manual")
    g.dreams.status(dream, "CLIPS", "CANCELLED", None)
    assert not g.gpu.is_open(dream)


def test_nobody_waiting_ends_the_pod_unless_a_short_holds_it(g):
    g.db.execute("UPDATE dream_short SET status = 'CANCELLED'")  # other tests' Shorts share this database: none may look like it waits for GPU work
    auto, _ = rented_short(g, "auto")
    g.db.execute("UPDATE dream_short SET status = 'WAITING', step = 'FINISH' WHERE id = %(d)s", {"d": auto})
    g.dreams.release_gpu_if_nobody_waits()
    assert not g.gpu.is_open(auto), "no Short is waiting for GPU work: the pod ends now"
    held, _ = rented_short(g, "auto")
    g.db.execute("UPDATE dream_short SET status = 'WAITING', step = 'FINISH' WHERE id = %(d)s", {"d": held})
    g.gpu.hold(held)  # a Video batch holds the shared pod
    g.dreams.release_gpu_if_nobody_waits()
    assert g.gpu.is_open(held), "a held pod is not ended by Dream's 'nobody waits'"
    g.gpu.unhold(held)
    g.dreams.release_gpu_if_nobody_waits()
    assert not g.gpu.is_open(held)


def test_hold_and_idle_minutes_are_validated_and_clamped(g):
    dream, _ = seed_short(g.db)
    assert not g.gpu.hold_manual(dream) and g.gpu.idle_minutes(dream) == 10, "default: Auto"
    with pytest.raises(ValueError):
        g.gpu.set_hold(dream, "sometimes", None)
    g.gpu.set_hold(dream, "MANUAL", 1)
    assert g.gpu.hold_manual(dream) and g.gpu.idle_minutes(dream) == 5, "at least 5 minutes"
    g.gpu.set_hold(dream, "manual", 999)
    assert g.gpu.idle_minutes(dream) == 30, "at most 30 minutes"
    g.gpu.set_hold(dream, "auto", None)
    assert not g.gpu.hold_manual(dream) and g.gpu.idle_minutes(dream) == 30, "idle minutes kept when not given"


def test_a_pick_during_a_rental_is_stored_for_the_next_rent_and_a_bad_pick_is_a_refusal(g):
    dream, _ = rented_short(g, "auto")  # a pod is open
    g.gpu.set_pick(dream, "manual", "runpod|NVIDIA GeForce RTX 4090|COMMUNITY")
    assert g.gpu.pick(dream).choice is not None and g.gpu.pick(dream).manual(), "applies to the next rental"
    with pytest.raises(PickRefusedError, match="not an offered") as e:
        g.gpu.set_pick(dream, "manual", "vast|RTX 9999")
    assert e.value.blocked is False
    with pytest.raises(ValueError, match="mode must be auto or manual"):
        g.gpu.set_pick(dream, "sometimes", None)
    with pytest.raises(PickRefusedError):
        g.gpu.set_pick(dream, "manual", "  ")
    g.dreams.status(dream, "MUSIC", "RUNNING", None)


def test_the_rental_estimate_and_inr_use_the_live_price(g):
    dream, _ = seed_short(g.db, shot_count=4, picture_model="flux2-klein", svi_scenes=1)
    est = g.gpu.rental_estimate(dream)
    assert est["hourlyUsd"] == 0.34 and est["hours"] == pytest.approx(0.35, abs=0.05)
    assert float(g.gpu.inr(3600)) == pytest.approx(0.34 * g.pricing.fx(), abs=0.01)
    assert g.gpu.picture_seconds(dream, 2) == 2 * 10 + 900, "no pod open yet: the start-up counts"


# ------------------------------------------------------------------------------------------------ warm image


def plain_gpu(tmp_path, warm):
    worker = tmp_path / "w.py"
    worker.write_text("print('x')")
    return DreamGpu(None, None, None, None, "broker", str(worker), "plain:1", "", warm, 30, 8000, 10, 76, 900, 2.0)


@pytest.mark.parametrize("blank", [None, "", "  "])
def test_warm_image_is_off_by_default(tmp_path, blank):
    gpu = plain_gpu(tmp_path, blank)
    assert gpu.pod_image() == "plain:1" and "SV_BAKED" not in gpu.pod_env()


def test_warm_image_on_uses_the_warm_image_and_sets_the_baked_root(tmp_path):
    gpu = plain_gpu(tmp_path, " ghcr.io/o/projectv-pod:comfy-v0.45.0 ")
    assert gpu.pod_image() == "ghcr.io/o/projectv-pod:comfy-v0.45.0" and gpu.pod_env()["SV_BAKED"] == "/opt/projectv"
    assert "SV_BOOT_N" in gpu.pod_env(), "the worker is still shipped by env: no rebuild for worker edits"


def test_the_provider_setting_must_be_modal_or_broker(tmp_path):
    with pytest.raises(ValueError):
        DreamGpu(None, None, None, None, "salad")
    assert not DreamGpu(None, None, None, None, "modal").enabled() and uuid.uuid4()


def test_pod_image_settings_read_the_documented_pod_names_first(monkeypatch):
    from studio.api.wiring import _env

    for n in ("POD_WARM_IMAGE", "GPU_WARM_IMAGE"):
        monkeypatch.delenv(n, raising=False)
    assert _env("POD_WARM_IMAGE", "GPU_WARM_IMAGE") == ""
    monkeypatch.setenv("GPU_WARM_IMAGE", "old/name:1")
    assert _env("POD_WARM_IMAGE", "GPU_WARM_IMAGE") == "old/name:1"
    monkeypatch.setenv("POD_WARM_IMAGE", " ghcr.io/x/pod:warm ")
    assert _env("POD_WARM_IMAGE", "GPU_WARM_IMAGE") == "ghcr.io/x/pod:warm"
