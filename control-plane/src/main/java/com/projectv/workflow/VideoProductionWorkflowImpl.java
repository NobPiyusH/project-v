package com.projectv.workflow;

import com.projectv.workflow.WorkflowTypes.StageResult;
import com.projectv.workflow.WorkflowTypes.StageView;
import com.projectv.workflow.WorkflowTypes.VideoRequest;
import com.projectv.workflow.WorkflowTypes.WorkflowStatus;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.workflow.Workflow;
import org.slf4j.Logger;

import java.time.Duration;
import java.util.ArrayList;
import java.util.EnumMap;
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

    // Runs on the Python worker. Long schedule-to-close so the stage simply waits if the worker is offline.
    private final MediaActivities media = Workflow.newActivityStub(MediaActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.MEDIA)
                    .setStartToCloseTimeout(Duration.ofMinutes(2))
                    .setScheduleToCloseTimeout(Duration.ofHours(24))
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

    @Override
    public void run(VideoRequest request) {
        defaultMode = request.defaultMode() == null ? StageMode.AUTO : request.defaultMode();
        if (request.modes() != null) {
            modes.putAll(request.modes());
        }
        String videoId = request.videoId();
        phase = "RUNNING";
        activities.setVideoStatus(videoId, "RUNNING");

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
        String notes = null;
        int attempt = 0;
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

            StageResult result = runStageActivity(videoId, stage, prompt, notes, attempt);

            if (modeOf(stage) == StageMode.AUTO) {
                finish(videoId, stage, attempt, "AUTO_PASSED", result, notes);
                return result;
            }

            // MANUAL: wait for the owner, or for the stage to be switched to AUTO.
            views.put(stage, new StageView(stage, StageMode.MANUAL, "WAITING_APPROVAL", attempt, result.summary()));
            activities.recordStage(videoId, stage, attempt, StageMode.MANUAL, "WAITING_APPROVAL",
                    result.summary(), result.artifactJson(), notes);
            phase = "WAITING";
            waitingStage = stage;
            decision = null;
            Workflow.await(() -> decision != null || modeOf(stage) == StageMode.AUTO);
            waitingStage = null;
            phase = "RUNNING";
            Decision d = decision;
            decision = null;

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

    private StageResult runStageActivity(String videoId, Stage stage, String prompt, String notes, int attempt) {
        if (stage == Stage.PLAN) {
            return activities.plan(videoId, prompt, notes, attempt);
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
