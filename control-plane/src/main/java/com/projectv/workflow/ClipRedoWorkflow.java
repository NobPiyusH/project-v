package com.projectv.workflow;

import com.projectv.workflow.WorkflowTypes.StageResult;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

import java.util.List;

/** Per-shot clip redo (draft or final) started by the owner, who saw the estimate before confirming. */
@WorkflowInterface
public interface ClipRedoWorkflow {

    record ClipRedoRequest(String videoId, List<String> shotIds, String kind) {
    }

    @WorkflowMethod
    StageResult run(ClipRedoRequest request);
}
