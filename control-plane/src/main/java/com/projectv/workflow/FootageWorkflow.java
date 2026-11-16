package com.projectv.workflow;

import com.projectv.workflow.WorkflowTypes.StageResult;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

/**
 * Real NASA footage for one shot, started by the owner: search (nasaId null) or use a picked clip (nasaId set).
 */
@WorkflowInterface
public interface FootageWorkflow {

    record FootageRequest(String videoId, String shotId, String query, String nasaId, Double startSec) {
    }

    @WorkflowMethod
    StageResult run(FootageRequest request);
}
