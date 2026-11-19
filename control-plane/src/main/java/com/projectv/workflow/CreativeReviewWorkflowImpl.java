package com.projectv.workflow;

import com.projectv.media.MediaTypes.Finding;
import com.projectv.workflow.WorkflowTypes.StageResult;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.workflow.Workflow;

import java.time.Duration;
import java.util.List;

/** Deterministic: all I/O in activities (same rules as the main workflow). */
public class CreativeReviewWorkflowImpl implements CreativeReviewWorkflow {

    private final VideoActivities activities = Workflow.newActivityStub(VideoActivities.class,
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
                    .setStartToCloseTimeout(Duration.ofMinutes(30))
                    .setHeartbeatTimeout(Duration.ofMinutes(5))
                    .setScheduleToCloseTimeout(Duration.ofHours(2))
                    .setRetryOptions(RetryOptions.newBuilder().setInitialInterval(Duration.ofSeconds(15)).setMaximumAttempts(2).build())
                    .build());

    @Override
    public StageResult run(CreativeReviewRequest r) {
        int attempt = Math.max(1, activities.lastAttempt(r.videoId(), Stage.KEYFRAMES));
        var request = activities.imageReviewRequest(r.videoId());
        List<Finding> findings = activities.saveImageReview(r.videoId(), attempt, media.reviewImages(request));
        long fails = findings.stream().filter(f -> "FAIL".equals(f.severity())).count();
        long warns = findings.stream().filter(f -> "WARN".equals(f.severity())).count();
        return StageResult.of("Creative Director reviewed the images: %d problem(s), %d warning(s).".formatted(fails, warns), null);
    }
}
