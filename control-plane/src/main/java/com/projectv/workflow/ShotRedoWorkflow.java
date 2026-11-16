package com.projectv.workflow;

import com.projectv.workflow.WorkflowTypes.StageResult;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

import java.util.List;

/**
 * Per-shot redo (owner request): regenerate the keyframes of just the chosen shots, with notes, instead of a whole
 * stage. Separate from the main production workflow so it also works on finished, paused or waiting videos.
 * Every version is kept; the newest becomes active (the owner can switch back for free).
 */
@WorkflowInterface
public interface ShotRedoWorkflow {

    /** @param policy AUTO | PREMIUM | FREE (see ProductionService.ImagePolicy) */
    record ShotRedoRequest(String videoId, List<String> shotIds, String notes, String policy) {
    }

    @WorkflowMethod
    StageResult run(ShotRedoRequest request);
}
