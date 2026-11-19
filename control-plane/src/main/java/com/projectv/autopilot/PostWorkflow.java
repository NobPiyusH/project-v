package com.projectv.autopilot;

import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

/** Uploads one ready Short to YouTube after the owner tapped Post. Never started any other way. */
@WorkflowInterface
public interface PostWorkflow {

    @WorkflowMethod
    String post(String runId);

    /** Control-plane side of posting (CONTROL queue). */
    @ActivityInterface(namePrefix = "autopilot_")
    interface Activities {
        AutopilotTypes.YoutubeUploadRequest uploadRequest(String runId);

        String saveUpload(String runId, AutopilotTypes.YoutubeUploadResult result);
    }

    /** The upload itself, on the Python media worker. */
    @ActivityInterface
    interface Media {
        @ActivityMethod(name = "youtube_upload")
        AutopilotTypes.YoutubeUploadResult upload(AutopilotTypes.YoutubeUploadRequest request);
    }
}
