package com.projectv.workflow;

import com.projectv.workflow.WorkflowTypes.StageResult;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.failure.ActivityFailure;
import io.temporal.workflow.Workflow;

import java.time.Duration;

/** Deterministic: all I/O in activities (same rules as the main workflow). */
public class ShotRedoWorkflowImpl implements ShotRedoWorkflow {

    private final VideoActivities activities = Workflow.newActivityStub(VideoActivities.class,
            ActivityOptions.newBuilder()
                    .setStartToCloseTimeout(Duration.ofMinutes(5))
                    .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(10).build())
                    .build());

    // Budget-checked request: never retried after a budget refusal.
    private final VideoActivities guarded = Workflow.newActivityStub(VideoActivities.class,
            ActivityOptions.newBuilder()
                    .setStartToCloseTimeout(Duration.ofMinutes(5)) // may wait for a pod start (8-18 min): retries join it, see VideoGpu
                    .setRetryOptions(RetryOptions.newBuilder()
                            .setMaximumAttempts(8)
                            .setDoNotRetry(com.projectv.ledger.BudgetGuard.BUDGET_ERROR)
                            .build())
                    .build());

    private final MediaActivities media = Workflow.newActivityStub(MediaActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.MEDIA)
                    .setStartToCloseTimeout(Duration.ofMinutes(30))
                    .setHeartbeatTimeout(Duration.ofMinutes(10))
                    .setScheduleToCloseTimeout(Duration.ofHours(6))
                    .setRetryOptions(RetryOptions.newBuilder().setInitialInterval(Duration.ofSeconds(15)).setMaximumAttempts(3).build())
                    .build());

    @Override
    public StageResult run(ShotRedoRequest r) {
        activities.markShots(r.videoId(), r.shotIds(), "REDOING", null);
        try {
            var request = guarded.shotKeyframeRequest(r.videoId(), r.shotIds(), r.notes(), r.policy());
            var result = media.generateKeyframes(request);
            return activities.saveKeyframes(r.videoId(), request, result);
        } catch (ActivityFailure failure) {
            Throwable c = failure;
            while (c.getCause() != null) {
                c = c.getCause();
            }
            String reason = c.getMessage() == null ? c.getClass().getSimpleName() : c.getMessage();
            activities.markShots(r.videoId(), r.shotIds(), "FAILED", reason.length() > 400 ? reason.substring(0, 400) : reason);
            return StageResult.of("Shot redo failed: " + reason, null);
        }
    }
}
