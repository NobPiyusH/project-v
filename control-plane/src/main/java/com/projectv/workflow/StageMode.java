package com.projectv.workflow;

/** R1: every stage runs in one of these modes, switchable per video and mid-run. */
public enum StageMode {
    /** The system does the stage and continues without waiting. */
    AUTO,
    /**
     * AI Producer (owner, 18 Nov 2026; docs/plans/ai-producer.md): an AI producer reviews the stage's output (reads,
     * looks, watches, listens) and recommends approve / redo / ask. P1 is advise-only: the owner decides with the
     * recommendation in front of them; stages it doesn't review pass like AUTO.
     */
    PRODUCER,
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
