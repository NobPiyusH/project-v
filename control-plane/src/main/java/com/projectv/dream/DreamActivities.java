package com.projectv.dream;

import com.projectv.dream.DreamTypes.DreamEstimate;
import com.projectv.dream.DreamTypes.DreamFinishRequest;
import com.projectv.dream.DreamTypes.DreamFinishResult;
import com.projectv.media.MediaTypes.ClipRequest;
import com.projectv.media.MediaTypes.ClipResult;
import com.projectv.media.MediaTypes.KeyframeRequest;
import com.projectv.media.MediaTypes.KeyframeResult;
import io.temporal.activity.ActivityInterface;

import java.util.List;

/**
 * Control-plane activities of a Dream Short (Java worker, CONTROL queue): all database, LLM, router and budget work.
 * Method names are prefixed {@code dream} so they never clash with the video pipeline's activity types.
 */
@ActivityInterface(namePrefix = "dream_")
public interface DreamActivities {

    /** The writer: one shared world and one scene + camera move per shot. Replaces the shots (notes = owner's redo notes). */
    String writeShots(String dreamId, String notes);

    /** Pictures for these shots (null = every shot without one). Routed and logged; refused over the GPU cap. */
    KeyframeRequest pictureRequest(String dreamId, List<String> shotIds, String notes);

    String savePictures(String dreamId, KeyframeRequest request, KeyframeResult result);

    /** What the clips would cost (shown before any GPU time is spent). */
    DreamEstimate clipEstimate(String dreamId, List<String> shotIds);

    /** Clips for these shots (null = every shot with a picture and no clip). Routed and logged; refused over the GPU cap. */
    ClipRequest clipRequest(String dreamId, List<String> shotIds);

    String saveClips(String dreamId, ClipRequest request, ClipResult result);

    DreamFinishRequest finishRequest(String dreamId);

    String saveFinish(String dreamId, DreamFinishResult result);

    /** Status shown on the dashboard. */
    void status(String dreamId, String step, String status, String message);

    /** The owner's Stop for clips in flight (the media worker polls it). Cleared when a new batch starts. */
    void startClipBatch(String dreamId);
}
