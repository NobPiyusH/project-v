package com.projectv.dream;

import com.projectv.workflow.StageMode;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

/** Small, serialisable types for Dream Shorts, passed through Temporal (IDs and text only, never media bytes). */
public final class DreamTypes {

    private DreamTypes() {
    }

    /** The steps of a Dream Short, in order. */
    public enum DreamStep { WRITE, PICTURES, CLIPS, MUSIC, FINISH }

    /**
     * Workflow input. Steps not listed in {@code modes} use {@code defaultMode}. {@code fromStep}: start there and keep
     * the earlier steps' results (redo from a step after the Short is done); null = from the start.
     */
    public record DreamRequest(String dreamId, StageMode defaultMode, Map<DreamStep, StageMode> modes, int reviewMinutes,
                               DreamStep fromStep) {
        public DreamRequest(String dreamId, StageMode defaultMode, Map<DreamStep, StageMode> modes, int reviewMinutes) {
            this(dreamId, defaultMode, modes, reviewMinutes, null);
        }
    }

    /** One shot as the writer plans it: the scene (shared world added later) and how the camera moves. */
    public record PlannedDreamShot(String scene, String motion) {
    }

    /** The writer's output: one world shared by every shot, so the Short looks like one place. */
    public record DreamWorld(String title, String world, String palette, String light, List<PlannedDreamShot> shots) {
    }

    /** What a step would cost before anything is spent. {@code needsApproval}: over the auto-approve amount. */
    public record DreamEstimate(DreamStep step, BigDecimal inr, double minutes, boolean needsApproval, String summary) {
    }

    /** One clip for the finish, in order. {@code fps} is the clip's own frame rate (Wan: 16). */
    public record DreamFinishClip(String uri, double fps) {
    }

    /**
     * Finish job for the media worker: smooth to 24 fps (laptop RIFE, else FFmpeg), join with soft dissolves, apply
     * the look, add the Short's score ({@code musicUri}, Lyria) or else a track from {@code musicDir}, loudness to
     * Shorts level, save one MP4.
     */
    public record DreamFinishRequest(String dreamId, String look, List<DreamFinishClip> clips, int width, int height,
                                     int fps, double dissolveSec, String musicDir, String progressUrl, String musicUri,
                                     String musicName, double speed, String handheld) {
    }

    /** @param smoothing "rife-laptop" | "ffmpeg" (how 16 fps became 24); {@code music} the track used, or null */
    /** {@code metrics}: what the finished Short measures (motion, colour, shadows, cuts, loudness...), for the learning loop. */
    public record DreamFinishResult(String uri, double durationSec, int width, int height, int fps, String smoothing,
                                    String music, String error, Map<String, Double> metrics) {
    }

    /**
     * A piece of the owner's music bed for one Short: {@code targetSec} long, away from the starts other Shorts used
     * ({@code usedStartsSec}), never a quiet stretch, starting on a beat. {@code candidates} pieces are cut; when
     * {@code model} is set, Gemini listens to them next to the Short's pictures and {@code brief} and keeps the best fit.
     */
    public record DreamBedRequest(String dreamId, String bedUri, double targetSec, String seed, List<Double> usedStartsSec,
                                  String model, String brief, List<String> pictureUris, int candidates) {
    }

    /** {@code model}/{@code usage} are null when nothing listened (one candidate, no model, or the call failed). */
    public record DreamBedResult(String uri, double durationSec, double startSec, double bedDurationSec, String bedName,
                                 String model, com.projectv.media.MediaTypes.Usage usage, String why, int heard) {
    }

    /** What the workflow shows the dashboard (Temporal query). */
    public record DreamView(String step, String status, String message, Map<String, String> modes, String deadline) {
    }
}
