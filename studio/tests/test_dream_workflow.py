"""Port of DreamShortWorkflowTest: step order, Auto/Review/Manual/Producer gates, the clips' cost gate and failure -> wait for Retry, with
in-memory fake activities (no DB, no LLM, no media worker) on Temporal's time-skipping test server (no network, no spend)."""

import asyncio
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytest.importorskip("temporalio")

from temporalio import activity  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402
from temporalio.worker import Worker  # noqa: E402

from studio.workflow import dream_workflow as dw  # noqa: E402
from studio.workflow import queues  # noqa: E402
from studio.workflow.dream_activities import DreamActivities, DreamProducerActivities, activity_functions, activity_names  # noqa: E402

DREAM_ID = "00000000-0000-0000-0000-000000000001"


class FakeDream:
    """Same method names as DreamActivities (the real names are `dream.<method>`)."""

    def __init__(self):
        self.events, self.picture_notes = [], []
        self.writes = 0
        self.ask_for_cost = False
        self.fail_pictures_times = 0
        self.last_clip_shots = None
        self.music_source_ = "bed"
        self.scout = True
        self.ahead: dict[str, list[int]] = {}
        self.lock = threading.Lock()

    def ev(self, e):
        with self.lock:
            self.events.append(e)

    def write_shots(self, dream_id, notes):
        self.ev("write" + ("" if notes is None else f":{notes}"))
        self.writes += 1
        return f"write v{self.writes}"

    def picture_request(self, dream_id, shot_ids, notes):
        self.picture_notes.append(f"{shot_ids}:{notes}")
        if self.fail_pictures_times > 0:
            self.fail_pictures_times -= 1
            raise RuntimeError("GPU cap reached")
        return {"videoId": dream_id, "jobs": [{"shotId": "s1", "attempt": 1, "provider": "modal_zimage", "prompt": "p"}]}

    def save_pictures(self, dream_id, request, result):
        self.ev("pictures")
        return "pictures"

    def clip_estimate(self, dream_id, shot_ids):
        return {"step": "CLIPS", "inr": 10.0, "minutes": 3, "needsApproval": self.ask_for_cost, "summary": "about ₹10"}

    def clip_request(self, dream_id, shot_ids):
        self.last_clip_shots = shot_ids
        return {"videoId": dream_id, "jobs": [{"shotId": "s1"}]}

    def save_clips(self, dream_id, request, result):
        self.ev("clips")
        return "clips"

    def music_request(self, dream_id, notes):
        return {"videoId": dream_id, "model": "lyria", "prompt": "p"}

    def save_music(self, dream_id, result):
        self.ev("music")
        self.ev("music:lyria")
        return "music"

    def check_request(self, dream_id):
        return {"dreamId": dream_id, "videoUri": "s3://final"}

    def picture_check_request(self, dream_id):
        return {"dreamId": dream_id, "pictureUris": ["s3://img"]}

    def save_picture_check(self, dream_id, result):
        self.ev(f"picture-check:{result['overall']}")
        return "pictures checked"

    def save_check(self, dream_id, result):
        self.ev(f"check:{result['overall']}")
        return "checked"

    def scout_wanted(self, dream_id):
        return self.scout

    def scout_request(self, dream_id, notes):
        return {"dreamId": dream_id, "query": "idea" + ("" if notes is None else f" {notes}")}

    def save_scout(self, dream_id, request, result):
        self.ev(f"scout:{request['query']}:{len(result['videos'])}")
        return "scouted"

    def music_source(self, dream_id):
        return self.music_source_

    def music_bed_request(self, dream_id):
        return {"dreamId": dream_id, "bedUri": "s3://b/bed.m4a"}

    def save_music_bed(self, dream_id, result):
        self.ev("music")
        self.ev(f"music:bed:{result['startSec']}")
        return "music"

    def finish_request(self, dream_id):
        return {"dreamId": dream_id, "look": "dreamcore"}

    def save_finish(self, dream_id, result):
        self.ev("finish")
        return "finished"

    def status(self, dream_id, step, status, message):
        self.ev(f"status:{step}:{status}")

    def start_clip_batch(self, dream_id):
        pass

    def gpu_batch_ahead(self, dream_id, step):
        """Other Shorts ahead of this one on the shared pod, per step; each call takes the next value (last one repeats)."""
        with self.lock:
            q = self.ahead.get(step)
            if not q:
                return 0
            return q.pop(0) if len(q) > 1 else q[0]


class FakeProducer:
    """The AI Producer stand-in: verdicts in order (then approve), and whether it may act alone."""

    def __init__(self):
        self.verdicts: list[str] = []
        self.acted, self.reviewed = [], []
        self.decides_ = True
        self.spend_approve = True
        self.max_rounds = 3

    def review_request(self, dream_id, step):
        if step == "SCOUT":
            return None
        return {"videoId": dream_id, "stage": f"DREAM_{step}", "items": [{"id": "shot-1"}]}

    def save_review(self, dream_id, step, attempt, result):
        self.reviewed.append(step)
        v = self.verdicts.pop(0) if step == "PICTURES" and self.verdicts else "approve"
        return {"verdict": v, "confidence": 0.9, "summary": f"advice {v}", "redoNotes": "sharper edges", "decisionId": f"d-{step}{attempt}",
                "weakItems": ["shot-1"] if v == "redo" else []}

    def decides(self, dream_id):
        return self.decides_

    def may_redo(self, dream_id, round_):
        return round_ <= self.max_rounds

    def shot_ids(self, dream_id, item_ids):
        return ["s1"]

    def spend_decision(self, dream_id, estimate_inr):
        return {"approve": self.spend_approve, "text": "approved" if self.spend_approve else "Producer asks: over ₹50"}

    def mark_acted(self, decision_id, acted):
        self.acted.append(f"{decision_id}:{acted}")


def media_defs():
    """The Python media worker's activities the Dream workflow calls, by name."""
    def mk(name, fn):
        @activity.defn(name=name)
        def f(req):
            return fn(req)
        return f

    return [
        mk("generate_keyframes", lambda r: {"results": [{"shotId": "s1", "uri": "s3://img", "provider": "modal_zimage", "model": "z", "seed": 1,
                                                          "seconds": 1.0, "usage": None, "error": None, "references": []}], "modalGpuSeconds": 1.0}),
        mk("generate_clips", lambda r: {"clips": [{"shotId": "s1", "take": 1, "kind": "dream", "uri": "s3://clip", "gpuSeconds": 20, "frames": 57,
                                                   "fps": 16, "mode": "fast", "error": None, "outputTokens": 0}], "containers": 1, "stopped": False}),
        mk("generate_music", lambda r: {"uri": "s3://music", "durationSec": 16, "bpm": 80, "beatTimes": [], "downbeatTimes": [], "segments": 1,
                                        "model": "lyria", "usage": []}),
        mk("dream_finish", lambda r: {"uri": "s3://final", "durationSec": 15, "width": 1080, "height": 1920, "fps": 24, "smoothing": "ffmpeg",
                                      "music": None, "error": None, "metrics": {}}),
        mk("dream_check", lambda r: {"scores": {"noPeople": 10}, "overall": 9, "issues": [], "summary": "fine", "model": "gemini", "usage": None,
                                     "error": None}),
        mk("dream_scout", lambda r: {"videos": [{"url": "u", "title": "t", "shows": "a kiosk"}], "usage": [], "source": "youtube", "searches": 1,
                                     "error": None}),
        mk("dream_music_bed", lambda r: {"uri": "s3://bed-piece", "durationSec": 16.5, "startSec": 754.2, "bedDurationSec": 3600,
                                         "bedName": "bed.m4a", "model": None, "usage": None, "why": None, "heard": 1}),
        mk("producer_review", lambda r: {"verdict": "x", "confidence": 0.9, "summary": "s", "redoNotes": "n", "items": [], "model": "m", "usage": None}),
    ]


class Harness:
    def __init__(self, env, dream, producer):
        self.env, self.d, self.p, self.client = env, dream, producer, env.client

    async def start(self, default_mode="AUTO", modes=None, from_step=None, review=10):
        return await self.client.start_workflow(
            queues.DREAM_WORKFLOW, {"dreamId": DREAM_ID, "defaultMode": default_mode, "modes": modes or {}, "reviewMinutes": review,
                                    "fromStep": from_step}, id="dream-test", task_queue=queues.CONTROL)

    async def wait_for(self, h, step, status):
        v = None
        for _ in range(400):
            v = await h.query("view")
            if v["step"] == step and v["status"] == status:
                return v
            await self.env.sleep(0.05)
        raise AssertionError(f"never reached {step} {status}; at {v}")


def run_case(case, setup=None):
    async def main():
        env = await WorkflowEnvironment.start_time_skipping()
        dream, producer = FakeDream(), FakeProducer()
        if setup:
            setup(dream, producer)
        with ThreadPoolExecutor(max_workers=8) as pool:
            acts = activity_functions(dream, producer)
            async with Worker(env.client, task_queue=queues.CONTROL, workflows=dw.WORKFLOWS, activities=acts, activity_executor=pool), \
                    Worker(env.client, task_queue=queues.MEDIA, activities=media_defs(), activity_executor=pool):
                try:
                    return await case(Harness(env, dream, producer))
                finally:
                    await env.shutdown()

    return asyncio.run(main())


def work(events):
    return [e for e in events if not e.startswith("status") and not e.startswith("music:") and "check:" not in e]


def test_the_activity_names_cover_everything_the_workflow_calls():
    src = Path(dw.__file__).read_text(encoding="utf-8")
    called = set(re.findall(r'_d\(\s*"([a-z_]+)"', src)) | set(re.findall(r"_d\(\s*'([a-z_]+)'", src))
    have = {n.split(".", 1)[1] for n in activity_names() if n.startswith("dream.")}
    assert called <= have, called - have
    produced = set(re.findall(r'_p\(\s*"([a-z_]+)"', src))
    assert produced <= {n.split(".", 1)[1] for n in activity_names() if n.startswith("dreamProducer.")}
    assert dw.STEPS == ["SCOUT", "WRITE", "PICTURES", "CLIPS", "MUSIC", "FINISH"]
    assert dw.BUDGET_ERROR == __import__("studio.ledger.budget_guard", fromlist=["BUDGET_ERROR"]).BUDGET_ERROR
    assert DreamActivities and DreamProducerActivities


def test_auto_runs_all_the_steps_in_order():
    async def case(h):
        w = await h.start()
        assert await w.result() == "completed"
        assert work(h.d.events) == ["scout:idea:1", "write", "pictures", "clips", "music", "finish"]
        assert "status:FINISH:COMPLETED" in h.d.events
        assert "music:bed:754.2" in h.d.events, "the owner's track is the default music"
        assert "check:9" in h.d.events, "every finished Short gets a quality check"
        assert "picture-check:9" in h.d.events, "the pictures are checked before clips"

    run_case(case)


def test_lyria_is_used_only_when_chosen_for_the_short():
    def setup(d, p):
        d.music_source_ = "lyria"

    async def case(h):
        await (await h.start()).result()
        assert "music:lyria" in h.d.events and not any(e.startswith("music:bed") for e in h.d.events)

    run_case(case, setup)


def test_a_manual_step_waits_for_the_owner_and_redo_uses_the_notes():
    async def case(h):
        w = await h.start(modes={"WRITE": "MANUAL"})
        await h.wait_for(w, "WRITE", "WAITING")
        await w.signal("redo", args=["WRITE", None, "more mushrooms"])
        await h.wait_for(w, "WRITE", "WAITING")
        assert "write:more mushrooms" in h.d.events
        await w.signal("approve", args=["WRITE"])
        assert await w.result() == "completed" and h.d.writes == 2

    run_case(case)


def test_review_goes_on_by_itself_after_the_window():
    async def case(h):
        w = await h.start(modes={"PICTURES": "REVIEW"})
        v = await h.wait_for(w, "PICTURES", "WAITING_REVIEW")
        assert v["deadline"] is not None
        await h.env.sleep(11 * 60)
        assert await w.result() == "completed"

    run_case(case)


def test_clips_wait_for_cost_approval_and_a_redo_targets_only_the_chosen_shots():
    def setup(d, p):
        d.ask_for_cost = True

    async def case(h):
        w = await h.start(modes={"CLIPS": "MANUAL"})
        await h.wait_for(w, "CLIPS", "WAITING_COST")
        assert not any(e.startswith("clips") for e in h.d.events)
        await w.signal("approve", args=["CLIPS"])
        await h.wait_for(w, "CLIPS", "WAITING")
        await w.signal("redo", args=["CLIPS", ["s1"], None])
        await h.wait_for(w, "CLIPS", "WAITING_COST")
        await w.signal("approve", args=["CLIPS"])
        await h.wait_for(w, "CLIPS", "WAITING")
        assert h.d.last_clip_shots == ["s1"]
        await w.signal("approve", args=["CLIPS"])
        assert await w.result() == "completed"

    run_case(case, setup)


def test_a_failed_step_waits_for_retry_instead_of_ending_the_short():
    def setup(d, p):
        d.fail_pictures_times = 3  # every automatic attempt fails

    async def case(h):
        w = await h.start()
        v = await h.wait_for(w, "PICTURES", "FAILED_WAITING")
        assert "GPU cap reached" in v["message"]
        await w.signal("retry")
        assert await w.result() == "completed"

    run_case(case, setup)


def test_redo_from_music_keeps_the_earlier_steps():
    async def case(h):
        w = await h.start(from_step="MUSIC")
        assert await w.result() == "completed"
        assert work(h.d.events) == ["music", "finish"]

    run_case(case)


def test_cancel_ends_the_short_while_waiting():
    async def case(h):
        w = await h.start(default_mode="MANUAL")
        await h.wait_for(w, "SCOUT", "WAITING")  # in Manual the Scout is the first step that waits
        await w.signal("cancel")
        assert await w.result() == "cancelled"
        assert "status:SCOUT:CANCELLED" in h.d.events

    run_case(case)


def test_the_scout_can_be_switched_off_for_a_short():
    def setup(d, p):
        d.scout = False

    async def case(h):
        await (await h.start()).result()
        assert not any(e.startswith("scout") for e in h.d.events)

    run_case(case, setup)


def test_a_scout_redo_searches_again_with_the_owners_notes():
    async def case(h):
        w = await h.start(modes={"SCOUT": "MANUAL"})
        await h.wait_for(w, "SCOUT", "WAITING")
        await w.signal("redo", args=["SCOUT", None, "at night"])
        await h.wait_for(w, "SCOUT", "WAITING")
        assert "scout:idea at night:1" in h.d.events
        await w.signal("approve", args=["SCOUT"])
        assert await w.result() == "completed"

    run_case(case)


def test_the_mode_can_be_switched_mid_run():
    async def case(h):
        w = await h.start(modes={"WRITE": "MANUAL"})
        await h.wait_for(w, "WRITE", "WAITING")
        await w.signal("set_mode", args=["WRITE", "AUTO"])
        assert await w.result() == "completed"
        view = await w.query("view")
        assert view["modes"]["WRITE"] == "AUTO" and view["status"] == "COMPLETED"

    run_case(case)


def test_clips_wait_their_turn_while_other_shorts_make_pictures():
    def setup(d, p):
        d.ahead["CLIPS"] = [2, 1, 0]

    async def case(h):
        w = await h.start()
        assert await w.result() == "completed"
        e = h.d.events
        waiting = e.index("status:CLIPS:WAITING_BATCH")
        assert max(i for i, x in enumerate(e) if x == "status:PICTURES:RUNNING") < waiting

    run_case(case, setup)


def test_start_now_skips_the_batch_wait():
    def setup(d, p):
        d.ahead["PICTURES"] = [1]

    async def case(h):
        w = await h.start()
        await h.wait_for(w, "PICTURES", "WAITING_BATCH")
        await w.signal("approve", args=["PICTURES"])
        assert await w.result() == "completed"

    run_case(case, setup)


def test_the_batch_wait_is_capped_so_a_short_is_never_stuck():
    def setup(d, p):
        d.ahead["CLIPS"] = [1]  # someone is always ahead

    async def case(h):
        assert await (await h.start()).result() == "completed"  # goes on by itself after BATCH_WAIT

    run_case(case, setup)


# ------------------------------------------------------------------------------------------------ the AI Producer


def test_a_deciding_producer_redoes_weak_shots_with_its_notes_then_approves():
    def setup(d, p):
        p.verdicts.append("redo")

    async def case(h):
        w = await h.start(default_mode="PRODUCER", modes={"SCOUT": "AUTO"})
        assert await w.result() == "completed"
        assert h.d.picture_notes == ["None:None", "['s1']:sharper edges"]
        assert "d-PICTURES1:redo" in h.p.acted and "d-PICTURES2:approved" in h.p.acted
        assert h.p.reviewed == ["WRITE", "PICTURES", "PICTURES", "CLIPS", "MUSIC", "FINISH"]

    run_case(case, setup)


def test_the_producer_stops_redoing_after_its_rounds_and_asks_the_owner():
    def setup(d, p):
        p.max_rounds = 1
        p.verdicts.extend(["redo", "redo"])

    async def case(h):
        w = await h.start(default_mode="PRODUCER", modes={"SCOUT": "AUTO"})
        v = await h.wait_for(w, "PICTURES", "WAITING")
        assert "advice redo" in v["message"]
        await w.signal("approve", args=["PICTURES"])
        assert await w.result() == "completed"
        assert "d-PICTURES2:asked" in h.p.acted, "you answered inside the 30 s: it counts as asked"

    run_case(case, setup)


def test_an_advising_producer_leaves_the_decision_to_the_owner():
    def setup(d, p):
        p.decides_ = False

    async def case(h):
        w = await h.start(modes={"WRITE": "PRODUCER"})
        v = await h.wait_for(w, "WRITE", "WAITING")
        assert "advice approve" in v["message"] and h.p.acted == []
        await w.signal("approve", args=["WRITE"])
        assert await w.result() == "completed"

    run_case(case, setup)


def test_an_advising_producer_decides_alone_when_the_owner_stays_silent_for_30_seconds():
    def setup(d, p):
        p.decides_ = False

    async def case(h):
        assert await (await h.start(modes={"WRITE": "PRODUCER"})).result() == "completed"
        assert "d-WRITE1:approved" in h.p.acted

    run_case(case, setup)


def test_a_producer_out_of_rounds_goes_on_after_30_seconds_instead_of_waiting_forever():
    def setup(d, p):
        p.max_rounds = 1
        p.verdicts.extend(["redo", "redo"])

    async def case(h):
        assert await (await h.start(default_mode="PRODUCER", modes={"SCOUT": "AUTO"})).result() == "completed"
        assert "d-PICTURES2:approved" in h.p.acted

    run_case(case, setup)


def test_a_deciding_producer_approves_the_clip_cost_inside_the_limits():
    def setup(d, p):
        d.ask_for_cost = True

    async def case(h):
        assert await (await h.start(modes={"CLIPS": "PRODUCER"})).result() == "completed"
        assert "status:CLIPS:WAITING_COST" not in h.d.events

    run_case(case, setup)


def test_a_clip_cost_above_the_limits_waits_for_the_owner_with_the_producers_reason():
    def setup(d, p):
        d.ask_for_cost = True
        p.spend_approve = False

    async def case(h):
        w = await h.start(modes={"CLIPS": "PRODUCER"})
        v = await h.wait_for(w, "CLIPS", "WAITING_COST")
        assert "over ₹50" in v["message"]
        await w.signal("approve", args=["CLIPS"])
        assert await w.result() == "completed"

    run_case(case, setup)
