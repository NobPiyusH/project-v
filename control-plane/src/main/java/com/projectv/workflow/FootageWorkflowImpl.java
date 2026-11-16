package com.projectv.workflow;

import com.projectv.workflow.WorkflowTypes.StageResult;
import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.failure.ActivityFailure;
import io.temporal.workflow.Workflow;

import java.time.Duration;
import java.util.List;

/** Deterministic: all I/O in activities (same rules as the main workflow). Free: NASA media costs nothing. */
public class FootageWorkflowImpl implements FootageWorkflow {

    private final VideoActivities activities = Workflow.newActivityStub(VideoActivities.class,
            ActivityOptions.newBuilder()
                    .setStartToCloseTimeout(Duration.ofMinutes(2))
                    .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(3).build())
                    .build());

    private final MediaActivities media = Workflow.newActivityStub(MediaActivities.class,
            ActivityOptions.newBuilder()
                    .setTaskQueue(TaskQueues.MEDIA)
                    .setStartToCloseTimeout(Duration.ofMinutes(20))
                    .setScheduleToCloseTimeout(Duration.ofHours(2))
                    .setRetryOptions(RetryOptions.newBuilder().setInitialInterval(Duration.ofSeconds(10)).setMaximumAttempts(3).build())
                    .build());

    @Override
    public StageResult run(FootageRequest r) {
        try {
            if (r.nasaId() == null) {
                var search = activities.footageSearchRequest(r.videoId(), List.of(r.shotId()), r.query());
                return activities.saveFootageCandidates(r.videoId(), media.searchFootage(search));
            }
            activities.markClips(r.videoId(), List.of(r.shotId()), "RENDERING", null);
            var fetch = activities.footageFetchRequest(r.videoId(), r.shotId(), r.nasaId(), r.startSec());
            return activities.saveFootage(r.videoId(), fetch, media.fetchFootage(fetch));
        } catch (ActivityFailure failure) {
            Throwable c = failure;
            while (c.getCause() != null) {
                c = c.getCause();
            }
            String reason = c.getMessage() == null ? c.getClass().getSimpleName() : c.getMessage();
            if (r.nasaId() != null) {
                activities.markClips(r.videoId(), List.of(r.shotId()), "FAILED",
                        "NASA footage: " + (reason.length() > 380 ? reason.substring(0, 380) : reason));
            }
            return StageResult.of("Footage failed: " + reason, null);
        }
    }
}
