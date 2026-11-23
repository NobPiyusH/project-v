"""Port of VideoProductionWorkflowTest: Auto/Manual/Review behaviour of the workflow with in-memory fake activities (no DB, no LLM, no Python
media worker), on Temporal's time-skipping test server (no network, no spend)."""

import asyncio
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("temporalio")

from temporalio import activity  # noqa: E402
from temporalio.client import WorkflowFailureError  # noqa: E402
from temporalio.exceptions import ApplicationError  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

from studio.production import inspector  # noqa: E402
from studio.workflow import queues  # noqa: E402
from studio.workflow import video_production as vp  # noqa: E402
from studio.workflow.activities import ControlActivities, ProducerActivities, activity_functions, activity_names  # noqa: E402
from studio.workflow.codec import video_request_json  # noqa: E402
from studio.workflow.stage import (
    Stage,  # noqa: E402
    StageMode,  # noqa: E402
)
from studio.workflow.types import VideoRequest  # noqa: E402

ALL = [s.name for s in Stage]


def ok(summary, artifact="{}", planned=None):
    return {"summary": summary, "artifactJson": artifact, "plannedStages": planned}


class FakeControl:
    """Same method names as ControlActivities (the real names are `control.<method>`); records recordStage as STAGE:attempt:STATUS."""

    def __init__(self):
        self.events, self.notes_seen, self.inspected, self.clip_requests = [], [], [], []
        self.stock_shots = 0
        self.budget_refusal = False
        self.shot_requests = 0
        self.clips_to_make = 2
        self.clips_need_approval = False
        self.broken_clip_inspections = 0
        self.footage_queries = 0
        self.weak_image_reviews = 0
        self.fail_creative = False
        self.keyframe_inspection_fails = 0
        self.lock = threading.Lock()

    def ev(self, e):
        with self.lock:
            self.events.append(e)

    def plan(self, video_id, prompt, owner_notes, attempt):
        if owner_notes is not None:
            self.notes_seen.append(owner_notes)
        return ok(f"plan v{attempt}", planned=[s for s in ALL if s not in ("RESEARCH", "MUSIC")])  # a plan that skips RESEARCH and MUSIC

    def run_stage(self, video_id, stage, owner_notes, attempt):
        return ok(f"{stage} done")

    def research_request(self, video_id, owner_notes):
        return {"topic": "t"}

    def save_research(self, video_id, result):
        return ok("research saved")

    def claude_research(self, video_id, request):
        return {"facts": [], "sources": [], "queries": [], "model": "claude:test", "usage": None}

    def concept(self, video_id, owner_notes):
        return ok("concept")

    def script(self, video_id, owner_notes):
        return ok("script")

    def music_request(self, video_id, owner_notes):
        return {"videoId": video_id}

    def save_music(self, video_id, result):
        return ok("music")

    def narration_request(self, video_id):
        return {"videoId": video_id}

    def save_narration(self, video_id, result):
        return ok("voice")

    def last_attempt(self, video_id, stage):
        return 2

    def shot_plan(self, video_id, owner_notes):
        return ok("shots")

    def stock_requests(self, video_id):
        return [{"videoId": video_id, "shotId": f"shot{i}", "shotNumber": i + 1} for i in range(self.stock_shots)]

    def save_stock(self, video_id, request, result):
        self.ev(f"STOCK:{request['shotId']}:{'found' if result['found'] else 'fallback'}")
        return ok("saved", None)

    def keyframe_request(self, video_id, owner_notes):
        return {"videoId": video_id}

    def save_keyframes(self, video_id, request, result):
        return ok("keyframes")

    def shot_keyframe_request(self, video_id, shot_ids, owner_notes, policy):
        with self.lock:
            self.shot_requests += 1
        self.ev(f"SHOT_REQUEST:[{', '.join(shot_ids)}]:{policy}:{owner_notes}")
        if self.budget_refusal:
            raise ApplicationError("Monthly Gemini cap reached", type=vp.BUDGET_ERROR, non_retryable=False)
        return {"videoId": video_id}

    def mark_shots(self, video_id, shot_ids, status, error):
        self.ev(f"SHOTS:[{', '.join(shot_ids)}]:{status}")

    def line_narration_request(self, video_id, line_indexes, style_notes):
        self.ev(f"LINE_REQUEST:[{', '.join(map(str, line_indexes))}]:{style_notes}")
        return {"videoId": video_id, "style": f"in a voice that is {style_notes}"}

    def save_line_narration(self, video_id, result, style):
        self.ev(f"LINE_SAVED:{style}")
        return ok("lines saved", None)

    def mark_lines(self, video_id, line_indexes, status, why):
        self.ev(f"LINES:[{', '.join(map(str, line_indexes))}]:{status}")

    def animatic_request(self, video_id):
        return {"videoId": video_id}

    def save_animatic(self, video_id, result):
        return ok("animatic")

    def record_stage(self, video_id, stage, attempt, mode, status, summary, artifact_json, owner_notes):
        self.ev(f"{stage}:{attempt}:{status}")

    def set_review_deadline(self, video_id, stage, attempt, deadline_iso):
        self.ev(f"DEADLINE:{stage}:{'cleared' if deadline_iso is None else 'set'}")

    def set_video_status(self, video_id, status):
        self.ev(f"VIDEO:{status}")

    def clip_estimate(self, video_id, kind, shot_ids):
        n = self.clips_to_make if shot_ids is None else len(shot_ids)
        return {"kind": kind, "clips": n, "gpuSeconds": 100, "inr": 10.0, "usd": 0.1, "wallMinutes": 3, "needsApproval": self.clips_need_approval,
                "summary": f"Ready to make {n} {kind} clips: about ₹10"}

    def clip_request(self, video_id, kind, shot_ids):
        self.clip_requests.append(f"{kind}:{'null' if shot_ids is None else '[' + ', '.join(shot_ids) + ']'}")
        return {"videoId": video_id, "jobs": [], "provider": "wan"}

    def save_clips(self, video_id, request, result):
        return ok("clips saved")

    def mark_clips(self, video_id, shot_ids, status, error):
        self.ev(f"CLIPS:[{', '.join(shot_ids)}]:{status}")

    def smooth_estimate(self, video_id):
        return {"kind": "smooth", "clips": 0, "inr": 0.0, "needsApproval": False, "summary": "Every clip is already smooth."}

    def smooth_request(self, video_id):
        return {"jobs": []}

    def save_smooth(self, video_id, request, result):
        return ok("smoothed")

    def sharp_estimate(self, video_id):
        return {"kind": "sharp", "clips": 0, "inr": 0.0, "needsApproval": False, "summary": "Every clip is already sharp."}

    def sharp_request(self, video_id):
        return {"jobs": []}

    def save_sharp(self, video_id, request, result):
        return ok("sharpened")

    def assembly_request(self, video_id):
        return {"videoId": video_id}

    def save_master(self, video_id, result):
        self.ev(f"MASTER:{result['masterUri']}")
        return ok("master")

    def footage_search_request(self, video_id, shot_ids, query):
        return {"queries": [{"shotId": f"shot-{i}", "query": "solar flare"} for i in range(self.footage_queries)]}

    def save_footage_candidates(self, video_id, result):
        self.ev(f"FOOTAGE:{len(result['results'])}")
        return ok("footage suggested")

    def footage_fetch_request(self, video_id, shot_id, nasa_id, start_sec):
        return {"videoId": video_id, "shotId": shot_id, "nasaId": nasa_id}

    def save_footage(self, video_id, request, result):
        return ok("footage used")

    def creative_shot_plan_review(self, video_id, attempt):
        self.ev("CD:SHOT_PLAN")
        if self.fail_creative:
            raise ApplicationError("no model answered", type="Test", non_retryable=True)
        return [{"severity": "INFO", "code": "CD_PROMPT_IMPROVED", "message": "rewritten", "ref": "shot 2", "targetId": None}]

    def image_review_request(self, video_id):
        return {"videoId": video_id}

    def save_image_review(self, video_id, attempt, result):
        self.ev(f"CD:KEYFRAMES:{attempt}")
        if self.weak_image_reviews > 0:
            self.weak_image_reviews -= 1
            return [{"severity": "FAIL", "code": "CD_SHOT", "message": "shows a flashlight", "ref": "shot 3", "targetId": "shot-3"}]
        return []

    def fix_keyframe_request(self, video_id, shot_ids, owner_notes):
        self.ev(f"FIX:[{', '.join(shot_ids)}]:{owner_notes}")
        return {"videoId": video_id}

    def inspection_request(self, video_id, stage):
        return {"videoId": video_id, "stage": stage} if stage == "KEYFRAMES" else None

    def save_inspection(self, video_id, stage, attempt, media):
        self.inspected.append(f"{stage}:{attempt}")
        fails = [] if media is None else [f for f in media["findings"] if f["severity"] == "FAIL"]
        if fails:
            return {"verdict": "FAIL", "summary": "Creative Director: weak image", "notes": "fix it", "redoTargets": [f["targetId"] for f in fails]}
        if stage == "DRAFT_GEN" and self.broken_clip_inspections > 0:
            self.broken_clip_inspections -= 1
            return {"verdict": "FAIL", "summary": "Inspector: frozen clip", "notes": "fix", "redoTargets": ["shot-7"]}
        if stage == "KEYFRAMES" and self.keyframe_inspection_fails > 0:
            self.keyframe_inspection_fails -= 1
            return {"verdict": "FAIL", "summary": "Inspector: FAIL", "notes": "shot 3: blank image", "redoTargets": []}
        return {"verdict": "PASS", "summary": "Inspector: all checks passed.", "notes": None, "redoTargets": []}


class FakeMedia:
    def __init__(self):
        self.fail_keyframes = False
        self.fail_voice = False
        self.fail_inspector = False
        self.fail_stock = False

    def defs(self):
        m = self

        def mk(name, fn):
            @activity.defn(name=name)
            def f(req):
                return fn(req)

            return f

        def stock(req):
            if m.fail_stock:
                raise ApplicationError("worker down", type="Test", non_retryable=True)
            if req["shotId"] == "shot0":
                return {"found": True, "uri": "s3://b/stock.mp4"}
            return {"found": False, "reason": "no candidates"}

        def keyframes(req):
            if m.fail_keyframes:
                raise ApplicationError("GPU unavailable", type="Test", non_retryable=True)
            return {"results": [], "modalGpuSeconds": 0}

        def voice(req):
            if m.fail_voice:
                raise ApplicationError("TTS quota", type="Test", non_retryable=True)
            return {"lines": [], "model": "m", "voiceName": "v"}

        def inspect(req):
            if m.fail_inspector:
                raise ApplicationError("ffmpeg missing", type="Test", non_retryable=True)
            return {"findings": [], "checked": []}

        return [
            mk("research_topic", lambda r: {"facts": [], "sources": [], "queries": [], "model": "m"}),
            mk("check_sources", lambda r: r),
            mk("generate_music", lambda r: {"uri": "s3://b/m.wav"}),
            mk("synthesize_narration", voice),
            mk("generate_keyframes", keyframes),
            mk("render_animatic", lambda r: {"uri": "s3://b/a.mp4"}),
            mk("generate_clips", lambda r: {"clips": [], "containers": 1, "stopped": False}),
            mk("smooth_clips", lambda r: {"clips": [], "containers": 1, "stopped": False}),
            mk("upscale_clips", lambda r: {"clips": [], "containers": 1, "stopped": False}),
            mk("assemble_video", lambda r: {"masterUri": "s3://b/master.mp4"}),
            mk("search_footage", lambda r: {"results": [{"shotId": q["shotId"], "query": q["query"]} for q in r["queries"]]}),
            mk("fetch_footage", lambda r: {"uri": "s3://b/f.mp4"}),
            mk("stock_fetch", stock),
            mk("review_images", lambda r: {"review": None}),
            mk("inspect_media", inspect),
        ]


class Harness:
    def __init__(self, env, control, media):
        self.env, self.c, self.m = env, control, media
        self.client = env.client

    async def start(self, wid, default_mode="AUTO", modes=None, only=None, review=None):
        req = VideoRequest(wid, "test prompt", StageMode[default_mode], {Stage[k]: StageMode[v] for k, v in (modes or {}).items()},
                           None if only is None else [Stage[s] for s in only], review)
        return await self.client.start_workflow(queues.VIDEO_WORKFLOW, video_request_json(req), id=wid, task_queue=queues.CONTROL)

    async def await_waiting(self, h, stage):
        for _ in range(400):
            s = await h.query("status")
            if s["phase"] == "WAITING" and s["currentStage"] == stage:
                return
            await self.env.sleep(0.05)
        raise AssertionError(f"Stage {stage} never started waiting")


def run_case(case):
    """Starts a time-skipping server and the workers (control + media) around `case(harness)`."""
    async def main():
        env = await WorkflowEnvironment.start_time_skipping()
        control, media = FakeControl(), FakeMedia()
        with ThreadPoolExecutor(max_workers=8) as pool:
            acts = activity_functions(control, ProducerActivitiesStub())
            async with Worker(env.client, task_queue=queues.CONTROL, workflows=vp.WORKFLOWS, activities=acts, activity_executor=pool), \
                    Worker(env.client, task_queue=queues.MEDIA, activities=media.defs(), activity_executor=pool):
                try:
                    return await case(Harness(env, control, media))
                finally:
                    await env.shutdown()

    return asyncio.run(main())


class ProducerActivitiesStub:
    """No Producer in these tests (PRODUCER mode is covered separately)."""


# ---------------------------------------------------------------------------------------------------- the cases


def test_the_workflow_never_imports_anything_heavy_and_inspector_sets_match():
    assert {s.name for s in inspector.INSPECTED} == vp.INSPECTED and {s.name for s in inspector.PER_SHOT} == vp.PER_SHOT
    assert vp.STAGES == ALL and vp.DEFAULT_REVIEW_MINUTES == 10
    assert vp.BUDGET_ERROR == __import__("studio.ledger.budget_guard", fromlist=["BUDGET_ERROR"]).BUDGET_ERROR


def test_activity_names_cover_everything_the_workflow_calls():
    import re
    from pathlib import Path

    src = Path(vp.__file__).read_text(encoding="utf-8")
    called = set(re.findall(r'_(?:ctl|agents)\(\s*"([a-z_]+)"', src)) | set(re.findall(r'_ctl\(\s*"([a-z_]+)"', src))
    called |= set(re.findall(r'_failed\(v, "([a-z_]+)"', src))
    have = {n.split(".", 1)[1] for n in activity_names() if n.startswith("control.")}
    assert called <= have, called - have
    produced = set(re.findall(r'_producer\(\s*"([a-z_]+)"', src))
    assert produced <= {n.split(".", 1)[1] for n in activity_names() if n.startswith("producer.")}


def test_auto_runs_every_planned_stage_and_skips_the_rest():
    async def case(h):
        w = await h.start("auto")
        await w.result()
        e = h.c.events
        assert "RESEARCH:0:SKIPPED" in e and "MUSIC:0:SKIPPED" in e and "PUBLISH:1:AUTO_PASSED" in e
        assert "MASTER:s3://b/master.mp4" in e, "assembly renders the master"
        assert e[-1] == "VIDEO:COMPLETED"
        assert "STOCK" not in " ".join(e), "lanes off: no stock activity at all"

    run_case(case)


def test_manual_stage_waits_and_reject_reruns_with_notes():
    async def case(h):
        w = await h.start("manual", modes={"PLAN": "MANUAL"})
        await h.await_waiting(w, "PLAN")
        await w.signal("reject", args=["PLAN", "make it shorter"])
        await h.await_waiting(w, "PLAN")
        await w.signal("approve", args=["PLAN", "ok"])
        await w.result()
        assert h.c.notes_seen == ["make it shorter"]
        assert "PLAN:1:REJECTED" in h.c.events and "PLAN:2:APPROVED" in h.c.events and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_switching_a_waiting_stage_to_auto_lets_it_continue():
    async def case(h):
        w = await h.start("switch", default_mode="MANUAL")
        await h.await_waiting(w, "PLAN")
        await w.signal("set_all_modes", "AUTO")
        await w.result()
        assert "PLAN:1:AUTO_PASSED" in h.c.events and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_a_failed_stage_waits_for_the_owner_then_retries_or_skips():
    async def case(h):
        h.m.fail_keyframes = True
        w = await h.start("fail")
        await h.await_waiting(w, "KEYFRAMES")  # failed, even though the stage is AUTO
        assert "KEYFRAMES:1:FAILED" in h.c.events
        await w.signal("reject", args=["KEYFRAMES", "try again"])  # reject = retry
        await h.await_waiting(w, "KEYFRAMES")
        assert "KEYFRAMES:2:FAILED" in h.c.events
        h.m.fail_keyframes = False
        await w.signal("approve", args=["KEYFRAMES", None])  # approve = continue without it
        await w.result()
        assert "KEYFRAMES:2:SKIPPED" in h.c.events and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_inspector_fail_is_redone_once_automatically_with_the_findings_as_notes():
    async def case(h):
        h.c.keyframe_inspection_fails = 1
        w = await h.start("inspect-once")
        await w.result()
        e = h.c.events
        assert "KEYFRAMES:1:INSPECTION_FAILED" in e and "KEYFRAMES:2:AUTO_PASSED" in e
        assert "SHOT_PLAN:1" in h.c.inspected, "every inspected stage is checked"
        assert e[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_inspector_failing_twice_waits_for_the_owner_even_in_auto():
    async def case(h):
        h.c.keyframe_inspection_fails = 2
        w = await h.start("inspect-twice")
        await h.await_waiting(w, "KEYFRAMES")
        assert "KEYFRAMES:2:WAITING_APPROVAL" in h.c.events
        await w.signal("approve", args=["KEYFRAMES", None])  # accept it anyway
        await w.result()
        assert "KEYFRAMES:2:APPROVED" in h.c.events and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_a_weak_image_is_redone_on_its_own_with_the_better_prompt_then_the_plan_continues():
    async def case(h):
        h.c.weak_image_reviews = 1
        w = await h.start("cd-image")
        await w.result()
        e = h.c.events
        assert "CD:SHOT_PLAN" in e, "the shot plan gets a story review"
        assert "KEYFRAMES:1:INSPECTION_FAILED" in e
        assert "FIX:[shot-3]:None" in e, "only the weak shot is regenerated"
        assert "CD:KEYFRAMES:2" in e, "the images are reviewed again"
        assert "KEYFRAMES:2:AUTO_PASSED" in e and e[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_a_still_weak_image_waits_for_the_owner_and_reject_redoes_that_shot_with_their_notes():
    async def case(h):
        h.c.weak_image_reviews = 2
        w = await h.start("cd-image-owner")
        await h.await_waiting(w, "KEYFRAMES")
        await w.signal("reject", args=["KEYFRAMES", "show the Moon's far side in shadow"])
        await w.result()
        assert "FIX:[shot-3]:show the Moon's far side in shadow" in h.c.events and "KEYFRAMES:3:AUTO_PASSED" in h.c.events

    run_case(case)


def test_an_unavailable_creative_director_only_warns():
    async def case(h):
        h.c.fail_creative = True
        w = await h.start("cd-down")
        await w.result()
        assert "SHOT_PLAN:1:AUTO_PASSED" in h.c.events and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_clips_above_the_auto_approve_amount_wait_for_the_owners_go_ahead_even_in_auto():
    async def case(h):
        h.c.clips_need_approval = True
        w = await h.start("clips-gate")
        await h.await_waiting(w, "DRAFT_GEN")
        assert h.c.clip_requests == [], "nothing is sent to the GPU before the owner says yes"
        await w.signal("approve", args=["DRAFT_GEN", None])
        await h.await_waiting(w, "FINAL_GEN")
        await w.signal("reject", args=["FINAL_GEN", "too expensive"])  # declining the spend skips the stage
        await w.result()
        assert h.c.clip_requests == ["draft:null"]
        assert "DRAFT_GEN:1:AUTO_PASSED" in h.c.events and "FINAL_GEN:1:SKIPPED" in h.c.events and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_a_broken_clip_is_redone_on_its_own():
    async def case(h):
        h.c.broken_clip_inspections = 1
        w = await h.start("clips-fix")
        await w.result()
        assert "DRAFT_GEN:1:INSPECTION_FAILED" in h.c.events
        assert "draft:[shot-7]" in h.c.clip_requests, "only the broken shot is rendered again"
        assert "DRAFT_GEN:2:AUTO_PASSED" in h.c.events

    run_case(case)


def test_library_match_suggests_real_footage_for_free():
    async def case(h):
        h.c.footage_queries = 2
        w = await h.start("footage")
        await w.result()
        assert "FOOTAGE:2" in h.c.events and "LIBRARY_MATCH:1:AUTO_PASSED" in h.c.events

    run_case(case)


def test_nothing_to_render_skips_the_clip_stage():
    async def case(h):
        h.c.clips_to_make = 0
        w = await h.start("clips-none")
        await w.result()
        assert "DRAFT_GEN:1:SKIPPED" in h.c.events and h.c.clip_requests == []

    run_case(case)


def test_a_broken_inspector_never_blocks_the_video():
    async def case(h):
        h.m.fail_inspector = True
        w = await h.start("inspect-broken")
        await w.result()
        assert "KEYFRAMES:1:AUTO_PASSED" in h.c.events and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_stock_shots_are_fetched_after_shot_plan_and_a_miss_never_fails_the_video():
    async def case(h):
        h.c.stock_shots = 2
        w = await h.start("stock")
        await w.result()
        assert "STOCK:shot0:found" in h.c.events
        assert "STOCK:shot1:fallback" in h.c.events, "no match: the shot is handed back to the AI lane"
        assert h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_a_failing_stock_worker_falls_back_instead_of_failing_the_stage():
    async def case(h):
        h.c.stock_shots = 1
        h.m.fail_stock = True
        w = await h.start("stock-down")
        await w.result()
        assert "STOCK:shot0:fallback" in h.c.events and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_rerun_runs_only_the_requested_stages_with_continuing_attempt_numbers():
    async def case(h):
        w = await h.start("rerun", only=["ANIMATIC", "NARRATION"])
        await w.result()
        stage_events = [e for e in h.c.events if not e.startswith("VIDEO")]
        assert stage_events == ["NARRATION:3:RUNNING", "NARRATION:3:AUTO_PASSED", "ANIMATIC:3:RUNNING", "ANIMATIC:3:AUTO_PASSED"]

    run_case(case)


def test_review_mode_continues_by_itself_when_the_window_ends():
    async def case(h):
        w = await h.start("review", modes={"SCRIPT": "REVIEW"}, review=10)
        await w.result()  # the test server skips the 10 minutes
        assert "DEADLINE:SCRIPT:set" in h.c.events and "SCRIPT:1:AUTO_APPROVED" in h.c.events and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_review_mode_can_be_approved_early_or_held():
    async def case(h):
        w = await h.start("review2", modes={"CONCEPT": "REVIEW", "SCRIPT": "REVIEW"}, review=10)
        await h.await_waiting(w, "CONCEPT")
        await w.signal("approve", args=["CONCEPT", None])  # approve early
        await h.await_waiting(w, "SCRIPT")
        await w.signal("set_mode", args=["SCRIPT", "MANUAL"])  # hold: the timer must not auto-approve any more
        await h.env.sleep(30 * 60)
        assert (await w.query("status"))["currentStage"] == "SCRIPT"
        assert "DEADLINE:SCRIPT:cleared" in h.c.events
        await w.signal("approve", args=["SCRIPT", "ok"])
        await w.result()
        assert "CONCEPT:1:APPROVED" in h.c.events and "SCRIPT:1:APPROVED" in h.c.events

    run_case(case)


def test_approval_for_a_stage_that_is_not_waiting_is_ignored():
    async def case(h):
        w = await h.start("ignore", modes={"SCRIPT": "MANUAL"})
        await h.await_waiting(w, "SCRIPT")
        await w.signal("approve", args=["CONCEPT", "wrong stage"])  # ignored
        await h.env.sleep(0.2)
        assert (await w.query("status"))["currentStage"] == "SCRIPT"
        await w.signal("approve", args=["SCRIPT", None])
        await w.result()
        assert "SCRIPT:1:APPROVED" in h.c.events

    run_case(case)


def test_pause_holds_the_next_stage_until_resume():
    async def case(h):
        w = await h.start("pause", modes={"SCRIPT": "MANUAL"})
        await h.await_waiting(w, "SCRIPT")
        await w.signal("pause")
        await w.signal("approve", args=["SCRIPT", None])
        await h.env.sleep(5)
        s = await w.query("status")
        assert s["paused"] is True and "VIDEO:PAUSED" in h.c.events and not any(e.startswith("MUSIC") for e in h.c.events if "SKIPPED" not in e)
        await w.signal("resume")
        await w.result()
        assert h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_status_lists_every_stage_mode_and_the_views():
    async def case(h):
        w = await h.start("status", modes={"SCRIPT": "MANUAL"})
        await h.await_waiting(w, "SCRIPT")
        s = await w.query("status")
        assert s["phase"] == "WAITING" and s["defaultMode"] == "AUTO" and s["modes"]["SCRIPT"] == "MANUAL" and len(s["modes"]) == len(ALL)
        assert [v["stage"] for v in s["stages"]][:3] == ["PLAN", "RESEARCH", "CONCEPT"]
        await w.signal("approve", args=["SCRIPT", None])
        await w.result()

    run_case(case)


# ---- the small redo workflows


def test_shot_redo_regenerates_only_the_chosen_shots():
    async def case(h):
        r = await h.client.execute_workflow(queues.SHOT_REDO_WORKFLOW, {"videoId": "v", "shotIds": ["s14"], "ownerNotes": "less cartoonish",
                                                                       "policy": "PREMIUM"}, id="redo-ok", task_queue=queues.CONTROL)
        assert r["summary"] == "keyframes"
        assert h.c.events == ["SHOTS:[s14]:REDOING", "SHOT_REQUEST:[s14]:PREMIUM:less cartoonish"]

    run_case(case)


def test_shot_redo_failure_marks_the_shots_failed():
    async def case(h):
        h.m.fail_keyframes = True
        r = await h.client.execute_workflow(queues.SHOT_REDO_WORKFLOW, {"videoId": "v", "shotIds": ["s1", "s2"], "policy": "FREE"},
                                            id="redo-fail", task_queue=queues.CONTROL)
        assert r["summary"].startswith("Shot redo failed") and h.c.events[-1] == "SHOTS:[s1, s2]:FAILED"

    run_case(case)


def test_shot_redo_budget_refusal_is_not_retried():
    async def case(h):
        h.c.budget_refusal = True
        r = await h.client.execute_workflow(queues.SHOT_REDO_WORKFLOW, {"videoId": "v", "shotIds": ["s1"], "policy": "PREMIUM"}, id="redo-budget",
                                            task_queue=queues.CONTROL)
        assert "cap reached" in r["summary"] and h.c.shot_requests == 1 and h.c.events[-1] == "SHOTS:[s1]:FAILED"

    run_case(case)


def test_voice_redo_revoices_only_the_chosen_lines_with_the_new_style():
    async def case(h):
        r = await h.client.execute_workflow(queues.VOICE_REDO_WORKFLOW, {"videoId": "v", "lineIndexes": [7, 9], "styleNotes": "slower and more hushed"},
                                            id="voice-ok", task_queue=queues.CONTROL)
        assert r["summary"] == "lines saved"
        assert h.c.events == ["LINES:[7, 9]:REDOING", "LINE_REQUEST:[7, 9]:slower and more hushed",
                              "LINE_SAVED:in a voice that is slower and more hushed"]

    run_case(case)


def test_voice_redo_failure_marks_the_lines_failed():
    async def case(h):
        h.m.fail_voice = True
        r = await h.client.execute_workflow(queues.VOICE_REDO_WORKFLOW, {"videoId": "v", "lineIndexes": [3]}, id="voice-fail", task_queue=queues.CONTROL)
        assert r["summary"].startswith("Voice redo failed") and h.c.events[-1] == "LINES:[3]:FAILED"

    run_case(case)


def test_clip_redo_and_footage_and_creative_review_workflows():
    async def case(h):
        r = await h.client.execute_workflow(queues.CLIP_REDO_WORKFLOW, {"videoId": "v", "shotIds": ["a"], "kind": "draft"}, id="clip", task_queue=queues.CONTROL)
        assert r["summary"] == "clips saved" and h.c.clip_requests == ["draft:[a]"]
        f = await h.client.execute_workflow(queues.FOOTAGE_WORKFLOW, {"videoId": "v", "shotId": "s1", "query": "sun"}, id="f1", task_queue=queues.CONTROL)
        assert f["summary"] == "footage suggested"
        f2 = await h.client.execute_workflow(queues.FOOTAGE_WORKFLOW, {"videoId": "v", "shotId": "s1", "nasaId": "N1", "startSec": 2.0}, id="f2",
                                             task_queue=queues.CONTROL)
        assert f2["summary"] == "footage used" and "CLIPS:[s1]:RENDERING" in h.c.events
        c = await h.client.execute_workflow(queues.CREATIVE_REVIEW_WORKFLOW, {"videoId": "v"}, id="cr", task_queue=queues.CONTROL)
        assert c["summary"] == "Creative Director reviewed the images: 0 problem(s), 0 warning(s)." and "CD:KEYFRAMES:2" in h.c.events

    run_case(case)


_ = (uuid, WorkflowFailureError, ControlActivities, ProducerActivities)
