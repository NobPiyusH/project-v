package com.projectv.workflow;

import com.projectv.workflow.WorkflowTypes.StageResult;
import io.temporal.activity.ActivityInterface;

/** Control-plane activities (Java). All database and LLM I/O happens here, never in workflow code. */
@ActivityInterface
public interface VideoActivities {

    /** PLAN stage: prompt (+ owner notes from a rejected attempt) -> VideoPlan, stored on the video. */
    StageResult plan(String videoId, String prompt, String ownerNotes, int attempt);

    /** Any other stage. In M1 these are placeholders that record what the stage will do. */
    StageResult runStage(String videoId, Stage stage, String ownerNotes, int attempt);

    /** Upsert the stage_run row and the video's status / current stage. */
    void recordStage(String videoId, Stage stage, int attempt, StageMode mode, String status,
                     String summary, String artifactJson, String ownerNotes);

    void setVideoStatus(String videoId, String status);
}
