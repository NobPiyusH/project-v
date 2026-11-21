package com.projectv.dream;

import com.projectv.dream.DreamTypes.DreamEstimate;
import com.projectv.dream.DreamTypes.DreamRequest;
import com.projectv.dream.DreamTypes.DreamStep;
import com.projectv.dream.DreamTypes.DreamView;
import com.projectv.media.MediaTypes.ProducerAdvice;
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
                    // Outer cap only: on the shared pod a Short's pictures can wait behind other Shorts' jobs. Each job's own
                    // limit counts from when the pod starts it (workers/pod/client.py RUN_LIMIT_S), so queued time is free.
                    .setStartToCloseTimeout(Duration.ofHours(4))
                    // The worker beats every few seconds while a job is queued or running; 12 min rides out a laptop stall
                    // (21 Nov 2026: ~10 min) and still catches a dead media worker well before the outer cap.
                    .setHeartbeatTimeout(Duration.ofMinutes(12))
                    .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(1).build())
                    .build());

    // Never retried automatically: a retry would pay for the clips again. The owner's Retry decides.
    private final MediaActivities clips = Workflow.newActivityStub(MediaActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.MEDIA)
                    .setStartToCloseTimeout(Duration.ofHours(8)) // outer cap; per-clip run limit is on the worker (see pictures)
                    .setHeartbeatTimeout(Duration.ofMinutes(12))
                    .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(1).build())
                    .build());

    // Paid (Lyria): no automatic retry; the owner's Retry decides.
    private final MediaActivities music = Workflow.newActivityStub(MediaActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.MEDIA)
                    .setStartToCloseTimeout(Duration.ofMinutes(15))
                    .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(1).build())
                    .build());

    private final DreamMediaActivities finish = Workflow.newActivityStub(DreamMediaActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.MEDIA)
                    .setStartToCloseTimeout(Duration.ofHours(1))
                    .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(2).build())
                    .build());

    // AI Producer: what to review + saving it + may it act alone (control queue); the review on the media worker.
    private final DreamProducerActivities producer = Workflow.newActivityStub(DreamProducerActivities.class,
            ActivityOptions.newBuilder()
                    .setStartToCloseTimeout(Duration.ofMinutes(2))
                    .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(3).build())
                    .build());

    private final com.projectv.workflow.ProducerMediaActivities producerMedia = Workflow.newActivityStub(
            com.projectv.workflow.ProducerMediaActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.MEDIA)
                    .setStartToCloseTimeout(Duration.ofMinutes(20))
                    .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(2).build())
                    .build());

    private final Map<DreamStep, Integer> reviews = new EnumMap<>(DreamStep.class);
    private final Map<DreamStep, Integer> producerRounds = new EnumMap<>(DreamStep.class);

    private String dreamId;
    private String spendNote;                  // the Producer's reason for asking about a cost
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
        // Version 1 added the Scout step before Write; older runs replay without it.
        boolean scoutable = Workflow.getVersion("dream-scout", Workflow.DEFAULT_VERSION, 1) == 1;
        DreamStep from = request.fromStep() == null ? (scoutable ? DreamStep.SCOUT : DreamStep.WRITE) : request.fromStep();
        for (DreamStep step : DreamStep.values()) {
            if (step.ordinal() < from.ordinal()) {
                continue; // redo from a later step: earlier results stay as they are
            }
            if (step == DreamStep.SCOUT && (!scoutable || !activities.scoutWanted(dreamId))) {
                continue; // the owner switched the Scout off for this Short
            }
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
            case SCOUT -> {
                var req = activities.scoutRequest(dreamId, notes);
                yield activities.saveScout(dreamId, req, finish.dreamScout(req));
            }
            case WRITE -> activities.writeShots(dreamId, notes);
            case PICTURES -> {
                var req = activities.pictureRequest(dreamId, only, notes);
                String made = req.jobs().isEmpty() ? "Every shot already has a picture."
                        : activities.savePictures(dreamId, req, pictures.generateKeyframes(req));
                // Version 1 added a check of the pictures before clips are paid for; older runs replay without it.
                if (!req.jobs().isEmpty() && Workflow.getVersion("dream-picture-check", Workflow.DEFAULT_VERSION, 1) == 1) {
                    try {
                        var check = activities.pictureCheckRequest(dreamId);
                        if (check != null) {
                            made = made + " " + activities.savePictureCheck(dreamId, finish.dreamCheck(check));
                        }
                    } catch (ActivityFailure e) {
                        // a failed check never holds up the pictures
                    }
                }
                yield made;
            }
            case CLIPS -> {
                DreamEstimate est = activities.clipEstimate(dreamId, only);
                if (est.needsApproval() && !producerApprovesSpend(step, est)) {
                    approved = null;
                    show(step, "WAITING_COST", est.summary() + (spendNote == null ? "" : " " + spendNote) + " Approve to start.");
                    spendNote = null;
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
            case MUSIC -> {
                // Version 1 added the owner's music bed (free, the default); older runs replay as Lyria-only.
                int v = Workflow.getVersion("dream-music-bed", Workflow.DEFAULT_VERSION, 1);
                if (v == 1 && "bed".equals(activities.musicSource(dreamId))) {
                    yield activities.saveMusicBed(dreamId, finish.dreamMusicBed(activities.musicBedRequest(dreamId)));
                }
                yield activities.saveMusic(dreamId, music.generateMusic(activities.musicRequest(dreamId, notes)));
            }
            case FINISH -> {
                String done = activities.saveFinish(dreamId, finish.dreamFinish(activities.finishRequest(dreamId)));
                // Version 1 added the quality check after the finish; older runs replay without it.
                if (Workflow.getVersion("dream-check", Workflow.DEFAULT_VERSION, 1) == 1) {
                    try {
                        var req = activities.checkRequest(dreamId);
                        if (req != null) {
                            done = done + " " + activities.saveCheck(dreamId, finish.dreamCheck(req));
                        }
                    } catch (ActivityFailure e) {
                        // a failed check never holds up a finished Short
                    }
                }
                yield done;
            }
        };
    }

    /** What the dashboard says while a step runs (so an old "approve" message never lingers). */
    static String working(DreamStep step) {
        return switch (step) {
            case SCOUT -> "Finding and watching similar Shorts…";
            case WRITE -> "Writing the world and the shots…";
            case PICTURES -> "Making the pictures…";
            case CLIPS -> "Making the clips on the GPU…";
            case MUSIC -> "Making the music…";
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
        // Version 1: the AI Producer reviews Dream Shorts. Older runs replay PRODUCER like REVIEW.
        if (mode == StageMode.PRODUCER && Workflow.getVersion("dream-producer", Workflow.DEFAULT_VERSION, 1) == 1) {
            return producerGate(step, summary);
        }
        // REVIEW: wait the window, then go on.
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

    /**
     * PRODUCER mode: it reviews the step. When it may act alone (autopilot Short, or the owner trusts it) it approves,
     * or redoes the weak shots with its precise notes (3 rounds, inside the ₹ limits); anything else waits for the owner
     * with its advice in front of them. Finished Shorts are never redone by it (a redo of Finish changes nothing).
     */
    private Redo producerGate(DreamStep step, String summary) {
        show(step, "RUNNING", "AI Producer is reviewing…");
        ProducerAdvice advice = review(step);
        if (advice == null) {
            message = summary;
            return null; // nothing it judges here: passes like AUTO
        }
        String text = summary + " " + advice.summary();
        if (advice.decisionId() != null && producer.decides(dreamId)) {
            if ("approve".equals(advice.verdict())) {
                producer.markActed(advice.decisionId(), "approved");
                message = text;
                return null;
            }
            int round = producerRounds.merge(step, 1, Integer::sum);
            if ("redo".equals(advice.verdict()) && step != DreamStep.FINISH && producer.mayRedo(dreamId, round)) {
                producer.markActed(advice.decisionId(), "redo");
                List<String> shots = producer.shotIds(dreamId, advice.weakItems());
                return new Redo(step, shots, advice.redoNotes());
            }
            producer.markActed(advice.decisionId(), "asked");
        }
        show(step, "WAITING", text + " Approve, or Redo with notes.");
        Workflow.await(() -> decided(step) || modeOf(step) == StageMode.AUTO);
        return take(step);
    }

    /** The Producer's review as advice; null when it judges nothing here. A failed review never fails the step. */
    private ProducerAdvice review(DreamStep step) {
        int attempt = reviews.merge(step, 1, Integer::sum);
        try {
            var request = producer.reviewRequest(dreamId, step.name());
            return request == null ? null : producer.saveReview(dreamId, step.name(), attempt, producerMedia.review(request));
        } catch (ActivityFailure failure) {
            return new ProducerAdvice("ask", 0, "AI Producer could not review this step (" + rootMessage(failure)
                    + "); please decide.", null, null);
        }
    }

    /** In PRODUCER mode a deciding Producer approves the clips' cost by itself within the owner's limits. */
    private boolean producerApprovesSpend(DreamStep step, DreamEstimate est) {
        if (modeOf(step) != StageMode.PRODUCER
                || Workflow.getVersion("dream-producer-spend", Workflow.DEFAULT_VERSION, 1) != 1
                || !producer.decides(dreamId)) {
            return false;
        }
        var decision = producer.spendDecision(dreamId, est.inr());
        if (!decision.approve()) {
            spendNote = decision.text();
        }
        return decision.approve();
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
