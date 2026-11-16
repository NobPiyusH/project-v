package com.projectv.workflow;

import com.projectv.media.MediaTypes.AnimaticRequest;
import com.projectv.media.MediaTypes.AnimaticResult;
import com.projectv.media.MediaTypes.KeyframeRequest;
import com.projectv.media.MediaTypes.KeyframeResult;
import com.projectv.media.MediaTypes.MusicRequest;
import com.projectv.media.MediaTypes.MusicResult;
import com.projectv.media.MediaTypes.NarrationRequest;
import com.projectv.media.MediaTypes.NarrationResult;
import com.projectv.media.MediaTypes.ResearchRequest;
import com.projectv.media.MediaTypes.ResearchResult;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

/**
 * Activities implemented by the Python media worker (workers/). Names must match the Python
 * {@code @activity.defn(name=...)} declarations exactly.
 */
@ActivityInterface
public interface MediaActivities {

    /** Reports the media tool versions available on the worker (FFmpeg etc.). */
    @ActivityMethod(name = "tool_versions")
    String toolVersions();

    @ActivityMethod(name = "research_topic")
    ResearchResult researchTopic(ResearchRequest request);

    @ActivityMethod(name = "synthesize_narration")
    NarrationResult synthesizeNarration(NarrationRequest request);

    @ActivityMethod(name = "generate_music")
    MusicResult generateMusic(MusicRequest request);

    @ActivityMethod(name = "generate_keyframes")
    KeyframeResult generateKeyframes(KeyframeRequest request);

    @ActivityMethod(name = "render_animatic")
    AnimaticResult renderAnimatic(AnimaticRequest request);

    /** M2b: one Wan clip per job on Modal, saved as each finishes; honours the owner's Stop. */
    @ActivityMethod(name = "generate_clips")
    com.projectv.media.MediaTypes.ClipResult generateClips(com.projectv.media.MediaTypes.ClipRequest request);

    /** Creative Director image review: a vision model judges every image against its line, paragraph and video. */
    @ActivityMethod(name = "review_images")
    com.projectv.media.MediaTypes.ImageReviewResult reviewImages(com.projectv.media.MediaTypes.ImageReviewRequest request);

    /** Inspector file checks (Whisper, FFmpeg, image statistics). Free to run; reports findings, changes nothing. */
    @ActivityMethod(name = "inspect_media")
    com.projectv.media.MediaTypes.MediaInspectResult inspectMedia(com.projectv.media.MediaTypes.MediaInspectRequest request);
}
