package com.projectv.autopilot;

import com.projectv.workflow.TaskQueues;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.failure.ActivityFailure;
import io.temporal.workflow.Workflow;

import java.time.Duration;

/**
 * Deterministic (rule 5): the upload is an activity. It is tried ONCE: a retry after a lost reply could post the Short
 * twice, so a failure goes back to the owner as POST_FAILED with the reason, and Post can be tapped again.
 */
public class PostWorkflowImpl implements PostWorkflow {

    private final Activities control = Workflow.newActivityStub(Activities.class, ActivityOptions.newBuilder()
            .setStartToCloseTimeout(Duration.ofMinutes(1))
            .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(3).build())
            .build());

    private final Media media = Workflow.newActivityStub(Media.class, ActivityOptions.newBuilder()
            .setTaskQueue(TaskQueues.MEDIA)
            .setStartToCloseTimeout(Duration.ofMinutes(30))
            .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(1).build())
            .build());

    @Override
    public String post(String runId) {
        var request = control.uploadRequest(runId);
        AutopilotTypes.YoutubeUploadResult result;
        try {
            result = media.upload(request);
        } catch (ActivityFailure failure) {
            Throwable c = failure;
            while (c.getCause() != null) {
                c = c.getCause();
            }
            result = new AutopilotTypes.YoutubeUploadResult(null, null, request.privacy(), String.valueOf(c.getMessage()));
        }
        return control.saveUpload(runId, result);
    }
}
