package com.projectv.workflow;

public final class TaskQueues {
    /** Java worker inside the control plane: workflows and control activities. */
    public static final String CONTROL = "projectv-control";
    /** Python media worker (workers/): FFmpeg, TTS, captions, critic, generation clients. */
    public static final String MEDIA = "projectv-media";

    private TaskQueues() {
    }
}
