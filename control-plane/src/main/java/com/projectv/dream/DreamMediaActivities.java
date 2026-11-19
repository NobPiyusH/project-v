package com.projectv.dream;

import com.projectv.dream.DreamTypes.DreamBedRequest;
import com.projectv.dream.DreamTypes.DreamBedResult;
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

    /** Laptop, free: cut a fitting piece of the owner's music bed (soft fades), saved to storage as the Short's music. */
    @ActivityMethod(name = "dream_music_bed")
    DreamBedResult dreamMusicBed(DreamBedRequest request);

    /** Find Shorts like the idea (YouTube, else Google Search) and have Gemini watch each one. A few rupees. */
    /** Gemini watches the finished Short against the owner's checklist; never throws for a model problem. */
    @ActivityMethod(name = "dream_check")
    DreamTypes.DreamCheckResult dreamCheck(DreamTypes.DreamCheckRequest request);

    @ActivityMethod(name = "dream_scout")
    DreamTypes.DreamScoutResult dreamScout(DreamTypes.DreamScoutRequest request);
}
