package com.projectv.dream;

import com.projectv.workflow.StageMode;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

/** Small, serialisable types for Dream Shorts, passed through Temporal (IDs and text only, never media bytes). */
public final class DreamTypes {

    private DreamTypes() {
    }

    /** The four steps of a Dream Short, in order. */
    public enum DreamStep { WRITE, PICTURES, CLIPS, FINISH }

    /** Workflow input. Steps not listed in {@code modes} use {@code defaultMode}. */
    public record DreamRequest(String dreamId, StageMode defaultMode, Map<DreamStep, StageMode> modes, int reviewMinutes) {
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
     * the look, add music from {@code musicDir} (if any), loudness to Shorts level, save one MP4.
     */
    public record DreamFinishRequest(String dreamId, String look, List<DreamFinishClip> clips, int width, int height,
                                     int fps, double dissolveSec, String musicDir, String progressUrl) {
    }

    /** @param smoothing "rife-laptop" | "ffmpeg" (how 16 fps became 24); {@code music} the track used, or null */
    public record DreamFinishResult(String uri, double durationSec, int width, int height, int fps, String smoothing,
                                    String music, String error) {
    }

    /** What the workflow shows the dashboard (Temporal query). */
    public record DreamView(String step, String status, String message, Map<String, String> modes, String deadline) {
    }
}
