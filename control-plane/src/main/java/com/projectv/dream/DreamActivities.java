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

    /** The score request (Lyria): mood from the Short's world, length to cover it. Routed, logged, held to the Gemini cap. */
    com.projectv.media.MediaTypes.MusicRequest musicRequest(String dreamId, String notes);

    String saveMusic(String dreamId, com.projectv.media.MediaTypes.MusicResult result);

    /** The quality check's request, or null when it is switched off (DREAM_QUALITY_CHECK) or over the Gemini cap. */
    DreamTypes.DreamCheckRequest checkRequest(String dreamId);

    /** Stores the report and its cost; returns one line for the finish message. */
    String saveCheck(String dreamId, DreamTypes.DreamCheckResult result);

    /** The same check on the pictures, before any clip is paid for; null when switched off. */
    DreamTypes.DreamCheckRequest pictureCheckRequest(String dreamId);

    String savePictureCheck(String dreamId, DreamTypes.DreamCheckResult result);

    /** Whether this Short runs the Scout (the owner can switch it off on the form). */
    boolean scoutWanted(String dreamId);

    /** What to search for and how many Shorts to watch; held to the Gemini cap. */
    DreamTypes.DreamScoutRequest scoutRequest(String dreamId, String notes);

    /** Costs what was watched, has Claude write the brief (clichés, gaps, angles), stores it for the writer. */
    String saveScout(String dreamId, DreamTypes.DreamScoutRequest request, DreamTypes.DreamScoutResult result);

    /** "bed" (the owner's own track, free; the default) or "lyria" (paid), as chosen for this Short. */
    String musicSource(String dreamId);

    /** Which piece of the music bed to cut; fails clearly when no bed is set (DREAM_MUSIC_BED). */
    DreamTypes.DreamBedRequest musicBedRequest(String dreamId);

    String saveMusicBed(String dreamId, DreamTypes.DreamBedResult result);

    DreamFinishRequest finishRequest(String dreamId);

    String saveFinish(String dreamId, DreamFinishResult result);

    /** Status shown on the dashboard. */
    void status(String dreamId, String step, String status, String message);

    /** The owner's Stop for clips in flight (the media worker polls it). Cleared when a new batch starts. */
    void startClipBatch(String dreamId);

    /**
     * Batch order on the shared GPU pod (21 Nov 2026): how many other Shorts should use the pod before this one starts
     * {@code step}. CLIPS waits for other Shorts still writing or making pictures (so Z-Image and Wan are not swapped on
     * every job); PICTURES waits, while no pod is on yet, for other Shorts still on their free steps (rent once, later).
     * 0 = go now, and always 0 off the broker route.
     */
    default int gpuBatchAhead(String dreamId, String step) {
        return 0;
    }
}
