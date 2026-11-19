package com.projectv.dream;

import com.projectv.dream.DreamTypes.DreamRequest;
import com.projectv.dream.DreamTypes.DreamStep;
import com.projectv.dream.DreamTypes.DreamView;
import com.projectv.workflow.StageMode;
import io.temporal.workflow.QueryMethod;
import io.temporal.workflow.SignalMethod;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

import java.util.List;

/** One Dream Short: write → pictures → clips → finish, each step in Auto, Review or Manual (rule 2a). */
@WorkflowInterface
public interface DreamShortWorkflow {

    @WorkflowMethod
    String run(DreamRequest request);

    /** Accept the step's result (or the clips' cost) and go on. */
    @SignalMethod
    void approve(DreamStep step);

    /** Do the step again for these shots (null/empty = all), with the owner's notes. */
    @SignalMethod
    void redo(DreamStep step, List<String> shotIds, String notes);

    /** Switch a step's mode, also mid-run. */
    @SignalMethod
    void setMode(DreamStep step, StageMode mode);

    /** A failed step: try it again (rule 2b: a failure waits for the owner instead of ending the Short). */
    @SignalMethod
    void retry();

    @SignalMethod
    void cancel();

    @QueryMethod
    DreamView view();
}
