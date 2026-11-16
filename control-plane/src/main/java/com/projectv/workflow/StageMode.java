package com.projectv.workflow;

/** R1: every stage runs in one of these modes, switchable per video and mid-run. */
public enum StageMode {
    /** The system does the stage and continues without waiting. */
    AUTO,
    /**
     * Semi-auto (owner request, 16 Nov 2026): the system does the stage, then waits a review window (default 10 min)
     * for the owner to approve, reject with notes, or hold it. If nobody responds, it continues automatically.
     */
    REVIEW,
    /** The system prepares the stage's output, then waits for the owner to approve, reject (with notes) or take over. */
    MANUAL;

    public static StageMode parse(String value) {
        return value == null ? AUTO : StageMode.valueOf(value.trim().toUpperCase());
    }
}
