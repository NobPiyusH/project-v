package com.projectv.workflow;

import com.projectv.workflow.WorkflowTypes.VideoRequest;
import com.projectv.workflow.WorkflowTypes.WorkflowStatus;
import io.temporal.workflow.QueryMethod;
import io.temporal.workflow.SignalMethod;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

/** One long-running workflow per video (HANDOVER §9). Survives crashes and restarts. */
@WorkflowInterface
public interface VideoProductionWorkflow {

    @WorkflowMethod
    void run(VideoRequest request);

    /** Owner approves the stage that is waiting (manual mode). Ignored if that stage is not waiting. */
    @SignalMethod
    void approve(Stage stage, String notes);

    /** Owner rejects the waiting stage; it runs again with the notes as guidance. */
    @SignalMethod
    void reject(Stage stage, String notes);

    /** Switch one stage between AUTO and MANUAL. Takes effect immediately, even for a stage that is waiting. */
    @SignalMethod
    void setMode(Stage stage, StageMode mode);

    /** Switch every stage that hasn't finished yet. */
    @SignalMethod
    void setAllModes(StageMode mode);

    /** Review window for stages in REVIEW mode (applies from the next stage that starts waiting). */
    @SignalMethod
    void setReviewMinutes(int minutes);

    @SignalMethod
    void pause();

    @SignalMethod
    void resume();

    @QueryMethod
    WorkflowStatus status();
}
