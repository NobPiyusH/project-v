package com.projectv.workflow;

/** R1: every stage runs in one of two modes, switchable per video and mid-run. */
public enum StageMode {
    /** The system does the stage and continues without waiting. */
    AUTO,
    /** The system prepares the stage's output, then waits for the owner to approve, reject (with notes) or take over. */
    MANUAL;

    public static StageMode parse(String value) {
        return value == null ? AUTO : StageMode.valueOf(value.trim().toUpperCase());
    }
}
