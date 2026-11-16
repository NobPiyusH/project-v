package com.projectv.workflow;

import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

/**
 * Activities implemented by the Python media worker (workers/). Names must match the Python
 * {@code @activity.defn(name=...)} declarations exactly.
 */
@ActivityInterface
public interface MediaActivities {

    /** Reports the media tool versions available on the worker (FFmpeg etc.). Proves the Java -> Python hop. */
    @ActivityMethod(name = "tool_versions")
    String toolVersions();
}
