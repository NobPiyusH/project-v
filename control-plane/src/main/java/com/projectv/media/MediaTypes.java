package com.projectv.media;

import java.util.List;

/**
 * Requests/results exchanged with the Python media worker. Field names are the JSON contract with
 * workers/activities/*.py (camelCase dict keys) and must stay in sync. Media travels as s3:// URIs, never bytes.
 */
public final class MediaTypes {

    private MediaTypes() {
    }

    public record Usage(long promptTokens, long outputTokens, long thoughtTokens, long totalTokens) {
        public static final Usage NONE = new Usage(0, 0, 0, 0);
    }

    // ---- research
    public record Source(String title, String uri) {
    }

    public record Fact(String text, List<Source> sources) {
    }

    public record ResearchRequest(String topic, String summary, List<String> mustInclude, String language, int maxFacts,
                                  String model, String ownerNotes) {
    }

    public record ResearchResult(List<Fact> facts, List<Source> sources, List<String> queries, String model, Usage usage) {
    }

    // ---- narration
    public record NarrationLine(int index, String text) {
    }

    public record NarrationRequest(String videoId, String model, String checkModel, String voiceName, String style,
                                   String language, List<NarrationLine> lines) {
    }

    /** takes: how many generations were needed; passed: whether the final take passed the transcript check */
    public record NarrationClip(int index, String uri, double durationSec, Usage usage, int takes, boolean passed,
                                String check) {
    }

    public record NarrationResult(List<NarrationClip> lines, String model, String voiceName) {
    }

    // ---- music
    public record MusicRequest(String videoId, String model, String prompt, double targetSec, int maxSegments) {
    }

    public record MusicResult(String uri, double durationSec, double bpm, List<Double> beatTimes,
                              List<Double> downbeatTimes, int segments, String model, List<Usage> usage) {
    }

    // ---- keyframes
    public record KeyframeJob(String shotId, int attempt, String provider, String prompt, int width, int height,
                              String aspectRatio, String imageSize, long seed) {
    }

    public record KeyframeRequest(String videoId, String geminiImageModel, String modalApp, String modalClass,
                                  List<KeyframeJob> jobs) {
    }

    public record KeyframeOutput(String shotId, String uri, String provider, String model, Long seed, Double seconds,
                                 Usage usage, String error) {
    }

    public record KeyframeResult(List<KeyframeOutput> results, double modalGpuSeconds) {
    }

    // ---- animatic
    public record AnimaticShot(int index, double startSec, double durationSec, String imageUri, String overlayText) {
    }

    /** @param tempo voice speed factor (1.0 = as recorded) */
    public record AnimaticLine(String uri, double startSec, double tempo) {
    }

    public record AnimaticRequest(String videoId, String title, List<AnimaticShot> shots, List<AnimaticLine> narration,
                                  String musicUri, double totalSec, String aspectRatio) {
    }

    public record AnimaticResult(String uri, double durationSec) {
    }

    // ---- Inspector (second-level checks)

    /** @param severity FAIL (must not pass unseen), WARN (shown, does not block) or INFO */
    public record Finding(String severity, String code, String message, String ref) {
    }

    public record InspectLine(int index, String text, String uri) {
    }

    public record InspectImage(String ref, String uri) {
    }

    public record MediaInspectRequest(String videoId, String stage, String aspectRatio, boolean isShort, double targetSec,
                                      List<InspectLine> lines, String musicUri, List<InspectImage> images,
                                      String videoUri, Double speechEndSec) {
    }

    public record MediaInspectResult(List<Finding> findings, List<String> checked) {
        public static final MediaInspectResult NONE = new MediaInspectResult(List.of(), List.of());
    }
}
