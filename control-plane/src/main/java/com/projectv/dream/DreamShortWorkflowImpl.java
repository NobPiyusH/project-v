package com.projectv.dream;

import com.projectv.dream.DreamTypes.DreamEstimate;
import com.projectv.dream.DreamTypes.DreamRequest;
import com.projectv.dream.DreamTypes.DreamStep;
import com.projectv.dream.DreamTypes.DreamView;
import com.projectv.workflow.MediaActivities;
import com.projectv.workflow.StageMode;
import com.projectv.workflow.TaskQueues;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.failure.ActivityFailure;
import io.temporal.workflow.Workflow;

import java.time.Duration;
import java.time.Instant;
import java.util.EnumMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Deterministic (rule 5): every DB, LLM, GPU and file operation is an activity; the workflow only sees IDs and text.
 * <ul>
 *   <li>each step runs, then passes its gate: AUTO goes on; REVIEW waits the review window then goes on by itself;
 *       MANUAL waits for the owner (approve, or redo with notes / for some shots);</li>
 *   <li>clips always show their cost first and wait for the owner's OK when it is above the auto-approve amount
 *       (owner rule: ask before spending);</li>
 *   <li>a failed step waits for the owner's Retry instead of ending the Short (rule 2b).</li>
 * </ul>
 */
public class DreamShortWorkflowImpl implements DreamShortWorkflow {

    static final String BUDGET_FAILURE = "com.projectv.ledger.BudgetGuard$BudgetExceededException";

    private final DreamActivities activities = Workflow.newActivityStub(DreamActivities.class,
            ActivityOptions.newBuilder()
                    .setStartToCloseTimeout(Duration.ofMinutes(6)) // the writer's LLM call can take a few minutes
                    .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(3).setDoNotRetry(BUDGET_FAILURE).build())
                    .build());

    private final MediaActivities pictures = Workflow.newActivityStub(MediaActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.MEDIA)
                    .setStartToCloseTimeout(Duration.ofMinutes(30))
                    .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(1).build())
                    .build());

    // Never retried automatically: a retry would pay for the clips again. The owner's Retry decides.
    private final MediaActivities clips = Workflow.newActivityStub(MediaActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.MEDIA)
                    .setStartToCloseTimeout(Duration.ofHours(4))
                    .setHeartbeatTimeout(Duration.ofMinutes(3))
                    .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(1).build())
                    .build());

    private final DreamMediaActivities finish = Workflow.newActivityStub(DreamMediaActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.MEDIA)
                    .setStartToCloseTimeout(Duration.ofHours(1))
                    .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(2).build())
                    .build());

    private String dreamId;
    private StageMode defaultMode = StageMode.AUTO;
    private final Map<DreamStep, StageMode> modes = new EnumMap<>(DreamStep.class);
    private Duration reviewWindow = Duration.ofMinutes(10);

    private DreamStep current;
    private String status = "RUNNING";
    private String message;
    private String deadline;

    private DreamStep approved;                // set by approve(step)
    private Redo redo;                         // set by redo(...)
    private boolean retry;
    private boolean cancelled;

    private record Redo(DreamStep step, List<String> shotIds, String notes) {
    }

    @Override
    public String run(DreamRequest request) {
        dreamId = request.dreamId();
        defaultMode = request.defaultMode() == null ? StageMode.AUTO : request.defaultMode();
        if (request.modes() != null) {
            modes.putAll(request.modes());
        }
        reviewWindow = Duration.ofMinutes(Math.max(1, request.reviewMinutes()));
        for (DreamStep step : DreamStep.values()) {
            if (!runStep(step)) {
                show(step, "CANCELLED", "Cancelled by the owner.");
                return "cancelled";
            }
        }
        show(DreamStep.FINISH, "COMPLETED", message);
        return "completed";
    }

    /** Runs one step until the owner (or its mode) accepts it. false = cancelled. */
    private boolean runStep(DreamStep step) {
        current = step;
        List<String> only = null;
        String notes = null;
        while (true) {
            if (cancelled) {
                return false;
            }
            show(step, "RUNNING", working(step));
            String summary;
            try {
                summary = execute(step, only, notes);
            } catch (ActivityFailure failure) {
                show(step, "FAILED_WAITING", "Failed: " + rootMessage(failure) + " Retry when ready, or cancel.");
                retry = false;
                Workflow.await(() -> retry || cancelled);
                continue;
            }
            if (summary == null) {
                return false; // cancelled while waiting for the clips' go-ahead
            }
            message = summary;
            Redo r = gate(step, summary);
            if (cancelled) {
                return false;
            }
            if (r == null) {
                return true;
            }
            only = r.shotIds() == null || r.shotIds().isEmpty() ? null : r.shotIds();
            notes = r.notes();
            if (step == DreamStep.WRITE) {
                only = null; // the writer always rewrites the whole Short (one world)
            }
        }
    }

    private String execute(DreamStep step, List<String> only, String notes) {
        return switch (step) {
            case WRITE -> activities.writeShots(dreamId, notes);
            case PICTURES -> {
                var req = activities.pictureRequest(dreamId, only, notes);
                yield req.jobs().isEmpty() ? "Every shot already has a picture."
                        : activities.savePictures(dreamId, req, pictures.generateKeyframes(req));
            }
            case CLIPS -> {
                DreamEstimate est = activities.clipEstimate(dreamId, only);
                if (est.needsApproval()) {
                    approved = null;
                    show(step, "WAITING_COST", est.summary() + " Approve to start.");
                    Workflow.await(() -> approved == DreamStep.CLIPS || cancelled);
                    approved = null;
                    if (cancelled) {
                        yield null;
                    }
                    show(step, "RUNNING", working(step));
                }
                activities.startClipBatch(dreamId);
                var req = activities.clipRequest(dreamId, only);
                yield activities.saveClips(dreamId, req, clips.generateClips(req));
            }
            case FINISH -> activities.saveFinish(dreamId, finish.dreamFinish(activities.finishRequest(dreamId)));
        };
    }

    /** What the dashboard says while a step runs (so an old "approve" message never lingers). */
    static String working(DreamStep step) {
        return switch (step) {
            case WRITE -> "Writing the world and the shots…";
            case PICTURES -> "Making the pictures…";
            case CLIPS -> "Making the clips on the GPU…";
            case FINISH -> "Finishing on the laptop: smoothing, dissolves, look, music…";
        };
    }

    /** null = accepted; otherwise the owner's redo. */
    private Redo gate(DreamStep step, String summary) {
        approved = null;
        redo = null;
        StageMode mode = modeOf(step);
        if (mode == StageMode.AUTO) {
            return null;
        }
        if (mode == StageMode.MANUAL) {
            show(step, "WAITING", summary);
            Workflow.await(() -> decided(step) || modeOf(step) == StageMode.AUTO);
            return take(step);
        }
        // REVIEW (and PRODUCER, which has no AI producer for Dream Shorts yet): wait the window, then go on.
        deadline = Instant.ofEpochMilli(Workflow.currentTimeMillis()).plus(reviewWindow).toString();
        show(step, "WAITING_REVIEW", summary);
        Workflow.await(reviewWindow, () -> decided(step) || modeOf(step) == StageMode.MANUAL || modeOf(step) == StageMode.AUTO);
        if (!decided(step) && modeOf(step) == StageMode.MANUAL) {
            show(step, "WAITING", summary);
            Workflow.await(() -> decided(step) || modeOf(step) == StageMode.AUTO);
        }
        deadline = null;
        return take(step);
    }

    private boolean decided(DreamStep step) {
        return cancelled || approved == step || (redo != null && redo.step() == step);
    }

    private Redo take(DreamStep step) {
        Redo r = redo != null && redo.step() == step ? redo : null;
        approved = null;
        redo = null;
        return r;
    }

    private StageMode modeOf(DreamStep step) {
        return modes.getOrDefault(step, defaultMode);
    }

    private void show(DreamStep step, String newStatus, String newMessage) {
        status = newStatus;
        if (newMessage != null) {
            message = newMessage;
        }
        activities.status(dreamId, step.name(), newStatus, newMessage);
    }

    private static String rootMessage(Throwable t) {
        Throwable c = t;
        while (c.getCause() != null) {
            c = c.getCause();
        }
        String m = c.getMessage() == null ? c.getClass().getSimpleName() : c.getMessage();
        return m.length() > 400 ? m.substring(0, 400) : m;
    }

    // ------------------------------------------------------------------------------------------- signals & query

    @Override
    public void approve(DreamStep step) {
        approved = step;
    }

    @Override
    public void redo(DreamStep step, List<String> shotIds, String notes) {
        redo = new Redo(step, shotIds, notes);
    }

    @Override
    public void setMode(DreamStep step, StageMode mode) {
        modes.put(step, mode);
    }

    @Override
    public void retry() {
        retry = true;
    }

    @Override
    public void cancel() {
        cancelled = true;
    }

    @Override
    public DreamView view() {
        Map<String, String> m = new LinkedHashMap<>();
        for (DreamStep s : DreamStep.values()) {
            m.put(s.name(), modeOf(s).name());
        }
        return new DreamView(current == null ? null : current.name(), status, message, m, deadline);
    }
}
