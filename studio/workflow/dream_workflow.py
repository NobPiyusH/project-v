"""One Dream Short: scout -> write -> pictures -> clips -> music -> finish, each step in Auto, Review or Manual (rule 2a). Port of
DreamShortWorkflowImpl.

Deterministic (rule 5): every DB, LLM, GPU and file operation is an activity; the workflow only sees IDs and text.
- each step runs, then passes its gate: AUTO goes on; REVIEW waits the review window then goes on by itself; MANUAL waits for the owner
  (approve, or redo with notes / for some shots); PRODUCER lets the AI Producer judge it;
- clips always show their cost first and wait for the owner's OK when it is above the auto-approve amount (owner rule: ask before spending);
- a failed step waits for the owner's Retry instead of ending the Short (rule 2b).

A NEW workflow type (DreamShortWorkflowPy on the Python control queue): Java's running Shorts keep their worker, so there is no getVersion
history to replay and every feature the Java version gated behind a version marker is simply on."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from studio.workflow import queues

STEPS = ["SCOUT", "WRITE", "PICTURES", "CLIPS", "MUSIC", "FINISH"]
BUDGET_ERROR = "BudgetExceededError"  # never retried: the step waits for the owner
BATCH_WAIT = timedelta(minutes=30)
BATCH_POLL = timedelta(seconds=30)
PRODUCER_WINDOW = timedelta(seconds=30)


def _retry(attempts: int, no_retry: list[str] | None = None) -> RetryPolicy:
    return RetryPolicy(maximum_attempts=attempts, non_retryable_error_types=no_retry or [])


DREAM = dict(start_to_close_timeout=timedelta(minutes=6), retry_policy=_retry(3, [BUDGET_ERROR]))  # the writer's LLM call can take minutes
# Outer cap only: on the shared pod a Short's pictures can wait behind other Shorts' jobs. Each job's own limit counts from when the pod
# starts it (workers/pod/client.py RUN_LIMIT_S). The worker beats while a job is queued or running; 12 min rides out a laptop stall.
PICTURES = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(hours=4), heartbeat_timeout=timedelta(minutes=12), retry_policy=_retry(1))
# Never retried automatically: a retry would pay for the clips again. The owner's Retry decides.
CLIPS = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(hours=8), heartbeat_timeout=timedelta(minutes=12), retry_policy=_retry(1))
MUSIC = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(minutes=15), retry_policy=_retry(1))  # paid (Lyria): no auto retry
FINISH = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(hours=1), retry_policy=_retry(2))
PRODUCER = dict(start_to_close_timeout=timedelta(minutes=2), retry_policy=_retry(3))
PRODUCER_MEDIA = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(minutes=20), retry_policy=_retry(2))


def root_message(e: BaseException) -> str:
    c = e
    while c.__cause__ is not None:
        c = c.__cause__
    m = str(c) or type(c).__name__
    return m[:400]


def working(step: str) -> str:
    """What the dashboard says while a step runs (so an old "approve" message never lingers)."""
    return {"SCOUT": "Finding and watching similar Shorts…", "WRITE": "Writing the world and the shots…", "PICTURES": "Making the pictures…",
            "CLIPS": "Making the clips on the GPU…", "MUSIC": "Making the music…",
            "FINISH": "Finishing on the laptop: smoothing, dissolves, look, music…"}[step]


@dataclass
class _Redo:
    step: str
    shot_ids: list[str] | None
    notes: str | None


async def _d(name: str, *args: Any, opts: dict[str, Any] = DREAM) -> Any:
    return await workflow.execute_activity(f"dream.{name}", args=list(args), **opts)


async def _p(name: str, *args: Any) -> Any:
    return await workflow.execute_activity(f"dreamProducer.{name}", args=list(args), **PRODUCER)


async def _m(name: str, req: Any, opts: dict[str, Any]) -> Any:
    return await workflow.execute_activity(name, req, **opts)


@workflow.defn(name=queues.DREAM_WORKFLOW)
class DreamShortWorkflow:
    def __init__(self) -> None:
        self.reviews: dict[str, int] = {}
        self.producer_rounds: dict[str, int] = {}
        self.dream_id = ""
        self.spend_note: str | None = None  # the Producer's reason for asking about a cost
        self.default_mode = "AUTO"
        self.modes: dict[str, str] = {}
        self.review_window = timedelta(minutes=10)
        self.current: str | None = None
        self.status = "RUNNING"
        self.message: str | None = None
        self.deadline: str | None = None
        self.approved: str | None = None
        self.redo: _Redo | None = None
        self.retry_flag = False
        self.cancelled = False

    # ------------------------------------------------------------------------------------------- signals & query

    @workflow.signal
    def approve(self, step: str) -> None:
        self.approved = step

    @workflow.signal(name="redo")
    def redo_step(self, step: str, shot_ids: list[str] | None = None, notes: str | None = None) -> None:
        self.redo = _Redo(step, shot_ids, notes)

    @workflow.signal
    def set_mode(self, step: str, mode: str) -> None:
        self.modes[step] = mode

    @workflow.signal
    def retry(self) -> None:
        self.retry_flag = True

    @workflow.signal
    def cancel(self) -> None:
        self.cancelled = True

    @workflow.query
    def view(self) -> dict[str, Any]:
        return {"step": self.current, "status": self.status, "message": self.message, "modes": {s: self._mode(s) for s in STEPS},
                "deadline": self.deadline}

    # ------------------------------------------------------------------------------------------- the run

    @workflow.run
    async def run(self, request: dict[str, Any]) -> str:
        self.dream_id = request["dreamId"]
        self.default_mode = request.get("defaultMode") or "AUTO"
        self.modes.update(request.get("modes") or {})
        self.review_window = timedelta(minutes=max(1, request.get("reviewMinutes") or 10))
        start = STEPS.index(request.get("fromStep") or "SCOUT")
        for i, step in enumerate(STEPS):
            if i < start:
                continue  # redo from a later step: earlier results stay as they are
            if step == "SCOUT" and not await _d("scout_wanted", self.dream_id):
                continue  # the owner switched the Scout off for this Short
            if not await self._run_step(step):
                await self._show(step, "CANCELLED", "Cancelled by the owner.")
                return "cancelled"
        await self._show("FINISH", "COMPLETED", self.message)
        return "completed"

    async def _run_step(self, step: str) -> bool:
        """Runs one step until the owner (or its mode) accepts it. False = cancelled."""
        self.current = step
        only: list[str] | None = None
        notes: str | None = None
        while True:
            if self.cancelled:
                return False
            await self._show(step, "RUNNING", working(step))
            try:
                summary = await self._execute(step, only, notes)
            except ActivityError as failure:
                await self._show(step, "FAILED_WAITING", f"Failed: {root_message(failure)} Retry when ready, or cancel.")
                self.retry_flag = False
                await workflow.wait_condition(lambda: self.retry_flag or self.cancelled)
                continue
            if summary is None:
                return False  # cancelled while waiting for the clips' go-ahead
            self.message = summary
            r = await self._gate(step, summary)
            if self.cancelled:
                return False
            if r is None:
                return True
            only = r.shot_ids or None
            notes = r.notes
            if step == "WRITE":
                only = None  # the writer always rewrites the whole Short (one world)

    async def _execute(self, step: str, only: list[str] | None, notes: str | None) -> str | None:
        d = self.dream_id
        if step == "SCOUT":
            req = await _d("scout_request", d, notes)
            return await _d("save_scout", d, req, await _m("dream_scout", req, FINISH))
        if step == "WRITE":
            return await _d("write_shots", d, notes)
        if step == "PICTURES":
            if not await self._wait_for_batch(step):
                return None
            req = await _d("picture_request", d, only, notes)
            made = ("Every shot already has a picture." if not req["jobs"]
                    else await _d("save_pictures", d, req, await _m("generate_keyframes", req, PICTURES)))
            if req["jobs"]:  # a check of the pictures before clips are paid for; a failed check never holds up the pictures
                try:
                    check = await _d("picture_check_request", d)
                    if check is not None:
                        made = f"{made} {await _d('save_picture_check', d, await _m('dream_check', check, FINISH))}"
                except ActivityError:
                    pass
            return made
        if step == "CLIPS":
            if not await self._wait_for_batch(step):
                return None
            est = await _d("clip_estimate", d, only)
            if est["needsApproval"] and not await self._producer_approves_spend(step, est):
                self.approved = None
                await self._show(step, "WAITING_COST", est["summary"] + ("" if self.spend_note is None else " " + self.spend_note) + " Approve to start.")
                self.spend_note = None
                await workflow.wait_condition(lambda: self.approved == "CLIPS" or self.cancelled)
                self.approved = None
                if self.cancelled:
                    return None
                await self._show(step, "RUNNING", working(step))
            await _d("start_clip_batch", d)
            req = await _d("clip_request", d, only)
            return await _d("save_clips", d, req, await _m("generate_clips", req, CLIPS))
        if step == "MUSIC":
            if await _d("music_source", d) == "bed":
                return await _d("save_music_bed", d, await _m("dream_music_bed", await _d("music_bed_request", d), FINISH))
            return await _d("save_music", d, await _m("generate_music", await _d("music_request", d, notes), MUSIC))
        # FINISH
        done = await _d("save_finish", d, await _m("dream_finish", await _d("finish_request", d), FINISH))
        try:  # the quality check after the finish; a failed check never holds up a finished Short
            req = await _d("check_request", d)
            if req is not None:
                done = f"{done} {await _d('save_check', d, await _m('dream_check', req, FINISH))}"
        except ActivityError:
            pass
        return done

    async def _wait_for_batch(self, step: str) -> bool:
        """Batch order on the shared pod: waits (at most BATCH_WAIT) while other working Shorts should use the pod first; the owner's
        approve(step) starts it at once. Scheduling only: the step's mode and gate are unchanged. False = cancelled while waiting."""
        until = workflow.now() + BATCH_WAIT
        self.approved = None
        waited = False
        while not self.cancelled and self.approved != step and workflow.now() < until:
            ahead = await _d("gpu_batch_ahead", self.dream_id, step)
            if ahead <= 0:
                break
            waited = True
            what = ("Waiting for %d other Short(s) to finish their pictures, so the GPU makes all clips back to back." if step == "CLIPS"
                    else "Waiting for %d other Short(s) to finish writing, so one GPU is rented for the whole batch.") % ahead
            await self._show(step, "WAITING_BATCH", what + " Start now to skip the wait.")
            await self._await(lambda: self.cancelled or self.approved == step, BATCH_POLL)
        self.approved = None
        if waited and not self.cancelled:
            await self._show(step, "RUNNING", working(step))
        return not self.cancelled

    async def _gate(self, step: str, summary: str) -> _Redo | None:
        """None = accepted; otherwise the owner's redo."""
        self.approved = None
        self.redo = None
        mode = self._mode(step)
        if mode == "AUTO":
            return None
        if mode == "MANUAL":
            await self._show(step, "WAITING", summary)
            await workflow.wait_condition(lambda: self._decided(step) or self._mode(step) == "AUTO")
            return self._take(step)
        if mode == "PRODUCER":
            return await self._producer_gate(step, summary)
        # REVIEW: wait the window, then go on.
        self.deadline = (workflow.now() + self.review_window).isoformat()
        await self._show(step, "WAITING_REVIEW", summary)
        await self._await(lambda: self._decided(step) or self._mode(step) in ("MANUAL", "AUTO"), self.review_window)
        if not self._decided(step) and self._mode(step) == "MANUAL":
            await self._show(step, "WAITING", summary)
            await workflow.wait_condition(lambda: self._decided(step) or self._mode(step) == "AUTO")
        self.deadline = None
        return self._take(step)

    async def _producer_gate(self, step: str, summary: str) -> _Redo | None:
        """PRODUCER mode: it reviews the step. When it may act alone (autopilot Short, or the owner trusts it) it approves, or redoes the
        weak shots with its precise notes (3 rounds, inside the Rs limits); anything else waits for the owner with its advice in front of
        them. The owner gets only PRODUCER_WINDOW to answer; then the Producer decides on its own. Finished Shorts are never redone by it."""
        await self._show(step, "RUNNING", "AI Producer is reviewing…")
        advice = await self._review(step)
        if advice is None:
            self.message = summary
            return None  # nothing it judges here: passes like AUTO
        text = f"{summary} {advice['summary']}"
        acts = advice["decisionId"] is not None and await _p("decides", self.dream_id)
        if not acts:  # it may only advise: give the owner the short window first
            if await self._owner_answers(step, text + " Approve, or Redo with notes. I decide in 30 s if you don't."):
                return self._take(step)
            acts = True  # no answer: it decides alone from here (a failed review has no verdict: it just goes on)
        if advice["decisionId"] is None or advice["verdict"] == "approve":
            if advice["decisionId"] is not None:
                await _p("mark_acted", advice["decisionId"], "approved")
            self.message = text
            return None
        rnd = self.producer_rounds.get(step, 0) + 1
        self.producer_rounds[step] = rnd
        if advice["verdict"] == "redo" and step != "FINISH" and await _p("may_redo", self.dream_id, rnd):
            await _p("mark_acted", advice["decisionId"], "redo")
            shots = await _p("shot_ids", self.dream_id, advice["weakItems"])
            return _Redo(step, shots, advice["redoNotes"])
        # Unsure, or out of redo rounds / budget.
        if await self._owner_answers(step, text + " Approve, or Redo with notes. I go on in 30 s if you don't."):
            await _p("mark_acted", advice["decisionId"], "asked")
            return self._take(step)
        await _p("mark_acted", advice["decisionId"], "approved")
        self.message = text + " (No answer in 30 s: went on with the best version.)"
        return None

    async def _owner_answers(self, step: str, text: str) -> bool:
        """Waits the Producer's short window for the owner. True when the owner answered (or switched the step to Auto)."""
        self.deadline = (workflow.now() + PRODUCER_WINDOW).isoformat()
        await self._show(step, "WAITING", text)
        answered = await self._await(lambda: self._decided(step) or self._mode(step) == "AUTO", PRODUCER_WINDOW)
        self.deadline = None
        return answered

    async def _review(self, step: str) -> dict[str, Any] | None:
        """The Producer's review as advice; None when it judges nothing here. A failed review never fails the step."""
        attempt = self.reviews.get(step, 0) + 1
        self.reviews[step] = attempt
        try:
            request = await _p("review_request", self.dream_id, step)
            if request is None:
                return None
            return await _p("save_review", self.dream_id, step, attempt, await _m("producer_review", request, PRODUCER_MEDIA))
        except ActivityError as failure:
            return {"verdict": "ask", "confidence": 0, "weakItems": [], "decisionId": None, "redoNotes": None,
                    "summary": f"AI Producer could not review this step ({root_message(failure)}); please decide."}

    async def _producer_approves_spend(self, step: str, est: dict[str, Any]) -> bool:
        """In PRODUCER mode a deciding Producer approves the clips' cost by itself within the owner's limits."""
        if self._mode(step) != "PRODUCER" or not await _p("decides", self.dream_id):
            return False
        decision = await _p("spend_decision", self.dream_id, est["inr"])
        if not decision["approve"]:
            self.spend_note = decision["text"]
        return bool(decision["approve"])

    # ------------------------------------------------------------------------------------------- helpers

    def _decided(self, step: str) -> bool:
        return self.cancelled or self.approved == step or (self.redo is not None and self.redo.step == step)

    def _take(self, step: str) -> _Redo | None:
        r = self.redo if self.redo is not None and self.redo.step == step else None
        self.approved = None
        self.redo = None
        return r

    def _mode(self, step: str) -> str:
        return self.modes.get(step, self.default_mode)

    async def _await(self, cond: Any, timeout: timedelta) -> bool:
        """wait_condition with a timeout: True when the condition came true, False when the time ran out."""
        try:
            await workflow.wait_condition(cond, timeout=timeout)
            return True
        except TimeoutError:
            return False

    async def _show(self, step: str, status: str, message: str | None) -> None:
        self.status = status
        if message is not None:
            self.message = message
        await _d("status", self.dream_id, step, status, message)


WORKFLOWS = [DreamShortWorkflow]
