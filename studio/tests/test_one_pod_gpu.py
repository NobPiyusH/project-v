"""One-pod mode in VideoGpu and the control activities: a fake broker door that behaves like the real shared pod (one open pod per video, cost
cap, state), so every leak path is exercised without a pod or any spend."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from studio.production.video_gpu import CAMERA_SECONDS_PER_81, CAMERA_SETUP_SECONDS, PodSession, VideoGpu
from studio.workflow.activities import ControlActivities

V = uuid.uuid4()
OPEN, GONE = SimpleNamespace(name="OPEN"), SimpleNamespace(name="GONE")


class FakeBrokerDoor:
    """PodGpu with the broker's behaviour: acquire joins an open pod, release ends it, a hours-size above `max_hours` is refused (cap)."""

    def __init__(self, max_hours=100.0, cap=3.5, spent=0.0):
        self.pods_rented, self.releases, self.sessions, self.prefetch = 0, [], [], []
        self.open, self.state, self.max_hours, self.cap, self.spent = False, OPEN, max_hours, cap, spent
        self.fail_state = False

    def enabled(self):
        return True

    def clip_seconds(self, vid, frames):
        return 100.0

    def picture_seconds(self, vid, n):
        return 600.0 + n

    def inr(self, seconds):
        return Decimal(seconds)

    def hold(self, vid):
        pass

    def unhold(self, vid):
        pass

    def session(self, vid, hours, prefetch=()):
        self.sessions.append((hours, tuple(prefetch)))
        if not self.open:
            if hours > self.max_hours:
                raise RuntimeError("Daily GPU cap reached: nothing was rented")
            self.open = True
            self.pods_rented += 1
            self.prefetch.append(tuple(prefetch))
        return PodSession("http://pod", 60)

    def release(self, vid, why):
        self.releases.append(why)
        self.open = False

    def last_activity(self, vid):
        return None

    def heartbeat(self, vid):
        if self.fail_state:
            raise RuntimeError("provider unreachable")
        return self.state

    def today(self):
        return {"spentUsd": self.spent, "capUsd": self.cap}


def gpu(door=None, **kw):
    door = door or FakeBrokerDoor()
    return door, VideoGpu(door, "broker", "omni", "broker", 5, one_pod=True, **kw)


def span(vg, n=4):
    return vg.span_seconds(V, n, n)


def test_the_span_is_sized_for_pictures_plus_every_clip_and_carries_the_camera_group():
    door, vg = gpu()
    s = span(vg, 4)
    assert s == pytest.approx(604 + CAMERA_SETUP_SECONDS + 4 * CAMERA_SECONDS_PER_81)
    vg.session(V, 100.0, s)
    assert door.sessions == [(pytest.approx(s / 3600.0), ("camera",))], "ONE reserve for the whole span, with the Fun Camera group to prefetch"
    vg.release(V, "x")


def test_keyframes_then_draft_clips_use_one_rental_and_release_once():
    door, vg = gpu()
    vg.session(V, 100.0, span(vg))  # KEYFRAMES
    assert vg.keep(V) is True
    vg.session(V, 5000.0)  # DRAFT_GEN joins the kept pod
    assert door.pods_rented == 1 and len(door.sessions) == 2
    vg.release(V, "clip batch saved")
    assert door.releases == ["clip batch saved"] and not door.open
    vg.release(V, "again")  # safe to repeat
    assert door.releases == ["clip batch saved"]


def test_a_pod_not_rented_for_the_span_is_never_kept():
    door, vg = gpu(FakeBrokerDoor(max_hours=0.5))  # the span (~0.6 h) is over the cap, the single stage (0.03 h) is not
    vg.session(V, 100.0, span(vg))
    assert door.sessions[0][0] > 0.5 and door.sessions[1][0] < 0.5, "refused for the span, rented for the single stage"
    assert door.prefetch == [()], "the single-stage pod has no camera prefetch"
    assert vg.keep(V) is False, "nothing was reserved for the clips: the caller releases it"
    vg.release(V, "keyframes saved")
    assert not door.open


def test_everything_refused_raises_and_nothing_is_held():
    door, vg = gpu(FakeBrokerDoor(max_hours=0.0))
    with pytest.raises(RuntimeError, match="cap"):
        vg.session(V, 100.0, span(vg))
    assert door.pods_rented == 0 and vg.keep(V) is False


def test_keep_is_off_without_the_flag_or_when_the_pod_is_gone_or_unreadable():
    door = FakeBrokerDoor()
    off = VideoGpu(door, "broker", "omni", "broker", 5)
    off.session(V, 100.0, 9999.0)
    assert off.keep(V) is False
    off.release(V, "x")
    door2, vg = gpu(FakeBrokerDoor())
    vg.session(V, 100.0, span(vg))
    door2.state = GONE
    assert vg.keep(V) is False
    door3, vg3 = gpu(FakeBrokerDoor())
    vg3.session(V, 100.0, span(vg3))
    door3.fail_state = True
    assert vg3.keep(V) is False


def test_idle_hold_timeout_ends_a_pod_whose_next_stage_never_starts():
    door, vg = gpu(hold_idle_minutes=5)
    vg.session(V, 100.0, span(vg))
    vg.keep(V)
    now = datetime.now(UTC)
    vg.release_stale_holds(now + timedelta(minutes=4))
    assert door.open, "still inside the hold window"
    vg.release_stale_holds(now + timedelta(minutes=6))
    assert not door.open and "next stage did not start within 5 min" in door.releases[0]


def test_a_stage_starting_between_the_two_restarts_the_idle_clock():
    door, vg = gpu(hold_idle_minutes=5)
    vg.session(V, 100.0, span(vg))
    vg.keep(V)
    assert vg.touch(V) is True
    vg.release_stale_holds(datetime.now(UTC) + timedelta(minutes=4))
    assert door.open
    vg.release(V, "x")
    assert vg.touch(V) is False


def test_the_generic_idle_check_leaves_a_deliberate_hold_to_the_hold_timer():
    door, vg = gpu()
    vg.session(V, 100.0, span(vg))
    vg.keep(V)
    vg.release_idle(datetime.now(UTC) + timedelta(hours=3))  # would end a normal idle holder
    assert door.open


def test_cap_reached_while_held_ends_the_pod_and_refuses_the_clips_stage():
    door, vg = gpu(FakeBrokerDoor(cap=3.5, spent=0.0))
    vg.session(V, 100.0, span(vg))
    vg.keep(V)
    door.spent = 3.51  # booked + this running pod reached the daily cap
    with pytest.raises(RuntimeError, match="Daily GPU cap reached"):
        vg.session(V, 5000.0)
    assert not door.open and any("cap" in r for r in door.releases)


def test_a_second_stage_after_a_pod_release_rents_again_the_normal_way():
    door, vg = gpu()
    vg.session(V, 100.0, span(vg))
    vg.release(V, "review hold")  # a review window began: the workflow let it go
    vg.session(V, 5000.0)
    assert door.pods_rented == 2


# ----------------------------------------------------------------------------------------------- the control activities


class Shots:
    """ShotStage's three GPU methods over a real VideoGpu; save_keyframes can be told to fail."""

    def __init__(self, vg):
        self.vg, self.fail_save = vg, False

    def save_keyframes(self, video_id, req, result):
        if self.fail_save:
            raise RuntimeError("could not save")
        return SimpleNamespace(summary="ok", artifact_json=None, planned=None)

    def release_gpu(self, video_id, why):
        self.vg.release(video_id, why)

    def keep_gpu(self, video_id):
        return self.vg.keep(video_id)


def acts(vg):
    shots = Shots(vg)
    a = ControlActivities(None, None, researcher=None, inspector=None, creative=None, research=None, story=None, music=None, voice=None,
                          shots=shots, stock=None, animatic=None, video=None, footage=None, assembly=None, finish=None, sharpen=None)
    return a, shots


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr("studio.workflow.activities.from_json", lambda cls, d: SimpleNamespace(gpu_url="http://pod" if d.get("pod") else None))
    monkeypatch.setattr("studio.workflow.activities.stage_result_json", lambda r: {"summary": r.summary})


def test_save_keyframes_keeps_the_pod_only_when_asked_and_only_if_it_can(patched):
    door, vg = gpu()
    a, _ = acts(vg)
    vg.session(V, 100.0, span(vg))
    a.save_keyframes(str(V), {"pod": True}, {}, True)
    assert door.open, "kept for the clips stage"
    a.release_held_pod(str(V), "draft clips stage finished")
    assert not door.open
    vg.session(V, 100.0, span(vg))
    a.save_keyframes(str(V), {"pod": True}, {}, False)
    assert not door.open, "not asked to keep: released exactly as without the flag"


def test_a_failed_save_releases_even_when_the_keep_was_asked(patched):
    door, vg = gpu()
    a, shots = acts(vg)
    vg.session(V, 100.0, span(vg))
    shots.fail_save = True
    with pytest.raises(RuntimeError):
        a.save_keyframes(str(V), {"pod": True}, {}, True)
    assert not door.open, "a failure in the middle never leaves the pod held"


def test_a_pod_that_cannot_be_kept_is_released_by_the_save(patched):
    door, vg = gpu()
    a, _ = acts(vg)
    vg.session(V, 100.0, span(vg))
    door.state = GONE
    a.save_keyframes(str(V), {"pod": True}, {}, True)
    assert not door.open


def test_a_save_without_a_pod_never_touches_the_gpu(patched):
    door, vg = gpu()
    a, _ = acts(vg)
    a.save_keyframes(str(V), {"pod": False}, {}, True)
    assert door.releases == [] and door.pods_rented == 0


def test_the_new_activities_exist():
    from studio.workflow.activities import activity_names

    names = activity_names()
    assert {"control.one_pod_enabled", "control.touch_held_pod", "control.release_held_pod"} <= set(names)
