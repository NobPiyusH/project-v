package com.projectv.workflow;

import com.projectv.media.MediaTypes.ClipEstimate;
import com.projectv.media.MediaTypes.ClipRequest;
import com.projectv.media.MediaTypes.Finding;
import com.projectv.media.MediaTypes.ImageReviewRequest;
import com.projectv.media.MediaTypes.MediaInspectRequest;
import com.projectv.media.MediaTypes.MediaInspectResult;
import com.projectv.production.Inspector;
import com.projectv.workflow.WorkflowTypes.InspectionReport;
import com.projectv.workflow.WorkflowTypes.StageResult;
import com.projectv.workflow.WorkflowTypes.StageView;
import com.projectv.workflow.WorkflowTypes.VideoRequest;
import com.projectv.workflow.WorkflowTypes.WorkflowStatus;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.failure.ActivityFailure;
import io.temporal.workflow.Workflow;
import org.slf4j.Logger;

import java.time.Duration;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.EnumSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Deterministic workflow code (Temporal rule, HANDOVER §9.5): no I/O, no clocks, no randomness here.
 * Everything with side effects goes through activities.
 *
 * <p>Stage loop for every stage the plan includes:
 * <ol>
 *   <li>run the stage (activity)</li>
 *   <li>Inspector: independent second-level checks, plus the Creative Director's story review of the shot plan and
 *       the images. A FAIL is redone once automatically (images: only the failing shots, with better prompts); a
 *       second FAIL waits for the owner (approve = accept anyway, reject = redo with notes), whatever the mode.</li>
 *   <li>AUTO: record AUTO_PASSED and continue</li>
 *   <li>MANUAL: record WAITING_APPROVAL and wait for approve / reject / a switch to AUTO.
 *       Reject re-runs the stage with the owner's notes.</li>
 * </ol>
 */
public class VideoProductionWorkflowImpl implements VideoProductionWorkflow {

    private static final Logger log = Workflow.getLogger(VideoProductionWorkflowImpl.class);

    private final VideoActivities activities = Workflow.newActivityStub(VideoActivities.class,
            ActivityOptions.newBuilder()
                    .setStartToCloseTimeout(Duration.ofMinutes(5))
                    .setRetryOptions(RetryOptions.newBuilder()
                            .setInitialInterval(Duration.ofSeconds(2))
                            .setMaximumInterval(Duration.ofMinutes(1))
                            .setMaximumAttempts(10)
                            .build())
                    .build());

    private static final String BUDGET_ERROR = "com.projectv.ledger.BudgetGuard$BudgetExceededException";

    // Activities that call paid LLMs: few retries (each retry costs money), never retry a budget refusal.
    private final VideoActivities agents = Workflow.newActivityStub(VideoActivities.class,
            ActivityOptions.newBuilder()
                    .setStartToCloseTimeout(Duration.ofMinutes(6))
                    .setRetryOptions(RetryOptions.newBuilder()
                            .setInitialInterval(Duration.ofSeconds(10))
                            .setMaximumAttempts(3)
                            .setDoNotRetry(BUDGET_ERROR)
                            .build())
                    .build());

    // Python media worker. Long schedule-to-close so a stage simply waits if the worker is offline;
    // heartbeats detect a worker that died mid-job. Paid calls inside, so few retries.
    private final MediaActivities media = Workflow.newActivityStub(MediaActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.MEDIA)
                    .setStartToCloseTimeout(Duration.ofMinutes(60))
                    .setHeartbeatTimeout(Duration.ofMinutes(10))
                    .setScheduleToCloseTimeout(Duration.ofHours(24))
                    .setRetryOptions(RetryOptions.newBuilder()
                            .setInitialInterval(Duration.ofSeconds(15))
                            .setMaximumAttempts(3)
                            .build())
                    .build());

    // Inspector file checks on the media worker: free (FFmpeg + Groq Whisper), so a couple of retries are fine.
    private final MediaActivities inspectorMedia = Workflow.newActivityStub(MediaActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.MEDIA)
                    .setStartToCloseTimeout(Duration.ofMinutes(30))
                    .setHeartbeatTimeout(Duration.ofMinutes(5))
                    .setScheduleToCloseTimeout(Duration.ofHours(2))
                    .setRetryOptions(RetryOptions.newBuilder()
                            .setInitialInterval(Duration.ofSeconds(15))
                            .setMaximumAttempts(2)
                            .build())
                    .build());

    /** Stages the Inspector already sent back once in this run (the automatic redo happens only once). */
    private final java.util.Set<Stage> inspectorRedone = EnumSet.noneOf(Stage.class);

    /** A pending targeted redo (images or clips): only these shots, with the owner's notes if any. */
    private record StageFix(Stage stage, List<String> shotIds, String ownerNotes) {
    }

    private StageFix pendingFix;

    /** Set when the owner declines a GPU batch: the stage is recorded as skipped instead of passed. */
    private boolean skipStage;

    // Wan clips on Modal (M2b): a batch can take an hour; the worker heartbeats every few seconds. Never retried by
    // Temporal: a retry would pay for every clip again, so a failure waits for the owner instead.
    private final MediaActivities clipMedia = Workflow.newActivityStub(MediaActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.MEDIA)
                    .setStartToCloseTimeout(Duration.ofHours(4))
                    .setHeartbeatTimeout(Duration.ofMinutes(3))
                    .setScheduleToCloseTimeout(Duration.ofHours(24))
                    .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(1).build())
                    .build());

    private record Decision(Stage stage, boolean approve, String notes) {
    }

    private final Map<Stage, StageMode> modes = new EnumMap<>(Stage.class);
    private final Map<Stage, StageView> views = new LinkedHashMap<>();
    private StageMode defaultMode = StageMode.AUTO;
    private String phase = "CREATED";
    private Stage currentStage;
    private Stage waitingStage;
    private Decision decision;
    private boolean paused;
    private int reviewMinutes = DEFAULT_REVIEW_MINUTES;

    public static final int DEFAULT_REVIEW_MINUTES = 10;

    @Override
    public void run(VideoRequest request) {
        defaultMode = request.defaultMode() == null ? StageMode.AUTO : request.defaultMode();
        if (request.reviewMinutes() != null && request.reviewMinutes() > 0) {
            reviewMinutes = request.reviewMinutes();
        }
        if (request.modes() != null) {
            modes.putAll(request.modes());
        }
        String videoId = request.videoId();
        phase = "RUNNING";
        activities.setVideoStatus(videoId, "RUNNING");

        if (request.onlyStages() != null) {
            // Re-run: only the requested stages, in production order; everything else keeps its earlier output.
            for (Stage stage : Stage.values()) {
                if (request.onlyStages().contains(stage)) {
                    execute(videoId, stage, request.prompt(), activities.lastAttempt(videoId, stage));
                }
            }
            phase = "COMPLETED";
            currentStage = null;
            activities.setVideoStatus(videoId, "COMPLETED");
            return;
        }

        StageResult planResult = execute(videoId, Stage.PLAN, request.prompt());
        List<Stage> planned = planResult.plannedStages() == null ? List.of(Stage.values()) : planResult.plannedStages();

        for (Stage stage : Stage.values()) {
            if (stage == Stage.PLAN) {
                continue;
            }
            if (!planned.contains(stage)) {
                views.put(stage, new StageView(stage, modeOf(stage), "SKIPPED", 0, "Not needed for this video"));
                activities.recordStage(videoId, stage, 0, modeOf(stage), "SKIPPED",
                        "Not needed for this video (decided by the plan)", null, null);
                continue;
            }
            execute(videoId, stage, request.prompt());
        }

        phase = "COMPLETED";
        currentStage = null;
        activities.setVideoStatus(videoId, "COMPLETED");
    }

    private StageResult execute(String videoId, Stage stage, String prompt) {
        return execute(videoId, stage, prompt, 0);
    }

    private StageResult execute(String videoId, Stage stage, String prompt, int previousAttempts) {
        String notes = null;
        int attempt = previousAttempts;
        while (true) {
            attempt++;
            if (paused) {
                activities.setVideoStatus(videoId, "PAUSED");
                Workflow.await(() -> !paused);
                activities.setVideoStatus(videoId, "RUNNING");
            }
            currentStage = stage;
            StageMode startMode = modeOf(stage);
            views.put(stage, new StageView(stage, startMode, "RUNNING", attempt, null));
            activities.recordStage(videoId, stage, attempt, startMode, "RUNNING", null, null, notes);

            StageResult result;
            try {
                result = runStageActivity(videoId, stage, prompt, notes, attempt);
                if (skipStage) {
                    skipStage = false;
                    finish(videoId, stage, attempt, "SKIPPED", result, notes);
                    return result;
                }
            } catch (ActivityFailure failure) {
                // A failed stage never kills the video: it waits for the owner, whatever the stage's mode.
                // Approve = continue without this stage's output; reject (with notes) = run it again.
                String reason = rootMessage(failure);
                Decision d = awaitOwner(videoId, stage, attempt, "FAILED", reason,
                        "Failed: " + reason + " Approve to continue without it, or Reject to retry.", null, notes, false);
                if (d.approve()) {
                    StageResult skipped = StageResult.of("Skipped by owner after failure: " + reason, null);
                    finish(videoId, stage, attempt, "SKIPPED", skipped, notes);
                    return skipped;
                }
                notes = d.notes();
                continue;
            }

            InspectionReport inspection = inspect(videoId, stage, attempt);
            if (inspection.summary() != null) {
                result = new StageResult(result.summary() + " " + inspection.summary(), result.artifactJson(),
                        result.plannedStages());
            }
            if ("FAIL".equals(inspection.verdict()) && modeOf(stage) != StageMode.MANUAL) {
                if (!inspectorRedone.contains(stage)) {
                    // First FAIL: redo once by itself. Images: only the shots that failed, with better prompts.
                    inspectorRedone.add(stage);
                    log.info("Inspector failed stage {}, redoing once", stage);
                    finish(videoId, stage, attempt, "INSPECTION_FAILED", result, notes);
                    notes = targetedRedo(stage, inspection, null) ? null : inspection.notes();
                    continue;
                }
                // Failed again after the automatic redo: never pass it unseen, whatever the mode or review timer.
                Decision d = awaitOwner(videoId, stage, attempt, "WAITING_APPROVAL", result.summary(),
                        result.summary() + " Approve to accept it anyway, or Reject (with notes) to redo.",
                        result.artifactJson(), notes, false);
                if (d.approve()) {
                    finish(videoId, stage, attempt, "APPROVED", result, notes);
                    return result;
                }
                finish(videoId, stage, attempt, "REJECTED", result, notes);
                String ownerNotes = d.notes() == null || d.notes().isBlank() ? null : d.notes();
                notes = targetedRedo(stage, inspection, ownerNotes) ? ownerNotes
                        : ownerNotes == null ? inspection.notes() : ownerNotes;
                continue;
            }

            if (modeOf(stage) == StageMode.AUTO) {
                finish(videoId, stage, attempt, "AUTO_PASSED", result, notes);
                return result;
            }

            if (modeOf(stage) == StageMode.REVIEW) {
                // Semi-auto: wait a review window for the owner; continue automatically if nobody responds.
                // Switching the stage to MANUAL during the window holds it until the owner decides.
                Duration window = Duration.ofMinutes(reviewMinutes);
                String deadline = java.time.Instant.ofEpochMilli(Workflow.currentTimeMillis()).plus(window).toString();
                views.put(stage, new StageView(stage, StageMode.REVIEW, "WAITING_APPROVAL", attempt, result.summary()));
                activities.recordStage(videoId, stage, attempt, StageMode.REVIEW, "WAITING_APPROVAL",
                        result.summary(), result.artifactJson(), notes);
                activities.setReviewDeadline(videoId, stage, attempt, deadline);
                phase = "WAITING";
                waitingStage = stage;
                decision = null;
                Workflow.await(window, () -> decision != null || modeOf(stage) != StageMode.REVIEW);
                if (decision == null && modeOf(stage) == StageMode.MANUAL) {
                    activities.setReviewDeadline(videoId, stage, attempt, null); // held: no timer any more
                    Workflow.await(() -> decision != null || modeOf(stage) == StageMode.AUTO);
                }
                waitingStage = null;
                phase = "RUNNING";
                Decision rd = decision;
                decision = null;
                if (rd == null) {
                    String how = modeOf(stage) == StageMode.REVIEW ? "AUTO_APPROVED" : "AUTO_PASSED";
                    finish(videoId, stage, attempt, how, result, notes);
                    return result;
                }
                if (rd.approve()) {
                    finish(videoId, stage, attempt, "APPROVED", result, notes);
                    return result;
                }
                finish(videoId, stage, attempt, "REJECTED", result, notes);
                notes = rd.notes();
                continue;
            }

            // MANUAL: wait for the owner, or for the stage to be switched to AUTO.
            Decision d = awaitOwner(videoId, stage, attempt, "WAITING_APPROVAL", result.summary(), result.summary(),
                    result.artifactJson(), notes, true);
            if (d == null) {
                finish(videoId, stage, attempt, "AUTO_PASSED", result, notes);
                return result;
            }
            if (d.approve()) {
                finish(videoId, stage, attempt, "APPROVED", result, notes);
                return result;
            }
            log.info("Stage {} rejected by owner, re-running", stage);
            finish(videoId, stage, attempt, "REJECTED", result, notes);
            notes = d.notes();
        }
    }

    /**
     * Records the stage as waiting and blocks until the owner approves/rejects (or, when {@code autoReleases}, until
     * the stage is switched to AUTO, which returns null).
     */
    private Decision awaitOwner(String videoId, Stage stage, int attempt, String status, String viewSummary,
                                String summary, String artifactJson, String notes, boolean autoReleases) {
        views.put(stage, new StageView(stage, modeOf(stage), status, attempt, viewSummary));
        activities.recordStage(videoId, stage, attempt, modeOf(stage), status, summary, artifactJson, notes);
        phase = "WAITING";
        waitingStage = stage;
        decision = null;
        Workflow.await(() -> decision != null || (autoReleases && modeOf(stage) == StageMode.AUTO));
        waitingStage = null;
        phase = "RUNNING";
        Decision d = decision;
        decision = null;
        return d;
    }

    /**
     * Images whose every problem names its shot are fixed one by one (the Creative Director's better prompts), not by
     * regenerating the whole set. Returns true when the next KEYFRAMES run will be such a targeted redo.
     */
    private boolean targetedRedo(Stage stage, InspectionReport inspection, String ownerNotes) {
        if (!Inspector.PER_SHOT.contains(stage) || inspection.redoTargets() == null || inspection.redoTargets().isEmpty()
                || !(stage == Stage.KEYFRAMES ? creativeDirectorEnabled() : videoClipsEnabled())) {
            return false;
        }
        pendingFix = new StageFix(stage, inspection.redoTargets(), ownerNotes);
        return true;
    }

    private StageFix takeFix(Stage stage) {
        StageFix fix = pendingFix != null && pendingFix.stage() == stage ? pendingFix : null;
        pendingFix = null;
        return fix;
    }

    /**
     * M2b clip stages. The batch is estimated first and, unless it is under the auto-approve amount, the owner must
     * approve the GPU spend before anything runs (CLAUDE.md rule 2); declining skips the stage.
     */
    private StageResult runClips(String videoId, Stage stage, int attempt, String notes) {
        String kind = stage == Stage.DRAFT_GEN ? "draft" : "final";
        StageFix fix = takeFix(stage);
        List<String> only = fix == null ? null : fix.shotIds();
        ClipEstimate estimate = activities.clipEstimate(videoId, kind, only);
        if (estimate.clips() == 0) {
            skipStage = true;
            return StageResult.of(estimate.summary(), null);
        }
        if (estimate.needsApproval()) {
            Decision d = awaitOwner(videoId, stage, attempt, "WAITING_APPROVAL", estimate.summary(),
                    estimate.summary() + " Approve to start, or Reject to skip this stage (nothing is spent).", null, notes, false);
            if (!d.approve()) {
                skipStage = true;
                return StageResult.of("Skipped: the owner didn't approve the GPU spend (" + estimate.summary() + ")", null);
            }
            activities.recordStage(videoId, stage, attempt, modeOf(stage), "RUNNING", null, null, notes);
            views.put(stage, new StageView(stage, modeOf(stage), "RUNNING", attempt, null));
        }
        ClipRequest request = agents.clipRequest(videoId, kind, only);
        return activities.saveClips(videoId, request, clipMedia.generateClips(request));
    }

    private StageResult runStageActivity(String videoId, Stage stage, String prompt, String notes, int attempt) {
        switch (stage) {
            case PLAN:
                return activities.plan(videoId, prompt, notes, attempt);
            case RESEARCH:
                return activities.saveResearch(videoId, media.researchTopic(agents.researchRequest(videoId, notes)));
            case CONCEPT:
                return agents.concept(videoId, notes);
            case SCRIPT:
                return agents.script(videoId, notes);
            case MUSIC:
                return activities.saveMusic(videoId, media.generateMusic(agents.musicRequest(videoId, notes)));
            case NARRATION:
                // Voiced before the shot plan so every shot is timed against the real narration length.
                return activities.saveNarration(videoId, media.synthesizeNarration(agents.narrationRequest(videoId)));
            case SHOT_PLAN:
                return agents.shotPlan(videoId, notes);
            case KEYFRAMES: {
                StageFix fix = takeFix(Stage.KEYFRAMES);
                var request = fix == null ? activities.keyframeRequest(videoId, notes)
                        : agents.fixKeyframeRequest(videoId, fix.shotIds(), fix.ownerNotes());
                return activities.saveKeyframes(videoId, request, media.generateKeyframes(request));
            }
            case ANIMATIC:
                return activities.saveAnimatic(videoId, media.renderAnimatic(activities.animaticRequest(videoId)));
            case LIBRARY_MATCH:
                if (libraryMatchEnabled()) {
                    // Free: suggests real NASA footage for the shots the Director marked; the owner picks.
                    var search = activities.footageSearchRequest(videoId, null, null);
                    if (search.queries().isEmpty()) {
                        return StageResult.of("No shots asked for real footage.", null);
                    }
                    return activities.saveFootageCandidates(videoId, media.searchFootage(search));
                }
                break;
            case DRAFT_GEN:
            case FINAL_GEN:
                if (videoClipsEnabled()) {
                    return runClips(videoId, stage, attempt, notes);
                }
                break;
            default:
                break;
        }
        if (stage == Stage.ASSEMBLY && assemblyEnabled()) {
            return activities.saveMaster(videoId, media.assembleVideo(activities.assemblyRequest(videoId)));
        }
        StageResult result = activities.runStage(videoId, stage, notes, attempt);
        if (stage == Stage.ASSEMBLY) {
            // M1: prove the polyglot path (Java workflow -> Python activity). Real assembly arrives in M2.
            String tools = media.toolVersions();
            return new StageResult(result.summary() + " Media worker reachable: " + tools, result.artifactJson(),
                    result.plannedStages());
        }
        return result;
    }

    /**
     * Second-level inspection of a finished stage: the Inspector's checks plus, for the shot plan and the images, the
     * Creative Director's story review. Never fails the stage itself: a check that can't run is reported as a warning
     * and production continues.
     */
    private InspectionReport inspect(String videoId, Stage stage, int attempt) {
        if (!Inspector.INSPECTED.contains(stage)) {
            return InspectionReport.SKIPPED;
        }
        // Workflows started before the Inspector existed replay without it (Temporal determinism).
        if (Workflow.getVersion("inspector", Workflow.DEFAULT_VERSION, 1) == Workflow.DEFAULT_VERSION) {
            return InspectionReport.SKIPPED;
        }
        try {
            MediaInspectRequest request = activities.inspectionRequest(videoId, stage);
            MediaInspectResult files = request == null ? null : inspectorMedia.inspectMedia(request);
            if ((stage == Stage.SHOT_PLAN || stage == Stage.KEYFRAMES) && creativeDirectorEnabled()) {
                files = merge(files, creativeReview(videoId, stage, attempt));
            }
            return activities.saveInspection(videoId, stage, attempt, files);
        } catch (ActivityFailure failure) {
            log.warn("Inspector could not check {}: {}", stage, rootMessage(failure));
            return new InspectionReport("WARN", "Inspector could not run: " + rootMessage(failure), null);
        }
    }

    private List<Finding> creativeReview(String videoId, Stage stage, int attempt) {
        try {
            if (stage == Stage.SHOT_PLAN) {
                return agents.creativeShotPlanReview(videoId, attempt);
            }
            ImageReviewRequest request = agents.imageReviewRequest(videoId);
            return activities.saveImageReview(videoId, attempt, inspectorMedia.reviewImages(request));
        } catch (ActivityFailure failure) {
            log.warn("Creative Director could not review {}: {}", stage, rootMessage(failure));
            return List.of(new Finding("WARN", "CREATIVE_REVIEW_UNAVAILABLE",
                    "The Creative Director could not review this: " + rootMessage(failure), stage.name().toLowerCase()));
        }
    }

    private static MediaInspectResult merge(MediaInspectResult files, List<Finding> more) {
        List<Finding> findings = new ArrayList<>(files == null || files.findings() == null ? List.of() : files.findings());
        findings.addAll(more);
        return new MediaInspectResult(findings, files == null || files.checked() == null ? List.of() : files.checked());
    }

    /** Workflows started before M2c replay the old placeholder Assembly (Temporal determinism). */
    private boolean assemblyEnabled() {
        return Workflow.getVersion("assembly", Workflow.DEFAULT_VERSION, 1) != Workflow.DEFAULT_VERSION;
    }

    /** Workflows started before real footage existed replay the old placeholder Library match (determinism). */
    private boolean libraryMatchEnabled() {
        return Workflow.getVersion("library-match", Workflow.DEFAULT_VERSION, 1) != Workflow.DEFAULT_VERSION;
    }

    /** Workflows started before M2b replay their old placeholder clip stages (Temporal determinism). */
    private boolean videoClipsEnabled() {
        return Workflow.getVersion("video-clips", Workflow.DEFAULT_VERSION, 1) != Workflow.DEFAULT_VERSION;
    }

    /** Workflows started before the Creative Director existed replay without it (Temporal determinism). */
    private boolean creativeDirectorEnabled() {
        return Workflow.getVersion("creative-director", Workflow.DEFAULT_VERSION, 1) != Workflow.DEFAULT_VERSION;
    }

    private static String rootMessage(Throwable t) {
        Throwable c = t;
        while (c.getCause() != null) {
            c = c.getCause();
        }
        String m = c.getMessage() == null ? c.getClass().getSimpleName() : c.getMessage();
        // ApplicationFailure messages look like "message='...', type='...'"; keep them readable and short.
        return m.length() > 400 ? m.substring(0, 400) + "…" : m;
    }

    private void finish(String videoId, Stage stage, int attempt, String status, StageResult result, String notes) {
        views.put(stage, new StageView(stage, modeOf(stage), status, attempt, result.summary()));
        activities.recordStage(videoId, stage, attempt, modeOf(stage), status, result.summary(),
                result.artifactJson(), notes);
    }

    private StageMode modeOf(Stage stage) {
        return modes.getOrDefault(stage, defaultMode);
    }

    @Override
    public void approve(Stage stage, String notes) {
        if (stage == waitingStage) {
            decision = new Decision(stage, true, notes);
        }
    }

    @Override
    public void reject(Stage stage, String notes) {
        if (stage == waitingStage) {
            decision = new Decision(stage, false, notes);
        }
    }

    @Override
    public void setMode(Stage stage, StageMode mode) {
        modes.put(stage, mode);
    }

    @Override
    public void setAllModes(StageMode mode) {
        defaultMode = mode;
        modes.clear();
    }

    @Override
    public void setReviewMinutes(int minutes) {
        if (minutes > 0) {
            reviewMinutes = minutes;
        }
    }

    @Override
    public void pause() {
        paused = true;
    }

    @Override
    public void resume() {
        paused = false;
    }

    @Override
    public WorkflowStatus status() {
        Map<Stage, StageMode> effective = new EnumMap<>(Stage.class);
        for (Stage s : Stage.values()) {
            effective.put(s, modeOf(s));
        }
        return new WorkflowStatus(phase, currentStage, paused, defaultMode, effective, new ArrayList<>(views.values()));
    }
}
