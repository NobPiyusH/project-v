"""Hold before run (25 Nov 2026): a stage that can spend money waits for the owner BEFORE it runs, when it is in Manual mode or the video has
`approve_paid_first`. Workflow behaviour on Temporal's time-skipping test server with the fakes of test_video_workflow (no spend), plus the real
activity on the scratch Postgres (estimate, budget line, flag default, read model)."""

import json
import uuid

import pytest

pytest.importorskip("temporalio")

from studio.api import plan_preview  # noqa: E402
from studio.tests.test_video_workflow import run_case, vp  # noqa: E402


def _view(status, stage):
    return next((v for v in status["stages"] if v["stage"] == stage), {})


async def _drive(h, w, answer="approve"):
    """Answers every wait the owner is asked, in order; returns [(stage, was it a hold BEFORE the stage ran)]."""
    asked = []
    while True:
        s = None
        for _ in range(800):
            s = await w.query("status")
            if s["phase"] in ("WAITING", "COMPLETED"):
                break
            await h.env.sleep(0.05)
        else:
            raise AssertionError("the workflow neither waited nor finished")
        if s["phase"] == "COMPLETED":
            return asked
        stage = s["currentStage"]
        asked.append((stage, bool(_view(s, stage).get("heldBeforeRun"))))
        await w.signal(answer, args=[stage, None])
        for _ in range(800):  # until it has moved on
            n = await w.query("status")
            if n["phase"] == "COMPLETED" or n["currentStage"] != stage or n["phase"] != "WAITING":
                break
            await h.env.sleep(0.05)


def test_manual_music_waits_before_it_spends_and_approve_runs_it():
    async def case(h):
        h.c.plan_music = True
        w = await h.start("hold-music", modes={"MUSIC": "MANUAL"})
        await h.await_waiting(w, "MUSIC")
        e = h.c.events
        assert "MUSIC:1:WAITING_APPROVAL" in e and "BEFORE_RUN:MUSIC:1:inr=12.0" in e
        assert h.m.paid_calls == [] and h.c.ran == [], "no provider call and no work before the owner said yes"
        view = _view(await w.query("status"), "MUSIC")
        assert view["heldBeforeRun"] is True and view["estimateInr"] == 12.0 and "Waiting before it spends" in view["heldReason"]
        await w.signal("approve", args=["MUSIC", None])
        await h.await_waiting(w, "MUSIC")  # Manual still holds the RESULT afterwards, as before
        assert h.m.paid_calls == ["music"] and h.c.ran == ["MUSIC"]
        assert "MUSIC:1:RUNNING" in h.c.events and not _view(await w.query("status"), "MUSIC").get("heldBeforeRun")
        await w.signal("approve", args=["MUSIC", None])
        await w.result()
        assert "MUSIC:1:APPROVED" in h.c.events and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_reject_skips_the_stage_and_spends_nothing():
    async def case(h):
        h.c.plan_music = True
        w = await h.start("hold-reject", modes={"MUSIC": "MANUAL"})
        await h.await_waiting(w, "MUSIC")
        await w.signal("reject", args=["MUSIC", None])
        await w.result()
        assert "MUSIC:1:SKIPPED" in h.c.events and "MUSIC:1:RUNNING" not in h.c.events
        assert "music" not in h.m.paid_calls and "MUSIC" not in h.c.ran and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_the_flag_makes_auto_stages_ask_first_and_every_paid_call_follows_a_yes():
    async def case(h):
        h.c.plan_music, h.c.approve_paid_first = True, True
        w = await h.start("flag-on")
        asked = await _drive(h, w)
        held = [s for s, before in asked if before]
        assert held[:3] == ["MUSIC", "NARRATION", "SHOT_PLAN"] and "KEYFRAMES" in held
        assert {"CRITIC", "TAKE_SELECTION", "AUDIO", "PACKAGING"} <= set(held), "stages that will spend when built ask too"
        assert ("DRAFT_GEN", False) in asked, "the clip batch asks with its own estimate, made mandatory"
        assert h.m.paid_calls.count("music") == 1 and h.m.paid_calls.count("voice") == 1 and h.m.paid_calls.count("keyframes") == 1

    run_case(case)


def test_flag_off_auto_keeps_the_old_behaviour():
    async def case(h):
        h.c.plan_music = True
        w = await h.start("flag-off")
        await w.result()
        assert not any(e.startswith("BEFORE_RUN") for e in h.c.events) and "MUSIC:1:AUTO_PASSED" in h.c.events
        assert h.m.paid_calls.count("music") == 1 and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_the_cap_reason_is_shown_with_the_question():
    async def case(h):
        h.c.plan_music, h.c.cap_reason = True, "Video cap of ₹150 would be exceeded by one music track (≈₹12; ₹145 spent)."
        w = await h.start("hold-cap", modes={"MUSIC": "MANUAL"})
        await h.await_waiting(w, "MUSIC")
        view = _view(await w.query("status"), "MUSIC")
        assert "Over the cap: Video cap of ₹150" in view["heldReason"] and h.m.paid_calls == []
        await w.signal("reject", args=["MUSIC", None])
        await w.result()

    run_case(case)


CALL = {"NARRATION": "voice", "KEYFRAMES": "keyframes", "MUSIC": "music"}  # the paid media call of a stage (the others have none in the fakes)


@pytest.mark.parametrize("stage", ["SHOT_PLAN", "NARRATION", "KEYFRAMES", "CRITIC", "TAKE_SELECTION", "AUDIO", "PACKAGING"])
def test_every_paid_stage_holds_in_manual_before_any_work(stage):
    async def case(h):
        w = await h.start(f"hold-{stage}", modes={stage: "MANUAL"})
        await h.await_waiting(w, stage)
        assert f"BEFORE_RUN:{stage}:1:inr=12.0" in h.c.events and CALL.get(stage, "-") not in h.m.paid_calls and stage not in h.c.ran
        assert _view(await w.query("status"), stage)["heldBeforeRun"] is True
        await w.signal("reject", args=[stage, None])  # skip it: no spend
        await w.result()
        assert f"{stage}:1:SKIPPED" in h.c.events and stage not in h.c.ran and CALL.get(stage, "-") not in h.m.paid_calls

    run_case(case)


def test_a_stage_with_nothing_to_spend_is_not_held_before_it_runs():
    async def case(h):
        h.c.stage_inr["AUDIO"] = 0.0
        w = await h.start("hold-free", modes={"AUDIO": "MANUAL"})
        await h.await_waiting(w, "AUDIO")
        assert not any(e.startswith("BEFORE_RUN:AUDIO") for e in h.c.events) and "AUDIO" in h.c.ran  # only the old after-run hold
        await w.signal("approve", args=["AUDIO", None])
        await w.result()

    run_case(case)


def test_manual_clips_ask_even_under_the_auto_approve_amount_and_reject_makes_no_clip():
    async def case(h):
        w = await h.start("hold-clips", modes={"DRAFT_GEN": "MANUAL"})  # the batch is under the auto-approve amount: it used to run unasked
        await h.await_waiting(w, "DRAFT_GEN")
        assert h.m.paid_calls.count("clips") == 0 and h.c.clip_requests == []
        assert "Ready to make 2 draft clips" in _view(await w.query("status"), "DRAFT_GEN")["summary"]
        await w.signal("reject", args=["DRAFT_GEN", None])
        await w.result()
        assert "DRAFT_GEN:1:SKIPPED" in h.c.events and [r for r in h.c.clip_requests if r.startswith("draft")] == []  # only FINAL_GEN made clips

    run_case(case)


def test_switching_a_manual_stage_to_auto_while_it_waits_lets_it_go():
    async def case(h):
        h.c.plan_music = True
        w = await h.start("hold-switch", modes={"MUSIC": "MANUAL"})
        await h.await_waiting(w, "MUSIC")
        await w.signal("set_mode", args=["MUSIC", "AUTO"])
        await w.result()
        assert "MUSIC:1:AUTO_PASSED" in h.c.events and h.m.paid_calls.count("music") == 1

    run_case(case)


def test_the_workflow_hold_list_is_the_paid_list_plus_the_stages_that_will_spend_when_built():
    assert set(plan_preview.HOLDABLE) == vp.HOLDABLE and set(plan_preview.PAID_STAGES) <= vp.HOLDABLE
    assert {"DRAFT_GEN", "FINAL_GEN", "UPSCALE"} == vp.ASKS_ITSELF


def test_the_shipped_config_prices_every_flat_estimate_stage():
    from pathlib import Path

    from studio.ledger.pricing import Pricing

    p = Pricing(Path(__file__).resolve().parents[2] / "config")
    for stage in ("SHOT_PLAN", "NARRATION", "CRITIC", "TAKE_SELECTION", "AUDIO", "PACKAGING"):
        assert p.hold_estimate(stage) is not None, stage
    assert p.hold_estimate("NARRATION")[0] > 0 and p.hold_estimate("AUDIO")[0] == 0 and p.hold_estimate("MUSIC") is None


# ---------------------------------------------------------------------------------------------------- the real activity (scratch Postgres)

from studio.tests.test_control_activities import acts as acts_fixture  # noqa: E402

acts = acts_fixture  # the real control activities on a scratch Postgres (pytest picks fixtures up by name)


def test_hold_check_prices_music_shows_the_budget_and_never_holds_free_or_unflagged_stages(acts):
    db, c, _, video = acts
    v = str(video)
    c.shots.d.pricing._pricing["holdEstimates"] = {"NARRATION": {"provider": "voice", "inr": 15, "what": "the voice"}, "AUDIO": {"inr": 0}}
    free = c.hold_check(v, "MUSIC", False)
    assert free["hold"] is False and free["flag"] is False, "Auto and no flag: as before"
    g = c.hold_check(v, "MUSIC", True)
    assert g["hold"] is True and g["estimateInr"] > 0 and "Lyria" in g["provider"] and "Waiting before it spends" in g["text"]
    assert "Approve to run it, or Reject to skip it" in g["text"] and g["refusal"] is None
    db.execute("UPDATE video SET approve_paid_first = true WHERE id = %(v)s", {"v": video})
    assert c.hold_check(v, "NARRATION", False)["hold"] is True and c.hold_check(v, "NARRATION", False)["estimateInr"] == 15.0
    assert c.hold_check(v, "AUDIO", False)["hold"] is False and c.hold_check(v, "AUDIO", False)["estimateInr"] == 0.0, "placeholder: nothing to spend"
    assert c.hold_check(v, "PLAN", True)["hold"] is False and c.hold_check(v, "RESEARCH", True)["hold"] is False


def test_hold_check_says_why_when_the_video_cap_would_break(acts):
    db, c, _, video = acts
    db.execute("UPDATE video SET budget_inr = 5 WHERE id = %(v)s", {"v": video})
    g = c.hold_check(str(video), "MUSIC", True)
    assert g["hold"] is True and g["refusal"] and "Video cap of ₹5" in g["text"] and "Over the cap" in g["text"]
    assert "left of ₹5" in g["text"]


def test_the_hold_is_recorded_for_the_dashboard_and_cleared_when_the_stage_runs(acts):
    db, c, _, video = acts
    v = str(video)
    c.record_stage(v, "MUSIC", 1, "MANUAL", "WAITING_APPROVAL", "Waiting before it spends: x", None, None)
    c.record_hold_before_run(v, "MUSIC", 1, "Waiting before it spends: x", 12.5)
    from studio.video.service import VideoService
    run = VideoService(db, None).detail(video)["stageRuns"][0]
    assert run["heldBeforeRun"] is True and float(run["estimateInr"]) == 12.5 and run["heldReason"].startswith("Waiting before it spends")
    c.record_stage(v, "MUSIC", 1, "MANUAL", "RUNNING", None, None, None)
    c.record_stage(v, "MUSIC", 1, "MANUAL", "WAITING_APPROVAL", "the track", None, None)  # the old after-run hold of Manual
    run = VideoService(db, None).detail(video)["stageRuns"][0]
    assert run["heldBeforeRun"] is False and run["estimateInr"] is None and run["heldReason"] is None


def test_a_video_made_from_a_plan_file_asks_first_by_default_and_the_flag_can_be_set(acts):
    from studio.video.service import CreateVideo, VideoService
    db, _, _, _ = acts
    started = []

    class Gateway:
        def start(self, *a):
            started.append(a)

    svc = VideoService(db, Gateway())
    plain = svc.create(CreateVideo("a calm sea"))
    explicit_off = svc.create(CreateVideo("x", approve_paid_first=False))
    flag = lambda i: db.rows("SELECT approve_paid_first AS f FROM video WHERE id = %(i)s", {"i": i})[0]["f"]  # noqa: E731
    assert flag(plain) is False and flag(explicit_off) is False
    svc.set_approve_paid_first(plain, True)
    assert flag(plain) is True and svc.detail(plain)["approve_paid_first"] is True
    with pytest.raises(LookupError):
        svc.set_approve_paid_first(uuid.uuid4(), True)
    assert json.dumps(started[0][1]).startswith('"video-')
