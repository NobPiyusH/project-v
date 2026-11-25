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


class FakePodState:
    """The rented pod as the control plane sees it (one-pod tests): every acquire, keep and release, in order."""

    def __init__(self):
        self.open, self.log, self.lock = False, [], threading.Lock()

    def acquire(self, why):
        with self.lock:
            if not self.open:
                self.open = True
                self.log.append(f"ACQUIRE:{why}")

    def keep(self):
        with self.lock:
            self.log.append("KEEP")

    def release(self, why):
        with self.lock:
            if self.open:
                self.open = False
                self.log.append(f"RELEASE:{why}")

    def count(self, prefix):
        return sum(1 for e in self.log if e.startswith(prefix))


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
        self.quality_gate = False
        self.moon = False  # the plan has a moon block: MOON_LAYER is planned and asks for a composite
        self.lock = threading.Lock()
        self.pod = FakePodState()
        self.one_pod = False  # control.one_pod_enabled: VIDEO_ONE_POD with keyframes and drafts on the pod
        self.one_pod_calls = []  # (keyframe_request one_pod flag, save_keyframes keep_pod flag)
        self.block_animatic = None  # a threading.Event: animatic_request waits for it (the pod is held meanwhile)
        self.at_save = None  # a threading.Event set when save_keyframes runs; it then waits for `proceed`
        self.proceed = None
        self.auto_redo_paid = False  # the video's flag: may a paid automatic redo start without asking
        self.forced_fails = {}  # stage -> how many more inspections fail it (no shot targets)
        self.save_clips_failures = 0  # the next N save_clips calls crash after the media job succeeded
        self.saved_clips = 0
        self.holds = []

    def ev(self, e):
        with self.lock:
            self.events.append(e)

    def plan(self, video_id, prompt, owner_notes, attempt):
        if owner_notes is not None:
            self.notes_seen.append(owner_notes)
        return ok(f"plan v{attempt}", planned=[s for s in ALL if s not in ("RESEARCH", "MUSIC") and (self.moon or s != "MOON_LAYER")])  # skips RESEARCH, MUSIC and (no moon block) MOON_LAYER

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

    def keyframe_request(self, video_id, owner_notes, one_pod=False):
        self.pod.acquire("keyframes")
        self.one_pod_calls.append(("request", one_pod))
        return {"videoId": video_id}

    def save_keyframes(self, video_id, request, result, keep_pod=False):
        self.one_pod_calls.append(("save", keep_pod))
        if self.at_save is not None:
            self.at_save.set()
            self.proceed.wait(20)
        if keep_pod:
            self.pod.keep()
        else:
            self.pod.release("keyframes saved")
        return ok("keyframes")

    def one_pod_enabled(self):
        return self.one_pod

    def touch_held_pod(self, video_id):
        return self.pod.open

    def release_held_pod(self, video_id, why):
        self.pod.release(why)

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
        if self.block_animatic is not None:
            self.block_animatic.wait(20)
        return {"videoId": video_id}

    def save_animatic(self, video_id, result):
        return ok("animatic")

    def record_stage(self, video_id, stage, attempt, mode, status, summary, artifact_json, owner_notes):
        self.ev(f"{stage}:{attempt}:{status}")

    def redo_check(self, video_id, stage, shot_ids):
        from studio.production.redo_policy import redo_is_paid, redo_what
        return {"paid": redo_is_paid(stage), "autoPaid": self.auto_redo_paid, "inr": 42.0, "what": redo_what(stage)}

    def record_hold(self, video_id, stage, attempt, held_reason, can_resume, has_stored_result, redo_estimate_inr):
        self.ev(f"HOLD:{stage}:{attempt}:resume={can_resume}:inr={redo_estimate_inr}")
        self.holds.append(held_reason)

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
        self.pod.acquire("clips")
        return {"videoId": video_id, "jobs": [], "provider": "wan"}

    def save_clips(self, video_id, request, result):
        with self.lock:
            self.saved_clips += 1
            crash = self.save_clips_failures > 0
            self.save_clips_failures -= 1 if crash else 0
        if crash:
            raise ApplicationError("save crashed: missing field", type="Test", non_retryable=True)
        self.pod.release("clips saved")
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
        return {"videoId": video_id, "qualityGate": self.quality_gate}

    def save_master(self, video_id, result):
        self.ev(f"MASTER:{result['masterUri']}")
        return ok("master")

    def moon_request(self, video_id):
        return {"videoId": video_id, "plateUri": "s3://b/master.mp4"} if self.moon else {"skip": "No Moon layer: this plan has no moon block."}

    def save_moon_layer(self, video_id, request, result):
        self.ev(f"MOON:{result['videoUri']}")
        return ok("moon saved")

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
        if self.forced_fails.get(stage, 0) > 0:
            self.forced_fails[stage] -= 1
            return {"verdict": "FAIL", "summary": f"Inspector: {stage} FAIL", "notes": "fix", "redoTargets": []}
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
        self.gate_fails = 0  # the next N gate checks fail
        self.gate_calls = 0
        self.moon_fails = 0  # the next N Moon composites fail (missing picture)
        self.moon_calls = 0
        self.clip_jobs = 0  # generate_clips runs: each one is a pod's work

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

        def gate(req):
            m.gate_calls += 1
            if m.gate_fails > 0:
                m.gate_fails -= 1
                raise ApplicationError("Quality gate: FAIL, 1 failed: freeze: frozen 3.0 s", type="QualityGateFailed", non_retryable=True)
            return {"status": "warn", "summary": "Quality gate: WARN, 1 warning: motion/s2 low.", "reportUri": "s3://b/qg.json", "warnings": ["x"]}

        def moon(req):
            m.moon_calls += 1
            if m.moon_fails > 0:
                m.moon_fails -= 1
                raise ApplicationError("The Moon picture was not found", type="MoonLayerInputMissing", non_retryable=True)
            return {"videoUri": "s3://b/moon-layer.mp4", "durationSec": 10.0, "width": 720, "height": 1280, "fps": 16}

        def clips(req):
            m.clip_jobs += 1
            return {"clips": [], "containers": 1, "stopped": False}

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
            mk("generate_clips", clips),
            mk("smooth_clips", lambda r: {"clips": [], "containers": 1, "stopped": False}),
            mk("upscale_clips", lambda r: {"clips": [], "containers": 1, "stopped": False}),
            mk("quality_gate_check", gate),
            mk("moon_layer_composite", moon),
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


def test_quality_gate_fail_after_the_edit_waits_for_the_owner_and_warns_do_not_block():
    async def case(h):
        h.c.quality_gate, h.m.gate_fails = True, 1
        w = await h.start("gate-fail")
        await h.await_waiting(w, "ASSEMBLY")  # the free gate failed the master: held, even though the stage is AUTO
        assert "ASSEMBLY:1:FAILED" in h.c.events and h.m.gate_calls == 1, "a fail is not retried by Temporal"
        await w.signal("reject", args=["ASSEMBLY", "redo the edit"])  # retry: edit again, gate passes with a warning
        await w.result()
        assert "ASSEMBLY:2:AUTO_PASSED" in h.c.events and h.m.gate_calls == 2 and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_quality_gate_fail_can_be_continued_without_and_off_means_no_check():
    async def case(h):
        h.c.quality_gate, h.m.gate_fails = True, 5
        w = await h.start("gate-skip")
        await h.await_waiting(w, "ASSEMBLY")
        await w.signal("approve", args=["ASSEMBLY", None])  # continue without it
        await w.result()
        assert "ASSEMBLY:1:SKIPPED" in h.c.events and "MASTER:s3://b/master.mp4" in h.c.events and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)
    calls = []

    async def off(h):
        w = await h.start("gate-off")
        await w.result()
        calls.append(h.m.gate_calls)

    run_case(off)
    assert calls == [0]


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
        h.c.auto_redo_paid = True
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
        h.c.auto_redo_paid = True
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
        h.c.auto_redo_paid = True
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
        h.c.auto_redo_paid = True
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
        h.c.auto_redo_paid = True
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


def test_moon_layer_without_a_moon_block_is_not_planned_and_never_calls_the_composite():
    async def case(h):
        w = await h.start("moon-none")
        await w.result()
        assert "MOON_LAYER:0:SKIPPED" in h.c.events and h.m.moon_calls == 0 and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_moon_layer_with_a_moon_block_runs_after_the_edit_and_a_missing_picture_waits_for_the_owner():
    async def case(h):
        h.c.moon, h.m.moon_fails = True, 1
        w = await h.start("moon-fail")
        await h.await_waiting(w, "MOON_LAYER")  # failed stage: held even in AUTO; the video is not killed
        assert "MOON_LAYER:1:FAILED" in h.c.events and h.m.moon_calls == 1, "a missing picture is not retried by Temporal"
        await w.signal("reject", args=["MOON_LAYER", "retry"])
        await w.result()
        assert "MOON_LAYER:2:AUTO_PASSED" in h.c.events and "MOON:s3://b/moon-layer.mp4" in h.c.events and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_moon_layer_failure_can_be_continued_without_and_manual_mode_holds_a_good_composite():
    async def fail(h):
        h.c.moon, h.m.moon_fails = True, 5
        w = await h.start("moon-continue")
        await h.await_waiting(w, "MOON_LAYER")
        await w.signal("approve", args=["MOON_LAYER", None])
        await w.result()
        assert "MOON_LAYER:1:SKIPPED" in h.c.events and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(fail)

    async def manual(h):
        h.c.moon = True
        w = await h.start("moon-manual", modes={"MOON_LAYER": "MANUAL"})
        await h.await_waiting(w, "MOON_LAYER")
        await w.signal("approve", args=["MOON_LAYER", None])
        await w.result()
        assert "MOON_LAYER:1:APPROVED" in h.c.events and "MOON:s3://b/moon-layer.mp4" in h.c.events

    run_case(manual)


# ---------------------------------------------------------------------------------------------------- paid redos and Resume (25 Nov 2026)


def test_a_paid_automatic_redo_waits_for_the_owner_with_the_reason_and_the_estimate():
    async def case(h):
        h.c.keyframe_inspection_fails = 1
        w = await h.start("paid-held")
        await h.await_waiting(w, "KEYFRAMES")
        e = h.c.events
        assert "KEYFRAMES:1:WAITING_APPROVAL" in e and "KEYFRAMES:1:INSPECTION_FAILED" not in e
        assert "HOLD:KEYFRAMES:1:resume=False:inr=42.0" in e
        assert "rent a GPU pod" in h.c.holds[-1] and "about ₹42" in h.c.holds[-1] and "waits for you" in h.c.holds[-1]
        view = next(v for v in (await w.query("status"))["stages"] if v["stage"] == "KEYFRAMES")
        assert view["heldReason"] == h.c.holds[-1] and view["canResume"] is False and view["redoEstimateInr"] == 42.0
        assert h.c.pod.count("ACQUIRE:keyframes") == 1, "no second pod was rented"
        await w.signal("approve", args=["KEYFRAMES", None])  # continue with the current result
        await w.result()
        assert "KEYFRAMES:1:APPROVED" in h.c.events and h.c.pod.count("ACQUIRE:keyframes") == 1 and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_the_owner_can_redo_a_held_paid_redo_and_a_second_fail_still_holds():
    async def case(h):
        h.c.keyframe_inspection_fails = 2
        w = await h.start("paid-redo")
        await h.await_waiting(w, "KEYFRAMES")
        await w.signal("reject", args=["KEYFRAMES", "again please"])
        await h.await_waiting(w, "KEYFRAMES")  # the redo failed again: the cap of one automatic redo holds it as before
        assert "KEYFRAMES:2:WAITING_APPROVAL" in h.c.events and h.c.pod.count("ACQUIRE:keyframes") == 2
        await w.signal("approve", args=["KEYFRAMES", None])
        await w.result()

    run_case(case)


def test_a_free_automatic_redo_stays_automatic():
    async def case(h):
        h.c.forced_fails = {"ANIMATIC": 1}  # local ffmpeg: no pod, no paid call
        w = await h.start("free-redo")
        await w.result()
        assert "ANIMATIC:1:INSPECTION_FAILED" in h.c.events and "ANIMATIC:2:AUTO_PASSED" in h.c.events
        assert not any(x.startswith("HOLD:ANIMATIC") for x in h.c.events)

    run_case(case)


def test_with_auto_redo_paid_on_the_paid_redo_starts_by_itself():
    async def case(h):
        h.c.auto_redo_paid = True
        h.c.keyframe_inspection_fails = 1
        w = await h.start("paid-auto")
        await w.result()
        assert "KEYFRAMES:1:INSPECTION_FAILED" in h.c.events and "KEYFRAMES:2:AUTO_PASSED" in h.c.events
        assert h.c.pod.count("ACQUIRE:keyframes") == 2 and not any(x.startswith("HOLD:KEYFRAMES") for x in h.c.events)

    run_case(case)


def test_resume_after_a_save_crash_reuses_the_stored_result_and_rents_no_pod():
    async def case(h):
        h.c.save_clips_failures = 1
        w = await h.start("resume")
        await h.await_waiting(w, "DRAFT_GEN")
        assert "DRAFT_GEN:1:FAILED" in h.c.events and "HOLD:DRAFT_GEN:1:resume=True:inr=None" in h.c.events
        view = next(v for v in (await w.query("status"))["stages"] if v["stage"] == "DRAFT_GEN")
        assert view["canResume"] is True and view["hasStoredResult"] is True and "save crashed" in view["heldReason"]
        assert h.m.clip_jobs == 1 and h.c.clip_requests == ["draft:null"]
        await w.signal("resume_stage", "DRAFT_GEN")
        await w.result()
        assert h.c.saved_clips == 3, "the save ran again (draft crash + draft resume + final)"
        assert h.c.clip_requests.count("draft:null") == 1, "no second draft batch: the pod's work was kept"
        assert "DRAFT_GEN:2:AUTO_PASSED" in h.c.events and h.c.events[-1] == "VIDEO:COMPLETED"

    run_case(case)


def test_retry_after_a_save_crash_still_redoes_the_whole_stage():
    async def case(h):
        h.c.save_clips_failures = 1
        w = await h.start("retry")
        await h.await_waiting(w, "DRAFT_GEN")
        await w.signal("reject", args=["DRAFT_GEN", None])
        await w.result()
        assert h.c.clip_requests.count("draft:null") == 2, "Retry makes the clips again"
        assert "DRAFT_GEN:2:AUTO_PASSED" in h.c.events

    run_case(case)


def test_resume_is_not_offered_when_the_media_job_itself_failed():
    async def case(h):
        h.m.fail_keyframes = True
        w = await h.start("no-resume")
        await h.await_waiting(w, "KEYFRAMES")
        assert "HOLD:KEYFRAMES:1:resume=False:inr=None" in h.c.events
        await w.signal("resume_stage", "KEYFRAMES")  # ignored: nothing is stored
        await h.env.sleep(1)
        assert (await w.query("status"))["phase"] == "WAITING"
        await w.signal("approve", args=["KEYFRAMES", None])
        await w.result()

    run_case(case)
