package com.projectv.workflow;

import com.projectv.workflow.WorkflowTypes.StageResult;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.failure.ActivityFailure;
import io.temporal.workflow.Workflow;

import java.time.Duration;

/** Deterministic: all I/O in activities. Mirrors ShotRedoWorkflowImpl. */
public class VoiceRedoWorkflowImpl implements VoiceRedoWorkflow {

    private final VideoActivities activities = Workflow.newActivityStub(VideoActivities.class,
            ActivityOptions.newBuilder()
                    .setStartToCloseTimeout(Duration.ofMinutes(5))
                    .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(10).build())
                    .build());

    private final VideoActivities guarded = Workflow.newActivityStub(VideoActivities.class,
            ActivityOptions.newBuilder()
                    .setStartToCloseTimeout(Duration.ofMinutes(2))
                    .setRetryOptions(RetryOptions.newBuilder()
                            .setMaximumAttempts(3)
                            .setDoNotRetry(com.projectv.ledger.BudgetGuard.BUDGET_ERROR)
                            .build())
                    .build());

    private final MediaActivities media = Workflow.newActivityStub(MediaActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.MEDIA)
                    .setStartToCloseTimeout(Duration.ofMinutes(20))
                    .setHeartbeatTimeout(Duration.ofMinutes(10))
                    .setScheduleToCloseTimeout(Duration.ofHours(6))
                    .setRetryOptions(RetryOptions.newBuilder().setInitialInterval(Duration.ofSeconds(15)).setMaximumAttempts(3).build())
                    .build());

    @Override
    public StageResult run(VoiceRedoRequest r) {
        activities.markLines(r.videoId(), r.lineIndexes(), "REDOING", null);
        try {
            var request = guarded.lineNarrationRequest(r.videoId(), r.lineIndexes(), r.styleNotes());
            var result = media.synthesizeNarration(request);
            return activities.saveLineNarration(r.videoId(), result, request.style());
        } catch (ActivityFailure failure) {
            Throwable c = failure;
            while (c.getCause() != null) {
                c = c.getCause();
            }
            String reason = c.getMessage() == null ? c.getClass().getSimpleName() : c.getMessage();
            activities.markLines(r.videoId(), r.lineIndexes(), "FAILED", reason.length() > 400 ? reason.substring(0, 400) : reason);
            return StageResult.of("Voice redo failed: " + reason, null);
        }
    }
}
