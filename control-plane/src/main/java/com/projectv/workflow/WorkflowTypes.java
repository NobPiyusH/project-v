package com.projectv.workflow;

import java.util.List;
import java.util.Map;

/** Small, serialisable types passed through Temporal. Never media bytes: only IDs, text and small JSON. */
public final class WorkflowTypes {

    private WorkflowTypes() {
    }

    /** Workflow input. {@code modes} holds per-stage overrides; stages not listed use {@code defaultMode}. */
    public record VideoRequest(String videoId, String prompt, StageMode defaultMode, Map<Stage, StageMode> modes) {
    }

    /**
     * Result of one stage attempt. {@code plannedStages} is only set by the PLAN stage and decides which
     * later stages run.
     */
    public record StageResult(String summary, String artifactJson, List<Stage> plannedStages) {
        public static StageResult of(String summary, String artifactJson) {
            return new StageResult(summary, artifactJson, null);
        }
    }

    public record StageView(Stage stage, StageMode mode, String status, int attempt, String summary) {
    }

    public record WorkflowStatus(String phase, Stage currentStage, boolean paused, StageMode defaultMode,
                                 Map<Stage, StageMode> modes, List<StageView> stages) {
    }
}
