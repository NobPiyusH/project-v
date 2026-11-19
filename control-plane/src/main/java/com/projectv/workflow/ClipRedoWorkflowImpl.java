package com.projectv.workflow;

import com.projectv.workflow.WorkflowTypes.StageResult;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.failure.ActivityFailure;
import io.temporal.workflow.Workflow;

import java.time.Duration;

/** Deterministic: all I/O in activities (same rules as the main workflow). */
public class ClipRedoWorkflowImpl implements ClipRedoWorkflow {

    private final VideoActivities activities = Workflow.newActivityStub(VideoActivities.class,
            ActivityOptions.newBuilder()
                    .setStartToCloseTimeout(Duration.ofMinutes(2))
                    .setRetryOptions(RetryOptions.newBuilder()
                            .setMaximumAttempts(3)
                            .setDoNotRetry(com.projectv.ledger.BudgetGuard.BUDGET_ERROR)
                            .build())
                    .build());

    // Never retried: a retry would pay for the clips again.
    private final MediaActivities media = Workflow.newActivityStub(MediaActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.MEDIA)
                    .setStartToCloseTimeout(Duration.ofHours(4))
                    .setHeartbeatTimeout(Duration.ofMinutes(3))
                    .setScheduleToCloseTimeout(Duration.ofHours(24))
                    .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(1).build())
                    .build());

    @Override
    public StageResult run(ClipRedoRequest r) {
        try {
            var request = activities.clipRequest(r.videoId(), r.kind(), r.shotIds());
            return activities.saveClips(r.videoId(), request, media.generateClips(request));
        } catch (ActivityFailure failure) {
            Throwable c = failure;
            while (c.getCause() != null) {
                c = c.getCause();
            }
            String reason = c.getMessage() == null ? c.getClass().getSimpleName() : c.getMessage();
            activities.markClips(r.videoId(), r.shotIds(), "FAILED", reason.length() > 400 ? reason.substring(0, 400) : reason);
            return StageResult.of("Clip redo failed: " + reason, null);
        }
    }
}
