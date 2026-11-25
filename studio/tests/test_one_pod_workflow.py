"""One-pod mode (VIDEO_ONE_POD) in the workflow: one acquire and one release across KEYFRAMES and DRAFT_GEN when no human wait sits between,
and the pod let go on every other path (failure, review/manual hold, pause, cancel). Fakes only: no pod, no spend, Temporal's test server."""

import asyncio
import threading

import pytest

pytest.importorskip("temporalio")

from temporalio.client import WorkflowFailureError  # noqa: E402

from studio.tests.test_video_workflow import run_case  # noqa: E402


async def until(cond, what, seconds=30.0):
    """Polls in REAL time (the time-skipping server must not jump ahead while a fake activity thread is still working)."""
    for _ in range(int(seconds / 0.05)):
        if cond():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"never happened: {what}")


KF, KEPT, DRAFT_DONE = "ACQUIRE:keyframes", "KEEP", "RELEASE:clips saved"


def first_draft_cycle(h):
    """The pod log up to and including the release that ends the DRAFT_GEN batch."""
    log = h.c.pod.log
    return log[: log.index(DRAFT_DONE) + 1] if DRAFT_DONE in log else list(log)


def test_flag_off_keeps_the_old_two_pod_behaviour_and_never_calls_the_new_activities():
    async def case(h):
        w = await h.start("off")
        await w.result()
        assert first_draft_cycle(h) == [KF, "RELEASE:keyframes saved", "ACQUIRE:clips", DRAFT_DONE]
        assert all(not flag for _, flag in h.c.one_pod_calls), "the span/keep flags are never sent when the mode is off"
        assert not any("one-pod" in e or "human wait" in e for e in h.c.pod.log)

    run_case(case)


def test_happy_path_one_acquire_one_release_across_keyframes_and_draft_clips():
    async def case(h):
        h.c.one_pod = True
        w = await h.start("happy")
        await w.result()
        assert first_draft_cycle(h) == [KF, KEPT, DRAFT_DONE], "rented once for the keyframes, kept, used by the draft clips, released when saved"
        assert ("request", True) in h.c.one_pod_calls and ("save", True) in h.c.one_pod_calls
        assert not h.c.pod.open, "nothing is left held at the end of the video"
        assert h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_a_failure_in_the_keyframes_stage_releases_the_pod_while_it_waits_for_the_owner():
    async def case(h):
        h.c.one_pod = True
        h.m.fail_keyframes = True
        w = await h.start("kf-fail")
        await h.await_waiting(w, "KEYFRAMES")
        assert not h.c.pod.open, "the failed stage waits for a human: the pod must not wait with it"
        assert any(e.startswith("RELEASE:KEYFRAMES waits for the owner") for e in h.c.pod.log)
        h.m.fail_keyframes = False
        await w.signal("reject", args=["KEYFRAMES", "try again"])
        await w.result()
        assert h.c.pod.count("ACQUIRE:keyframes") == 2, "the retry rented a fresh pod"
        assert not h.c.pod.open and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_a_review_window_between_the_stages_means_no_keep_and_a_second_pod():
    async def case(h):
        h.c.one_pod = True
        w = await h.start("review", modes={"ANIMATIC": "REVIEW"})
        await w.result()
        assert ("save", False) in h.c.one_pod_calls and ("save", True) not in h.c.one_pod_calls
        assert first_draft_cycle(h) == [KF, "RELEASE:keyframes saved", "ACQUIRE:clips", DRAFT_DONE]

    run_case(case)


def test_a_manual_or_review_keyframes_stage_itself_is_a_hold_too():
    async def case(h):
        h.c.one_pod = True
        w = await h.start("kf-review", modes={"KEYFRAMES": "REVIEW"})
        await w.result()
        assert ("save", True) not in h.c.one_pod_calls and KEPT not in h.c.pod.log
        assert any(e.startswith("RELEASE:") for e in h.c.pod.log) and not h.c.pod.open

    run_case(case)


def test_the_owner_switching_a_later_stage_to_manual_after_the_keep_releases_the_pod_at_the_wait():
    async def case(h):
        h.c.one_pod = True
        h.c.at_save, h.c.proceed = threading.Event(), threading.Event()
        w = await h.start("switch")
        await until(h.c.at_save.is_set, "the keyframes save started")
        await w.signal("set_mode", args=["ANIMATIC", "MANUAL"])
        h.c.proceed.set()
        await h.await_waiting(w, "ANIMATIC")
        assert KEPT in h.c.pod.log and not h.c.pod.open, "kept at the save, then released when the animatic began waiting for the owner"
        await w.signal("approve", args=["ANIMATIC", None])
        await w.result()
        assert h.c.pod.count("ACQUIRE:") >= 2 and not h.c.pod.open

    run_case(case)


def test_pause_after_the_keep_releases_the_pod_before_the_video_waits():
    async def case(h):
        h.c.one_pod = True
        h.c.at_save, h.c.proceed = threading.Event(), threading.Event()
        w = await h.start("pause")
        await until(h.c.at_save.is_set, "the keyframes save started")
        await w.signal("pause")
        h.c.proceed.set()
        await until(lambda: "VIDEO:PAUSED" in h.c.events, "the video paused")
        assert KEPT in h.c.pod.log and not h.c.pod.open
        await w.signal("resume")
        await w.result()
        assert not h.c.pod.open

    run_case(case)


def test_cancel_while_the_pod_is_held_releases_it():
    async def case(h):
        h.c.one_pod = True
        h.c.block_animatic = threading.Event()  # the animatic runs for a long time while the pod is held
        w = await h.start("cancel")
        await until(lambda: KEPT in h.c.pod.log and "ANIMATIC:1:RUNNING" in h.c.events, "the animatic started with the pod held")
        assert h.c.pod.open
        await w.cancel()
        try:
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(w.result(), 60)
        finally:
            h.c.block_animatic.set()
        assert not h.c.pod.open, "the cancel handler released the held pod"
        # the cancelled activity surfaces as a failed stage (released at once, as for any wait) and the cancel handler is a second, idempotent net
        assert any(e.startswith("RELEASE:") for e in h.c.pod.log), h.c.pod.log

    run_case(case)


def test_clips_that_need_the_owners_go_ahead_release_the_held_pod_for_that_wait():
    async def case(h):
        h.c.one_pod = True
        h.c.clips_need_approval = True
        w = await h.start("approve-spend")
        await h.await_waiting(w, "DRAFT_GEN")
        assert KEPT in h.c.pod.log and not h.c.pod.open, "never keep a pod across the owner's spend approval"
        await w.signal("approve", args=["DRAFT_GEN", None])
        await h.await_waiting(w, "FINAL_GEN")  # the final pass needs a go-ahead too
        assert not h.c.pod.open, "the draft clips were saved and released before the next human wait"
        await w.signal("reject", args=["FINAL_GEN", "skip"])
        await w.result()
        assert not h.c.pod.open

    run_case(case)


def test_a_rerun_of_only_the_keyframes_keeps_nothing():
    async def case(h):
        h.c.one_pod = True
        w = await h.start("rerun", only=["KEYFRAMES"])
        await w.result()
        assert ("save", False) in h.c.one_pod_calls and KEPT not in h.c.pod.log and not h.c.pod.open

    run_case(case)


def test_asyncio_is_the_only_new_import_the_workflow_needs():
    # the workflow sandbox passes asyncio through; a module-level import of anything heavy would break replay
    from studio.workflow import video_production as vp

    assert vp.asyncio is asyncio
