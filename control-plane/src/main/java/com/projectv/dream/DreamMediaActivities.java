package com.projectv.dream;

import com.projectv.dream.DreamTypes.DreamFinishRequest;
import com.projectv.dream.DreamTypes.DreamFinishResult;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

/** Dream Short activities on the Python media worker (workers/dream/). Names match {@code @activity.defn(name=...)}. */
@ActivityInterface
public interface DreamMediaActivities {

    /** Laptop, free: smooth to 24 fps, soft dissolves, dream look, music, loudness; one MP4 saved to storage. */
    @ActivityMethod(name = "dream_finish")
    DreamFinishResult dreamFinish(DreamFinishRequest request);
}
