"""The long-running workflow, one per video (HANDOVER §9). Port of VideoProductionWorkflowImpl and the five small redo workflows.

Deterministic workflow code (Temporal rule, HANDOVER §9.5): no I/O, no clocks, no randomness here. Everything with side effects goes through
activities (`control.*`, `producer.*` on the Python control queue; the media worker's activities by name on the media queue). Payloads are
plain camelCase dicts (see codec): requests built by one activity go straight into the next, results straight into the save activity.

These are NEW workflow types (names in queues.py): the Java workflows keep running their videos; a video is started on one or the other.
There is no `getVersion` history to replay, so every feature the Java version gated behind a version marker is simply on.

Stage loop for every stage the plan includes: run the stage; the Inspector (plus the Creative Director's story review of the shot plan and the
images) checks it; a FAIL is redone once automatically (images and clips: only the failing shots, with better prompts), a second FAIL waits
for the owner whatever the mode; then AUTO / PRODUCER / REVIEW / MANUAL decide whether the owner is asked. A failed stage never kills the
video: it waits for the owner (approve = continue without it, reject = run it again).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from studio.workflow import queues
    from studio.workflow.stage import Stage

DEFAULT_REVIEW_MINUTES = 10
BUDGET_ERROR = "BudgetExceededError"  # studio.ledger.budget_guard.BUDGET_ERROR: never retried, the stage waits for the owner
STAGES = [s.name for s in Stage]
# Same sets as studio.production.inspector (a test keeps them equal); strings so the sandbox imports nothing heavy.
INSPECTED = {"RESEARCH", "SCRIPT", "MUSIC", "NARRATION", "SHOT_PLAN", "KEYFRAMES", "ANIMATIC", "DRAFT_GEN", "FINAL_GEN", "ASSEMBLY"}
PER_SHOT = {"KEYFRAMES", "DRAFT_GEN", "FINAL_GEN"}


def _retry(initial: int | None = None, maximum_interval: int | None = None, attempts: int = 0, no_retry: list[str] | None = None) -> RetryPolicy:
    return RetryPolicy(initial_interval=timedelta(seconds=initial or 1), maximum_interval=None if maximum_interval is None else timedelta(seconds=maximum_interval),
                       maximum_attempts=attempts, non_retryable_error_types=no_retry or [])


# Control activities (database work and cheap calls).
CONTROL = dict(start_to_close_timeout=timedelta(minutes=5), retry_policy=_retry(2, 60, 10))
# Activities that call paid LLMs: few retries (each retry costs money), never retry a budget refusal.
AGENTS = dict(start_to_close_timeout=timedelta(minutes=6), retry_policy=_retry(10, None, 3, [BUDGET_ERROR]))
# Claude research: one attempt only (a retry would pay for the web searches again); Gemini is the backup.
CLAUDE_ONCE = dict(start_to_close_timeout=timedelta(minutes=6), retry_policy=_retry(None, None, 1))
# Python media worker. Long schedule-to-close so a stage simply waits if the worker is offline; heartbeats detect a worker that died mid-job.
MEDIA = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(minutes=60), heartbeat_timeout=timedelta(minutes=10),
             schedule_to_close_timeout=timedelta(hours=24), retry_policy=_retry(15, None, 3))
PRODUCER_ACTS = dict(start_to_close_timeout=timedelta(minutes=2), retry_policy=_retry(None, None, 5))
PRODUCER_MEDIA = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(minutes=20), heartbeat_timeout=timedelta(minutes=5),
                      retry_policy=_retry(20, None, 2))  # a paid review: one retry at most
INSPECTOR_MEDIA = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(minutes=30), heartbeat_timeout=timedelta(minutes=5),
                       schedule_to_close_timeout=timedelta(hours=2), retry_policy=_retry(15, None, 2))
# Quality gate after the edit: free ffmpeg checks; a fail is not retried (it would only fail again), the owner decides.
GATE_MEDIA = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(minutes=20), schedule_to_close_timeout=timedelta(hours=24),
                  retry_policy=_retry(15, None, 2, ["QualityGateFailed"]))
# Moon layer composite: free local ffmpeg; a missing picture is not retried (it would only fail again), the owner decides.
MOON_MEDIA = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(minutes=30), schedule_to_close_timeout=timedelta(hours=24),
                  retry_policy=_retry(15, None, 2, ["MoonLayerInputMissing"]))
# Local RIFE interpolation: free, on the laptop GPU; a failure is not retried by Temporal (it would only take as long again), the owner decides.
INTERPOLATE_MEDIA = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(hours=3), schedule_to_close_timeout=timedelta(hours=24),
                         retry_policy=_retry(15, None, 1))
# Wan clips: a batch can take an hour. Never retried by Temporal: a retry would pay for every clip again, so a failure waits for the owner.
CLIP_MEDIA = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(hours=4), heartbeat_timeout=timedelta(minutes=3),
                  schedule_to_close_timeout=timedelta(hours=24), retry_policy=_retry(None, None, 1))


def root_message(e: BaseException) -> str:
    c = e
    while c.__cause__ is not None:
        c = c.__cause__
    m = str(c) or type(c).__name__
    return m[:400] + "…" if len(m) > 400 else m  # Java's rootMessage: readable and short


@dataclass
class _Decision:
    stage: str
    approve: bool
    notes: str | None
    resume: bool = False  # "Resume": re-run only the save from the stored media result (a failed-stage hold only)


@dataclass
class _Fix:
    stage: str
    shot_ids: list[str]
    owner_notes: str | None


async def _ctl(name: str, *args: Any, opts: dict[str, Any] = CONTROL) -> Any:
    return await workflow.execute_activity(f"control.{name}", args=list(args), **opts)


async def _agents(name: str, *args: Any) -> Any:
    return await _ctl(name, *args, opts=AGENTS)


async def _media(name: str, req: Any, opts: dict[str, Any] = MEDIA) -> Any:
    return await workflow.execute_activity(name, req, **opts)


async def _producer(name: str, *args: Any, opts: dict[str, Any] = PRODUCER_ACTS) -> Any:
    return await workflow.execute_activity(f"producer.{name}", args=list(args), **opts)


def _result(summary: str, artifact_json: str | None = None, planned: list[str] | None = None) -> dict[str, Any]:
    return {"summary": summary, "artifactJson": artifact_json, "plannedStages": planned}


@workflow.defn(name=queues.VIDEO_WORKFLOW)
class VideoProductionWorkflow:
    def __init__(self) -> None:
        self.modes: dict[str, str] = {}
        self.views: dict[str, dict[str, Any]] = {}
        self.default_mode = "AUTO"
        self.phase = "CREATED"
        self.current_stage: str | None = None
        self.waiting_stage: str | None = None
        self.decision: _Decision | None = None
        self.paused = False
        self.review_minutes = DEFAULT_REVIEW_MINUTES
        self.inspector_redone: set[str] = set()  # stages the Inspector already sent back once in this run
        self.pending_fix: _Fix | None = None  # a pending targeted redo (images or clips): only these shots, with the owner's notes if any
        self.producer_rounds: dict[str, int] = {}  # AI Producer (P2): redo rounds it started per stage
        self.producer_voice_notes: str | None = None
        self.skip_stage = False  # set when the owner declines a GPU batch: the stage is recorded as skipped instead of passed
        # One-pod mode (VIDEO_ONE_POD, AUTO only): the pod rented for KEYFRAMES is kept for DRAFT_GEN. The workflow only decides WHEN (no stage
        # between them waits for a human); every pod action is an activity (control.keyframe_request/save_keyframes/touch_held_pod/release_held_pod).
        self.one_pod = False
        self.chain: list[str] = []  # the stages this run will execute, in order
        # Paid redos wait for the owner and a failed save can be resumed (patch "2026-11-25-redo-resume-holds"; off for runs started before it).
        self.holds_v1 = False
        self.stored: dict[str, tuple[str, list[Any]]] = {}  # stage -> the save activity (name, args) whose media result is already made and kept
        self.resume_next = False  # the owner chose Resume: the next attempt re-runs only the stored save
        self.waiting_can_resume = False
        self.stages = STAGES  # without MOON_LAYER for runs started before it existed (replay-safe, see _run)

    # ------------------------------------------------------------------------------------------- the run

    @workflow.run
    async def run(self, request: dict[str, Any]) -> None:
        try:
            await self._run(request)
        except (asyncio.CancelledError, Exception):
            # Cancelled or failed with a pod possibly held: let it go before the workflow ends (rule 8). A terminated workflow cannot do this;
            # the hold timer (VideoGpu.release_stale_holds) and the pod's own lease cover that case.
            await self._release_pod(request["videoId"], "video workflow ended or was cancelled")
            raise

    async def _run(self, request: dict[str, Any]) -> None:
        self.default_mode = request.get("defaultMode") or "AUTO"
        if request.get("reviewMinutes") and request["reviewMinutes"] > 0:
            self.review_minutes = request["reviewMinutes"]
        self.modes.update(request.get("modes") or {})
        video_id, prompt = request["videoId"], request["prompt"]
        self.phase = "RUNNING"
        await _ctl("set_video_status", video_id, "RUNNING")
        if workflow.patched("one-pod-v1"):  # histories started before this feature never ask the question (replay-safe)
            self.one_pod = bool(await _ctl("one_pod_enabled"))

        self.holds_v1 = workflow.patched("2026-11-25-redo-resume-holds")
        if not workflow.patched("moon-layer-v1"):
            self.stages = [s for s in STAGES if s != "MOON_LAYER"]
        if not workflow.patched("interpolate-v1"):  # histories from before the INTERPOLATE stage existed never run it (replay-safe)
            self.stages = [s for s in self.stages if s != "INTERPOLATE"]
        only = request.get("onlyStages")
        if only is not None:
            # Re-run: only the requested stages, in production order; everything else keeps its earlier output.
            self.chain = [s for s in self.stages if s in only]
            for stage in self.stages:
                if stage in only:
                    await self._execute(video_id, stage, prompt, await _ctl("last_attempt", video_id, stage))
            await self._complete(video_id)
            return

        plan_result = await self._execute(video_id, "PLAN", prompt)
        planned = plan_result.get("plannedStages")
        planned = self.stages if planned is None else planned
        self.chain = [s for s in self.stages if s != "PLAN" and s in planned]
        for stage in self.stages:
            if stage == "PLAN":
                continue
            if stage not in planned:
                self.views[stage] = self._view(stage, self._mode(stage), "SKIPPED", 0, "Not needed for this video")
                await _ctl("record_stage", video_id, stage, 0, self._mode(stage), "SKIPPED", "Not needed for this video (decided by the plan)", None, None)
                continue
            await self._execute(video_id, stage, prompt)
        await self._complete(video_id)

    async def _complete(self, video_id: str) -> None:
        await self._release_pod(video_id, "video finished")
        self.phase = "COMPLETED"
        self.current_stage = None
        await _ctl("set_video_status", video_id, "COMPLETED")

    # ------------------------------------------------------------------------------------------- one stage

    async def _execute(self, video_id: str, stage: str, prompt: str, previous_attempts: int = 0) -> dict[str, Any]:
        notes: str | None = None
        attempt = previous_attempts
        while True:
            attempt += 1
            if self.paused:
                await self._release_pod(video_id, "video paused: never keep a pod across a human wait")
                await _ctl("set_video_status", video_id, "PAUSED")
                await workflow.wait_condition(lambda: not self.paused)
                await _ctl("set_video_status", video_id, "RUNNING")
            resuming = self.holds_v1 and self.resume_next and stage in self.stored
            self.resume_next = False
            if self.holds_v1 and not resuming:
                self.stored.pop(stage, None)  # a full redo starts from nothing
            self.current_stage = stage
            await self._pod_gate(video_id, stage)
            start_mode = self._mode(stage)
            self.views[stage] = self._view(stage, start_mode, "RUNNING", attempt, None)
            await _ctl("record_stage", video_id, stage, attempt, start_mode, "RUNNING", None, None, notes)

            try:
                if resuming:
                    # The media work is done and kept: only the cheap save runs again. No pod is rented, nothing is paid for twice.
                    name, saved_args = self.stored[stage]
                    result = await _ctl(name, *saved_args)
                else:
                    result = await self._run_stage_activity(video_id, stage, prompt, notes, attempt)
                if stage == "DRAFT_GEN":  # the clips stage is over (saved, or nothing to make): the pod is not needed any more
                    await self._release_pod(video_id, "draft clips stage finished")
                if self.skip_stage:
                    self.skip_stage = False
                    await self._finish(video_id, stage, attempt, "SKIPPED", result, notes)
                    return result
            except ActivityError as failure:
                # A failed stage never kills the video: it waits for the owner, whatever the stage's mode.
                # Approve = continue without this stage's output; reject (with notes) = run it again.
                reason = root_message(failure)
                can_resume = self.holds_v1 and stage in self.stored
                text = f"Failed: {reason} Approve to continue without it, or Reject to retry."
                if can_resume:
                    text += " Resume re-runs only the save from the result already made (no new pod, nothing paid twice)."
                d = await self._await_owner(video_id, stage, attempt, "FAILED", reason, text, None, notes, False,
                                            hold=(reason, can_resume, can_resume, None) if self.holds_v1 else None)
                if d.resume:
                    self.resume_next = True
                    continue
                if d.approve:
                    skipped = _result(f"Skipped by owner after failure: {reason}")
                    await self._finish(video_id, stage, attempt, "SKIPPED", skipped, notes)
                    return skipped
                notes = d.notes
                continue

            inspection = await self._inspect(video_id, stage, attempt)
            if inspection.get("summary") is not None:
                result = {**result, "summary": f"{result['summary']} {inspection['summary']}"}
            mode = self._mode(stage)
            if inspection["verdict"] == "FAIL" and mode != "MANUAL":
                paid_check: dict[str, Any] | None = None
                if stage not in self.inspector_redone:
                    if self.holds_v1:
                        # An automatic redo that would rent a pod or call a paid service waits for the owner unless the video allows it.
                        check = await _ctl("redo_check", video_id, stage, inspection.get("redoTargets") if stage in PER_SHOT else None)
                        if check["paid"] and not check["autoPaid"]:
                            paid_check = check
                    if paid_check is None:
                        # First FAIL: redo once by itself. Images and clips: only the shots that failed, with better prompts.
                        self.inspector_redone.add(stage)
                        workflow.logger.info("Inspector failed stage %s, redoing once", stage)
                        await self._finish(video_id, stage, attempt, "INSPECTION_FAILED", result, notes)
                        notes = None if self._targeted_redo(stage, inspection, None) else inspection.get("notes")
                        continue
                    self.inspector_redone.add(stage)  # whatever the owner picks is the one redo this stage gets by itself
                    cost = "cost not estimated" if paid_check["inr"] is None else f"about ₹{paid_check['inr']:.0f}"
                    why = f"{result['summary']} Redoing it would {paid_check['what']} ({cost}), so it waits for you."
                    d = await self._await_owner(video_id, stage, attempt, "WAITING_APPROVAL", result["summary"],
                                                f"{why} Approve to continue with the current result, or Reject (with notes) to redo it.",
                                                result.get("artifactJson"), notes, False, hold=(why, False, False, paid_check["inr"]))
                else:
                    # Failed again after the automatic redo: never pass it unseen, whatever the mode or review timer.
                    d = await self._await_owner(video_id, stage, attempt, "WAITING_APPROVAL", result["summary"],
                                                f"{result['summary']} Approve to accept it anyway, or Reject (with notes) to redo.",
                                                result.get("artifactJson"), notes, False)
                if d.approve:
                    await self._finish(video_id, stage, attempt, "APPROVED", result, notes)
                    return result
                await self._finish(video_id, stage, attempt, "REJECTED", result, notes)
                owner_notes = None if d.notes is None or not d.notes.strip() else d.notes
                if self._targeted_redo(stage, inspection, owner_notes):
                    notes = owner_notes
                else:
                    notes = inspection.get("notes") if owner_notes is None else owner_notes
                continue

            if mode == "PRODUCER":
                # AI Producer, P1 advise-only: it reviews (reads, looks, watches, listens) and recommends; the owner decides with the
                # recommendation in front of them. Stages it doesn't review pass like AUTO. P2: a trusted Producer acts on its own recommendation.
                advice = await self._producer_advice(video_id, stage, attempt)
                if advice is None:
                    await self._finish(video_id, stage, attempt, "AUTO_PASSED", result, notes)
                    return result
                text = f"{result['summary']} {advice['summary']}"
                with_text = {**result, "summary": text}
                if advice.get("decisionId") is not None and await _producer("deciding"):
                    if advice["verdict"] == "approve":
                        await _producer("mark_acted", advice["decisionId"], "approved")
                        await self._finish(video_id, stage, attempt, "PRODUCER_APPROVED", with_text, notes)
                        return result
                    rnd = self.producer_rounds[stage] = self.producer_rounds.get(stage, 0) + 1
                    if advice["verdict"] == "redo" and await _producer("may_redo", video_id, rnd):
                        await _producer("mark_acted", advice["decisionId"], "redo")
                        await self._finish(video_id, stage, attempt, "PRODUCER_REDO", with_text, notes)
                        notes = advice.get("redoNotes")
                        if stage in ("KEYFRAMES", "DRAFT_GEN", "FINAL_GEN") and advice.get("weakItems"):
                            shots = await _producer("shot_ids_for", video_id, advice["weakItems"])
                            if shots:  # only the weak shots, with the Producer's precise notes
                                self.pending_fix = _Fix(stage, shots, advice.get("redoNotes"))
                        if stage == "NARRATION":
                            self.producer_voice_notes = advice.get("redoNotes")
                        continue
                    await _producer("mark_acted", advice["decisionId"], "asked")
                pd = await self._await_owner(
                    video_id, stage, attempt, "WAITING_APPROVAL", text,
                    f"{text} Approve to accept, or Reject (with notes; empty notes = use the Producer's redo notes).",
                    result.get("artifactJson"), notes, True)
                if pd is None:  # switched to AUTO while waiting
                    await self._finish(video_id, stage, attempt, "AUTO_PASSED", result, notes)
                    return result
                if pd.approve:
                    await self._finish(video_id, stage, attempt, "APPROVED", result, notes)
                    return result
                await self._finish(video_id, stage, attempt, "REJECTED", result, notes)
                notes = advice.get("redoNotes") if pd.notes is None or not pd.notes.strip() else pd.notes
                continue

            if mode == "AUTO":
                await self._finish(video_id, stage, attempt, "AUTO_PASSED", result, notes)
                return result

            if mode == "REVIEW":
                # Semi-auto: wait a review window for the owner; continue automatically if nobody responds. Switching the stage to MANUAL during
                # the window holds it until the owner decides.
                await self._release_pod(video_id, "review window: never keep a pod across a human wait")
                window = timedelta(minutes=self.review_minutes)
                deadline = (workflow.now() + window).isoformat()
                self.views[stage] = self._view(stage, "REVIEW", "WAITING_APPROVAL", attempt, result["summary"])
                await _ctl("record_stage", video_id, stage, attempt, "REVIEW", "WAITING_APPROVAL", result["summary"], result.get("artifactJson"), notes)
                await _ctl("set_review_deadline", video_id, stage, attempt, deadline)
                self.phase = "WAITING"
                self.waiting_stage = stage
                self.decision = None
                with contextlib.suppress(TimeoutError):  # the window ended: continue by itself
                    await workflow.wait_condition(lambda: self.decision is not None or self._mode(stage) != "REVIEW", timeout=window)
                if self.decision is None and self._mode(stage) == "MANUAL":
                    await _ctl("set_review_deadline", video_id, stage, attempt, None)  # held: no timer any more
                    await workflow.wait_condition(lambda: self.decision is not None or self._mode(stage) == "AUTO")
                self.waiting_stage = None
                self.phase = "RUNNING"
                rd, self.decision = self.decision, None
                if rd is None:
                    how = "AUTO_APPROVED" if self._mode(stage) == "REVIEW" else "AUTO_PASSED"
                    await self._finish(video_id, stage, attempt, how, result, notes)
                    return result
                if rd.approve:
                    await self._finish(video_id, stage, attempt, "APPROVED", result, notes)
                    return result
                await self._finish(video_id, stage, attempt, "REJECTED", result, notes)
                notes = rd.notes
                continue

            # MANUAL: wait for the owner, or for the stage to be switched to AUTO.
            d = await self._await_owner(video_id, stage, attempt, "WAITING_APPROVAL", result["summary"], result["summary"],
                                        result.get("artifactJson"), notes, True)
            if d is None:
                await self._finish(video_id, stage, attempt, "AUTO_PASSED", result, notes)
                return result
            if d.approve:
                await self._finish(video_id, stage, attempt, "APPROVED", result, notes)
                return result
            workflow.logger.info("Stage %s rejected by owner, re-running", stage)
            await self._finish(video_id, stage, attempt, "REJECTED", result, notes)
            notes = d.notes

    async def _await_owner(self, video_id: str, stage: str, attempt: int, status: str, view_summary: str | None, summary: str | None,
                           artifact_json: str | None, notes: str | None, auto_releases: bool,
                           hold: tuple[str, bool, bool, float | None] | None = None) -> Any:
        """Records the stage as waiting and blocks until the owner approves/rejects (or, when auto_releases, until the stage is switched to AUTO,
        which returns None)."""
        await self._release_pod(video_id, f"{stage} waits for the owner ({status}): never keep a pod across a human wait")
        # hold = (why it waits, Resume offered, a stored result exists, estimated cost of the redo in INR): shown by the dashboard.
        extra = None if hold is None else {"heldReason": hold[0], "canResume": hold[1], "hasStoredResult": hold[2], "redoEstimateInr": hold[3]}
        self.views[stage] = self._view(stage, self._mode(stage), status, attempt, view_summary, extra)
        await _ctl("record_stage", video_id, stage, attempt, self._mode(stage), status, summary, artifact_json, notes)
        if hold is not None:
            await _ctl("record_hold", video_id, stage, attempt, *hold)
        self.phase = "WAITING"
        self.waiting_stage = stage
        self.waiting_can_resume = hold is not None and hold[1]
        self.decision = None
        await workflow.wait_condition(lambda: self.decision is not None or (auto_releases and self._mode(stage) == "AUTO"))
        self.waiting_stage = None
        self.waiting_can_resume = False
        self.phase = "RUNNING"
        d, self.decision = self.decision, None
        return d

    def _targeted_redo(self, stage: str, inspection: dict[str, Any], owner_notes: str | None) -> bool:
        """Images and clips whose every problem names its shot are fixed one by one (better prompts), not by regenerating the whole set. Returns
        true when the next run of the stage will be such a targeted redo."""
        targets = inspection.get("redoTargets")
        if stage not in PER_SHOT or not targets:
            return False
        self.pending_fix = _Fix(stage, list(targets), owner_notes)
        return True

    def _take_fix(self, stage: str) -> _Fix | None:
        fix = self.pending_fix if self.pending_fix is not None and self.pending_fix.stage == stage else None
        self.pending_fix = None
        return fix

    # ------------------------------------------------------------------------------------------- Producer

    async def _producer_advice(self, video_id: str, stage: str, attempt: int) -> dict[str, Any] | None:
        """The Producer's review of this stage as advice; None when it reviews nothing here. Never fails the stage."""
        try:
            request = await _producer("review_request", video_id, stage)
            if request is None:
                return None
            review = await _media("producer_review", request, PRODUCER_MEDIA)
            return await _producer("save_review", video_id, stage, attempt, review)
        except ActivityError as failure:
            workflow.logger.warning("AI Producer review of %s failed: %s", stage, root_message(failure))
            return {"verdict": "ask", "confidence": 0, "weakItems": [], "decisionId": None, "redoNotes": None,
                    "summary": f"AI Producer could not review this stage ({root_message(failure)}); please decide."}

    async def _producer_approves_spend(self, video_id: str, stage: str, inr: float) -> bool:
        """P2: in PRODUCER mode, a trusted Producer approves a money step within the owner's limits by itself."""
        if self._mode(stage) != "PRODUCER" or not await _producer("deciding"):
            return False
        return (await _producer("spend_decision", video_id, stage, inr))["approve"]

    async def _with_spend_advice(self, video_id: str, stage: str, summary: str, inr: float) -> str:
        """P1: the Producer's opinion on a money step, shown with the estimate (advice only, no AI call)."""
        if self._mode(stage) != "PRODUCER":
            return summary
        return f"{summary} {await _producer('spend_advice', video_id, inr)}"

    # ------------------------------------------------------------------------------------------- the work of each stage

    async def _run_stage_activity(self, video_id: str, stage: str, prompt: str, notes: str | None, attempt: int) -> dict[str, Any]:
        if stage == "PLAN":
            return await _ctl("plan", video_id, prompt, notes, attempt)
        if stage == "RESEARCH":
            research = await _agents("research_request", video_id, notes)
            # Claude first (blind A/B on the deep-ocean demo, 17 Nov 2026: accuracy 10 vs 8, facts 9 vs 7), then Gemini with Google Search.
            try:
                found = await _ctl("claude_research", video_id, research, opts=CLAUDE_ONCE)
            except ActivityError as failure:
                workflow.logger.warning("Claude research failed (%s); using Gemini with Google Search", root_message(failure))
                found = await _media("research_topic", research)
            return await self._save(stage, "save_research", video_id, await self._check_sources(found))
        if stage == "CONCEPT":
            return await _agents("concept", video_id, notes)
        if stage == "SCRIPT":
            return await _agents("script", video_id, notes)
        if stage == "MUSIC":
            return await self._save(stage, "save_music", video_id, await _media("generate_music", await _agents("music_request", video_id, notes)))
        if stage == "NARRATION":
            # Voiced before the shot plan so every shot is timed against the real narration length.
            if self.producer_voice_notes is not None:  # AI Producer redo: its precise notes become the reading style
                voice_notes, self.producer_voice_notes = self.producer_voice_notes, None
                request = await _producer("narration_redo_request", video_id, voice_notes)
            else:
                request = await _agents("narration_request", video_id)
            return await self._save(stage, "save_narration", video_id, await _media("synthesize_narration", request))
        if stage == "SHOT_PLAN":
            return await self._run_stock(video_id, await _agents("shot_plan", video_id, notes))
        if stage == "KEYFRAMES":
            fix = self._take_fix("KEYFRAMES")
            if not self.one_pod:
                request = await _ctl("keyframe_request", video_id, notes) if fix is None else await _agents("fix_keyframe_request", video_id, fix.shot_ids, fix.owner_notes)
                return await self._save(stage, "save_keyframes", video_id, request, await _media("generate_keyframes", request))
            keep = self._keep_pod_after_keyframes()
            request = await _ctl("keyframe_request", video_id, notes, keep) if fix is None else await _agents("fix_keyframe_request", video_id, fix.shot_ids, fix.owner_notes)
            made = await _media("generate_keyframes", request)
            # Resume never keeps the pod for the clips: it saves and lets go, exactly like a save that failed.
            return await self._save(stage, "save_keyframes", video_id, request, made, keep and fix is None, stored_args=[video_id, request, made])
        if stage == "ANIMATIC":
            return await self._save(stage, "save_animatic", video_id, await _media("render_animatic", await _ctl("animatic_request", video_id)))
        if stage == "LIBRARY_MATCH":
            # Free: suggests real NASA footage for the shots the Director marked; the owner picks.
            search = await _ctl("footage_search_request", video_id, None, None)
            if not search["queries"]:
                return _result("No shots asked for real footage.")
            return await _ctl("save_footage_candidates", video_id, await _media("search_footage", search))
        if stage in ("DRAFT_GEN", "FINAL_GEN"):
            return await self._run_clips(video_id, stage, attempt, notes)
        if stage == "UPSCALE":
            return await self._run_smooth(video_id, stage, attempt, notes)
        if stage == "ASSEMBLY":
            request = await _ctl("assembly_request", video_id)
            made = await _media("assemble_video", request)
            saved = await _ctl("save_master", video_id, made)
            if not request.get("qualityGate"):
                return saved
            # Free checks on the master (tiers 1-2, judge off). A fail raises: the stage then waits for the owner like any failed stage
            # (Reject = redo the edit, Approve = continue without it). Warnings only join the summary.
            gate = await _media("quality_gate_check", {"videoId": video_id, "masterUri": made["masterUri"], "srtUri": made.get("srtUri")}, GATE_MEDIA)
            return {**saved, "summary": f"{saved['summary']} {gate['summary']}"}
        if stage == "MOON_LAYER":
            # Optional, free: a plan with a moon block gets its Moon picture composited over the master. A failure raises (waits for the owner).
            request = await _ctl("moon_request", video_id)
            if request.get("skip"):
                self.skip_stage = True
                return _result(request["skip"])
            return await self._save(stage, "save_moon_layer", video_id, request, await _media("moon_layer_composite", request, MOON_MEDIA))
        if stage == "INTERPOLATE":
            # Optional, free, local: RIFE doubles the master's frame rate (INTERPOLATE_FPS=local). Off, or no RIFE program on the worker: skipped.
            # A real failure raises (waits for the owner: Retry / Continue without it).
            request = await _ctl("interpolate_request", video_id)
            if request.get("skip"):
                self.skip_stage = True
                return _result(request["skip"])
            made = await _media("interpolate_master", request, INTERPOLATE_MEDIA)
            if made.get("skip"):
                self.skip_stage = True
                return _result(made["skip"])
            return await self._save(stage, "save_interpolated", video_id, request, made)
        return await _ctl("run_stage", video_id, stage, notes, attempt)

    async def _save(self, stage: str, name: str, *args: Any, stored_args: list[Any] | None = None) -> dict[str, Any]:
        """The save step that follows a finished media job. The media result is kept in the workflow first, so a crash in the save leaves the
        owner a Resume that re-runs only this call (never the pod). Same single activity call as before the patch."""
        if self.holds_v1:
            self.stored[stage] = (name, list(args) if stored_args is None else stored_args)
        return await _ctl(name, *args)

    async def _run_clips(self, video_id: str, stage: str, attempt: int, notes: str | None) -> dict[str, Any]:
        """The batch is estimated first and, unless it is under the auto-approve amount, the owner must approve the GPU spend before anything runs
        (CLAUDE.md rule 2); declining skips the stage."""
        kind = "draft" if stage == "DRAFT_GEN" else "final"
        fix = self._take_fix(stage)
        only = None if fix is None else fix.shot_ids
        estimate = await _ctl("clip_estimate", video_id, kind, only)
        if estimate["clips"] == 0:
            self.skip_stage = True
            return _result(estimate["summary"])
        if estimate["needsApproval"] and not await self._producer_approves_spend(video_id, stage, estimate["inr"]):
            ask = await self._with_spend_advice(video_id, stage, estimate["summary"], estimate["inr"])
            d = await self._await_owner(video_id, stage, attempt, "WAITING_APPROVAL", ask,
                                        f"{ask} Approve to start, or Reject to skip this stage (nothing is spent).", None, notes, False)
            if not d.approve:
                self.skip_stage = True
                return _result(f"Skipped: the owner didn't approve the GPU spend ({estimate['summary']})")
            await _ctl("record_stage", video_id, stage, attempt, self._mode(stage), "RUNNING", None, None, notes)
            self.views[stage] = self._view(stage, self._mode(stage), "RUNNING", attempt, None)
        request = await _agents("clip_request", video_id, kind, only)
        return await self._save(stage, "save_clips", video_id, request, await _media("generate_clips", request, CLIP_MEDIA))

    async def _run_smooth(self, video_id: str, stage: str, attempt: int, notes: str | None) -> dict[str, Any]:
        """Upscale stage (GPU finishing): smooth motion (RIFE, L4), then sharpness (SeedVR2, H100). Both are estimated first and shown as one
        approval; the owner approves the spend unless it is under the auto-approve amount; declining skips the stage (the edit then uses the
        clips as they are). Sharpening runs after smoothing so it works on the clips the edit will actually use."""
        smooth = await _ctl("smooth_estimate", video_id)
        sharp = await _ctl("sharp_estimate", video_id)
        clips = smooth["clips"] + sharp["clips"]
        if sharp["clips"] == 0:
            summary = smooth["summary"]
        elif smooth["clips"] == 0:
            summary = sharp["summary"]
        else:
            summary = f"{smooth['summary']} Then: {sharp['summary']}"
        if clips == 0:
            self.skip_stage = True
            return _result(summary)
        total_inr = smooth["inr"] + sharp["inr"]
        if (smooth["needsApproval"] or sharp["needsApproval"]) and not await self._producer_approves_spend(video_id, stage, total_inr):
            ask = await self._with_spend_advice(video_id, stage, summary, total_inr)
            d = await self._await_owner(video_id, stage, attempt, "WAITING_APPROVAL", ask,
                                        f"{ask} Approve to start, or Reject to skip (nothing is spent).", None, notes, False)
            if not d.approve:
                self.skip_stage = True
                return _result(f"Skipped: the owner didn't approve the GPU spend ({summary})")
            await _ctl("record_stage", video_id, stage, attempt, self._mode(stage), "RUNNING", None, None, notes)
            self.views[stage] = self._view(stage, self._mode(stage), "RUNNING", attempt, None)
        smoothed = None
        if smooth["clips"] > 0:
            request = await _agents("smooth_request", video_id)
            smoothed = await _ctl("save_smooth", video_id, request, await _media("smooth_clips", request, CLIP_MEDIA))
        if sharp["clips"] == 0:
            return smoothed
        request = await _agents("sharp_request", video_id)
        sharpened = (_result("Every clip is already sharp.") if not request["jobs"]
                     else await _ctl("save_sharp", video_id, request, await _media("upscale_clips", request, CLIP_MEDIA)))
        if smoothed is None:
            return sharpened
        return _result(f"{smoothed['summary']} {sharpened['summary']}", sharpened.get("artifactJson"))

    async def _check_sources(self, research: dict[str, Any]) -> dict[str, Any]:
        """Source check (18 Nov 2026): free and optional; a failure keeps the facts as they are."""
        try:
            return await _media("check_sources", research, INSPECTOR_MEDIA)
        except ActivityError as failure:
            workflow.logger.warning("Source check skipped: %s", root_message(failure))
            return research

    async def _run_stock(self, video_id: str, planned: dict[str, Any]) -> dict[str, Any]:
        """Stock lane: search/fetch every stock shot (a switch that is off yields no requests). A miss never fails the shot."""
        requests = await _ctl("stock_requests", video_id)
        if not requests:
            return planned
        found = 0
        for request in requests:
            try:
                result = await _media("stock_fetch", request)
            except ActivityError as failure:
                result = {"found": False, "reason": root_message(failure)}
            found += 1 if result.get("found") else 0
            await _ctl("save_stock", video_id, request, result)
        return {**planned, "summary": f"{planned['summary']} Stock lane: {found} of {len(requests)} stock shots got a real clip, the rest use the AI lane."}

    # ------------------------------------------------------------------------------------------- Inspector

    async def _inspect(self, video_id: str, stage: str, attempt: int) -> dict[str, Any]:
        """Second-level inspection of a finished stage: the Inspector's checks plus, for the shot plan and the images, the Creative Director's story
        review. Never fails the stage itself: a check that can't run is reported as a warning and production continues."""
        if stage not in INSPECTED:
            return {"verdict": "PASS", "summary": None, "notes": None, "redoTargets": []}
        try:
            request = await _ctl("inspection_request", video_id, stage)
            files = None if request is None else await _media("inspect_media", request, INSPECTOR_MEDIA)
            if stage in ("SHOT_PLAN", "KEYFRAMES"):
                files = self._merge(files, await self._creative_review(video_id, stage, attempt))
            return await _ctl("save_inspection", video_id, stage, attempt, files)
        except ActivityError as failure:
            workflow.logger.warning("Inspector could not check %s: %s", stage, root_message(failure))
            return {"verdict": "WARN", "summary": f"Inspector could not run: {root_message(failure)}", "notes": None, "redoTargets": []}

    async def _creative_review(self, video_id: str, stage: str, attempt: int) -> list[dict[str, Any]]:
        try:
            if stage == "SHOT_PLAN":
                return await _agents("creative_shot_plan_review", video_id, attempt)
            request = await _agents("image_review_request", video_id)
            return await _ctl("save_image_review", video_id, attempt, await _media("review_images", request, INSPECTOR_MEDIA))
        except ActivityError as failure:
            workflow.logger.warning("Creative Director could not review %s: %s", stage, root_message(failure))
            return [{"severity": "WARN", "code": "CREATIVE_REVIEW_UNAVAILABLE",
                     "message": f"The Creative Director could not review this: {root_message(failure)}", "ref": stage.lower(), "targetId": None}]

    @staticmethod
    def _merge(files: dict[str, Any] | None, more: list[dict[str, Any]]) -> dict[str, Any]:
        findings = list((files or {}).get("findings") or [])
        findings.extend(more)
        return {"findings": findings, "checked": list((files or {}).get("checked") or [])}

    # ------------------------------------------------------------------------------------------- bookkeeping

    async def _finish(self, video_id: str, stage: str, attempt: int, status: str, result: dict[str, Any], notes: str | None) -> None:
        self.views[stage] = self._view(stage, self._mode(stage), status, attempt, result["summary"])
        await _ctl("record_stage", video_id, stage, attempt, self._mode(stage), status, result["summary"], result.get("artifactJson"), notes)

    def _mode(self, stage: str) -> str:
        return self.modes.get(stage, self.default_mode)

    # ------------------------------------------------------------------------------------------- one-pod mode

    def _keep_pod_after_keyframes(self) -> bool:
        """True when the keyframes' pod may wait for DRAFT_GEN: one-pod mode, not paused, DRAFT_GEN comes later in this run, and the keyframes
        and every stage up to and including DRAFT_GEN are AUTO (a review window, producer or manual hold in between = a human wait)."""
        if not self.one_pod or self.paused or "KEYFRAMES" not in self.chain or "DRAFT_GEN" not in self.chain:
            return False
        k, d = self.chain.index("KEYFRAMES"), self.chain.index("DRAFT_GEN")
        return k < d and all(self._mode(s) == "AUTO" for s in self.chain[k : d + 1])

    async def _pod_gate(self, video_id: str, stage: str) -> None:
        """At every stage start in one-pod mode: a stage between KEYFRAMES and DRAFT_GEN restarts the held pod's idle clock; a stage after
        DRAFT_GEN means no pod should be held any more."""
        if not self.one_pod:
            return
        order = STAGES.index(stage)
        if order > STAGES.index("DRAFT_GEN"):
            await self._release_pod(video_id, f"{stage} starts: the pod is not needed any more")
        elif order > STAGES.index("KEYFRAMES"):
            with contextlib.suppress(Exception):  # a failed touch only means the hold timer runs from the keyframes' save
                await _ctl("touch_held_pod", video_id)

    async def _release_pod(self, video_id: str, why: str) -> None:
        """Idempotent. Never fails the video: if the call fails, the hold timer, the lease heartbeat and the watchdog still end the pod."""
        if not self.one_pod:
            return
        try:
            await _ctl("release_held_pod", video_id, why)
        except Exception as e:  # noqa: BLE001
            workflow.logger.warning("Could not release the held pod (%s); the hold timer and the watchdog will", e)

    @staticmethod
    def _view(stage: str, mode: str, status: str, attempt: int, summary: str | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"stage": stage, "mode": mode, "status": status, "attempt": attempt, "summary": summary, **(extra or {})}

    # ------------------------------------------------------------------------------------------- signals and query

    @workflow.signal
    def approve(self, stage: str, notes: str | None = None) -> None:
        """Owner approves the stage that is waiting (manual mode). Ignored if that stage is not waiting."""
        if stage == self.waiting_stage:
            self.decision = _Decision(stage, True, notes)

    @workflow.signal
    def reject(self, stage: str, notes: str | None = None) -> None:
        """Owner rejects the waiting stage; it runs again with the notes as guidance."""
        if stage == self.waiting_stage:
            self.decision = _Decision(stage, False, notes)

    @workflow.signal
    def resume_stage(self, stage: str) -> None:
        """Owner chose Resume on a failed stage whose media result is stored: only the save runs again. Ignored if Resume is not offered."""
        if stage == self.waiting_stage and self.waiting_can_resume:
            self.decision = _Decision(stage, False, None, True)

    @workflow.signal
    def set_mode(self, stage: str, mode: str) -> None:
        """Switch one stage between modes. Takes effect immediately, even for a stage that is waiting."""
        self.modes[stage] = mode

    @workflow.signal
    def set_all_modes(self, mode: str) -> None:
        self.default_mode = mode
        self.modes.clear()

    @workflow.signal
    def set_review_minutes(self, minutes: int) -> None:
        """Review window for stages in REVIEW mode (applies from the next stage that starts waiting)."""
        if minutes > 0:
            self.review_minutes = minutes

    @workflow.signal
    def pause(self) -> None:
        self.paused = True

    @workflow.signal
    def resume(self) -> None:
        self.paused = False

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {"phase": self.phase, "currentStage": self.current_stage, "paused": self.paused, "defaultMode": self.default_mode,
                "modes": {s: self._mode(s) for s in STAGES}, "stages": list(self.views.values())}


# ----------------------------------------------------------------------------------------------- the five small workflows


async def _failed(video_id: str, mark: str, ids: list[Any], reason_prefix: str, failure: BaseException, limit: int = 400) -> dict[str, Any]:
    reason = root_message(failure)
    await _ctl(mark, video_id, ids, "FAILED", reason[:limit])
    return _result(f"{reason_prefix} failed: {reason}")


SHOT_REDO = dict(start_to_close_timeout=timedelta(minutes=5), retry_policy=_retry(None, None, 10))
# Budget-checked request: never retried after a budget refusal (it may wait for a pod start of 8-18 min: retries join it, see VideoGpu).
GUARDED_LONG = dict(start_to_close_timeout=timedelta(minutes=5), retry_policy=_retry(None, None, 8, [BUDGET_ERROR]))


@workflow.defn(name=queues.SHOT_REDO_WORKFLOW)
class ShotRedoWorkflow:
    @workflow.run
    async def run(self, r: dict[str, Any]) -> dict[str, Any]:
        v, shots = r["videoId"], r["shotIds"]
        await _ctl("mark_shots", v, shots, "REDOING", None, opts=SHOT_REDO)
        try:
            request = await _ctl("shot_keyframe_request", v, shots, r.get("ownerNotes"), r.get("policy"), opts=GUARDED_LONG)
            media = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(minutes=30), heartbeat_timeout=timedelta(minutes=10),
                         schedule_to_close_timeout=timedelta(hours=6), retry_policy=_retry(15, None, 3))
            result = await _media("generate_keyframes", request, media)
            return await _ctl("save_keyframes", v, request, result, opts=SHOT_REDO)
        except ActivityError as failure:
            return await _failed(v, "mark_shots", shots, "Shot redo", failure)


@workflow.defn(name=queues.CLIP_REDO_WORKFLOW)
class ClipRedoWorkflow:
    @workflow.run
    async def run(self, r: dict[str, Any]) -> dict[str, Any]:
        v, shots = r["videoId"], r["shotIds"]
        try:
            request = await _ctl("clip_request", v, r["kind"], shots, opts=GUARDED_LONG)
            # Never retried: a retry would pay for the clips again.
            return await _ctl("save_clips", v, request, await _media("generate_clips", request, CLIP_MEDIA), opts=GUARDED_LONG)
        except ActivityError as failure:
            return await _failed(v, "mark_clips", shots, "Clip redo", failure)


@workflow.defn(name=queues.VOICE_REDO_WORKFLOW)
class VoiceRedoWorkflow:
    @workflow.run
    async def run(self, r: dict[str, Any]) -> dict[str, Any]:
        v, lines = r["videoId"], r["lineIndexes"]
        await _ctl("mark_lines", v, lines, "REDOING", None, opts=SHOT_REDO)
        try:
            guarded = dict(start_to_close_timeout=timedelta(minutes=2), retry_policy=_retry(None, None, 3, [BUDGET_ERROR]))
            request = await _ctl("line_narration_request", v, lines, r.get("styleNotes"), opts=guarded)
            media = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(minutes=20), heartbeat_timeout=timedelta(minutes=10),
                         schedule_to_close_timeout=timedelta(hours=6), retry_policy=_retry(15, None, 3))
            result = await _media("synthesize_narration", request, media)
            return await _ctl("save_line_narration", v, result, request["style"], opts=SHOT_REDO)
        except ActivityError as failure:
            return await _failed(v, "mark_lines", lines, "Voice redo", failure)


@workflow.defn(name=queues.FOOTAGE_WORKFLOW)
class FootageWorkflow:
    """Free: NASA media costs nothing."""

    @workflow.run
    async def run(self, r: dict[str, Any]) -> dict[str, Any]:
        v, shot = r["videoId"], r["shotId"]
        short = dict(start_to_close_timeout=timedelta(minutes=2), retry_policy=_retry(None, None, 3))
        media = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(minutes=20), schedule_to_close_timeout=timedelta(hours=2),
                     retry_policy=_retry(10, None, 3))
        try:
            if r.get("nasaId") is None:
                search = await _ctl("footage_search_request", v, [shot], r.get("query"), opts=short)
                return await _ctl("save_footage_candidates", v, await _media("search_footage", search, media), opts=short)
            await _ctl("mark_clips", v, [shot], "RENDERING", None, opts=short)
            fetch = await _ctl("footage_fetch_request", v, shot, r["nasaId"], r.get("startSec"), opts=short)
            return await _ctl("save_footage", v, fetch, await _media("fetch_footage", fetch, media), opts=short)
        except ActivityError as failure:
            reason = root_message(failure)
            if r.get("nasaId") is not None:
                await _ctl("mark_clips", v, [shot], "FAILED", "NASA footage: " + reason[:380], opts=short)
            return _result(f"Footage failed: {reason}")


@workflow.defn(name=queues.CREATIVE_REVIEW_WORKFLOW)
class CreativeReviewWorkflow:
    @workflow.run
    async def run(self, r: dict[str, Any]) -> dict[str, Any]:
        v = r["videoId"]
        guarded = dict(start_to_close_timeout=timedelta(minutes=2), retry_policy=_retry(None, None, 3, [BUDGET_ERROR]))
        media = dict(task_queue=queues.MEDIA, start_to_close_timeout=timedelta(minutes=30), heartbeat_timeout=timedelta(minutes=5),
                     schedule_to_close_timeout=timedelta(hours=2), retry_policy=_retry(15, None, 2))
        attempt = max(1, await _ctl("last_attempt", v, "KEYFRAMES", opts=guarded))
        request = await _ctl("image_review_request", v, opts=guarded)
        findings = await _ctl("save_image_review", v, attempt, await _media("review_images", request, media), opts=guarded)
        fails = sum(1 for f in findings if f["severity"] == "FAIL")
        warns = sum(1 for f in findings if f["severity"] == "WARN")
        return _result(f"Creative Director reviewed the images: {fails} problem(s), {warns} warning(s).")


WORKFLOWS = [VideoProductionWorkflow, ShotRedoWorkflow, ClipRedoWorkflow, VoiceRedoWorkflow, FootageWorkflow, CreativeReviewWorkflow]
