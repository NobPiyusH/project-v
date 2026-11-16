package com.projectv.workflow;

import com.projectv.workflow.WorkflowTypes.StageResult;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

import java.util.List;

/**
 * Per-line voice redo (owner request): re-voice just the chosen narration lines, optionally with a different
 * reading style. Every take passes the transcript check; all takes are kept and the owner can switch back free.
 */
@WorkflowInterface
public interface VoiceRedoWorkflow {

    record VoiceRedoRequest(String videoId, List<Integer> lineIndexes, String styleNotes) {
    }

    @WorkflowMethod
    StageResult run(VoiceRedoRequest request);
}
