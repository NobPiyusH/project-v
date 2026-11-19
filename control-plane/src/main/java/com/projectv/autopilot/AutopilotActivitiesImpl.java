package com.projectv.autopilot;

import org.springframework.stereotype.Component;

import java.util.UUID;

/** Temporal activities for posting: a thin adapter over {@link AutopilotService}. */
@Component
public class AutopilotActivitiesImpl implements PostWorkflow.Activities {

    private final AutopilotService autopilot;

    public AutopilotActivitiesImpl(AutopilotService autopilot) {
        this.autopilot = autopilot;
    }

    @Override
    public AutopilotTypes.YoutubeUploadRequest uploadRequest(String runId) {
        return autopilot.uploadRequest(UUID.fromString(runId));
    }

    @Override
    public String saveUpload(String runId, AutopilotTypes.YoutubeUploadResult result) {
        return autopilot.saveUpload(UUID.fromString(runId), result);
    }
}
