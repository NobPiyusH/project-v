package com.projectv.workflow;

import com.projectv.workflow.WorkflowTypes.StageResult;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

/**
 * Owner-started Creative Director review of a video's existing images (no new images are made). Also lets older
 * videos, made before the Creative Director existed, get a report card.
 */
@WorkflowInterface
public interface CreativeReviewWorkflow {

    record CreativeReviewRequest(String videoId) {
    }

    @WorkflowMethod
    StageResult run(CreativeReviewRequest request);
}
