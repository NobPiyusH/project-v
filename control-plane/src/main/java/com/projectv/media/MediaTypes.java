package com.projectv.media;

import java.util.List;
import java.util.Map;

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

    /** @param check source check: confirmed | unconfirmed | unreachable | no-numbers | null (not checked) */
    public record Fact(String text, List<Source> sources, String check) {
        public Fact(String text, List<Source> sources) {
            this(text, sources, null);
        }
    }

    public record ResearchRequest(String topic, String summary, List<String> mustInclude, String language, int maxFacts,
                                  String model, String ownerNotes) {
    }

    public record ResearchResult(List<Fact> facts, List<Source> sources, List<String> queries, String model, Usage usage) {
    }

    // ---- narration
    /** @param delivery how to read this line (optional): passed to the TTS director's notes */
    public record NarrationLine(int index, String text, String delivery) {
        public NarrationLine(int index, String text) {
            this(index, text, null);
        }
    }

    /** @param voiceProvider auto (ElevenLabs when its credit covers the whole video, else Gemini), elevenlabs or gemini */
    public record NarrationRequest(String videoId, String model, String checkModel, String voiceName, String style,
                                   String language, List<NarrationLine> lines, String voiceProvider) {
        public NarrationRequest(String videoId, String model, String checkModel, String voiceName, String style,
                                String language, List<NarrationLine> lines) {
            this(videoId, model, checkModel, voiceName, style, language, lines, "auto");
        }
    }

    /** takes: how many generations were needed; passed: whether the final take passed the transcript check */
    public record NarrationClip(int index, String uri, double durationSec, Usage usage, int takes, boolean passed,
                                String check) {
    }

    /** @param characters ElevenLabs credits used (0 for Gemini) */
    public record NarrationResult(List<NarrationClip> lines, String model, String voiceName, long characters) {
        public NarrationResult(List<NarrationClip> lines, String model, String voiceName) {
            this(lines, model, voiceName, 0);
        }

        public boolean elevenLabs() {
            return model != null && model.startsWith("elevenlabs:");
        }

        /** Chatterbox on the laptop GPU: the channel's main voice (free, MIT). */
        public boolean chatterbox() {
            return model != null && model.startsWith("chatterbox:");
        }

        /** Kokoro on the laptop: the free last-resort voice. */
        public boolean kokoro() {
            return model != null && model.startsWith("kokoro:");
        }
    }

    // ---- music
    public record MusicRequest(String videoId, String model, String prompt, double targetSec, int maxSegments) {
    }

    public record MusicResult(String uri, double durationSec, double bpm, List<Double> beatTimes,
                              List<Double> downbeatTimes, int segments, String model, List<Usage> usage) {
    }

    // ---- keyframes
    /** @param referenceQuery subject to fetch real reference photos for (Gemini only; empty = none) */
    public record KeyframeJob(String shotId, int attempt, String provider, String prompt, int width, int height,
                              String aspectRatio, String imageSize, String referenceQuery, long seed) {
    }

    public record KeyframeRequest(String videoId, String geminiImageModel, String modalApp, String modalClass,
                                  List<KeyframeJob> jobs) {
    }

    /** @param references credit lines of the real reference photos the image model was given (rule 6) */
    public record KeyframeOutput(String shotId, String uri, String provider, String model, Long seed, Double seconds,
                                 Usage usage, String error, List<String> references) {
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
    /** @param targetId the shot id a FAIL can be fixed on alone (targeted redo), or null */
    public record Finding(String severity, String code, String message, String ref, String targetId) {
        public Finding(String severity, String code, String message, String ref) {
            this(severity, code, message, ref, null);
        }
    }

    public record InspectLine(int index, String text, String uri) {
    }

    public record InspectImage(String ref, String uri) {
    }

    public record MediaInspectRequest(String videoId, String stage, String aspectRatio, boolean isShort, double targetSec,
                                      List<InspectLine> lines, String musicUri, List<InspectImage> images,
                                      String videoUri, Double speechEndSec) {
    }

    // ---- Creative Director image review (vision model on the media worker)

    public record ReviewLine(int index, String act, String text, double startSec) {
    }

    public record ReviewShot(int n, String shotId, Integer lineIndex, String act, double startSec, double durationSec,
                             String description, String keyframePrompt, String priority, String imageUri) {
    }

    /** @param rubric the Creative Director's scoring rules (one copy, in CreativeDirectorAgent) */
    public record ImageReviewRequest(String videoId, String model, String rubric, String title, String aspectRatio,
                                     String visualStyle, String logline, String bigIdea, String emotionalArc,
                                     String musicBrief, List<ReviewLine> lines, List<ReviewShot> shots) {
    }

    public record ImageReviewResult(com.projectv.agents.AgentTypes.CreativeReview review, String model, Usage usage,
                                    int imagesSent) {
    }

    public record MediaInspectResult(List<Finding> findings, List<String> checked) {
        public static final MediaInspectResult NONE = new MediaInspectResult(List.of(), List.of());
    }

    // ---- M2b: moving video per shot (Wan 2.2 image-to-video on Modal)

    /**
     * @param kind      draft (fast mode, 480p) or final (hybrid mode, 720p)
     * @param mode      Wan quality mode: fast | hybrid | standard
     * @param numFrames 4n+1 frames at 16 fps (81 = ~5 s, Wan's limit)
     * @param negative  extra Wan negative-prompt terms (the Producer's stop-rule fix for a recurring cause), or null
     */
    public record ClipJob(String shotId, int shotNumber, int take, String kind, String mode, String imageUri, String prompt,
                          int width, int height, int numFrames, long seed, String negative) {
        public ClipJob(String shotId, int shotNumber, int take, String kind, String mode, String imageUri, String prompt,
                       int width, int height, int numFrames, long seed) {
            this(shotId, shotNumber, take, kind, mode, imageUri, prompt, width, height, numFrames, seed, null);
        }
    }

    /** @param estimateInr GPU estimate for the whole batch incl. container start-up (shown to the owner first) */
    /** @param containers GPU containers to use: 1 = render in turn on one GPU (one start-up), else up to 4 in parallel */
    /** @param holdSeconds keep the one GPU warm this long after the batch (the AI Producer may redo by itself), 0 = no */
    public record ClipRequest(String videoId, String modalApp, String modalClass, String progressUrl, List<ClipJob> jobs,
                              int containers, double estimateGpuSeconds, java.math.BigDecimal estimateInr,
                              String provider, String resolution, String omniModel, int holdSeconds) {
        public ClipRequest(String videoId, String modalApp, String modalClass, String progressUrl, List<ClipJob> jobs,
                           int containers, double estimateGpuSeconds, java.math.BigDecimal estimateInr,
                           String provider, String resolution, String omniModel) {
            this(videoId, modalApp, modalClass, progressUrl, jobs, containers, estimateGpuSeconds, estimateInr, provider,
                    resolution, omniModel, 0);
        }
    }

    /** One finished (or failed / stopped) clip. {@code gpuSeconds} is render time on the GPU. */
    public record ClipOutput(String shotId, int take, String kind, String uri, double gpuSeconds, int frames, int fps,
                             String mode, String error, long outputTokens) {
    }

    /** @param containers GPU containers the batch started (each billed its start-up + model load once) */
    public record ClipResult(List<ClipOutput> clips, int containers, boolean stopped) {
    }

    /** What a batch would cost, for the owner's go-ahead before any GPU time is spent. */
    public record ClipEstimate(String kind, int clips, double gpuSeconds, java.math.BigDecimal inr, double usd,
                               double wallMinutes, boolean needsApproval, String summary) {
    }

    // ---- Free real footage (NASA Image and Video Library)

    public record FootageQuery(String shotId, String query) {
    }

    public record FootageCandidate(String nasaId, String title, String description, String credit, String date,
                                   String thumbUrl, String previewUrl) {
    }

    public record FootageSearchRequest(List<FootageQuery> queries) {
    }

    public record FootageSearchResult(String shotId, String query, List<FootageCandidate> candidates, String error) {
    }

    public record FootageSearchResponse(List<FootageSearchResult> results) {
    }

    /** @param startSec null = automatic (skips NASA's opening title card) */
    public record FootageFetchRequest(String videoId, String shotId, int shotNumber, String nasaId, Double startSec,
                                      double durationSec, String aspectRatio) {
    }

    /** @param upscale how much the cropped source had to be enlarged to fill the frame (1.0 = native, >1.5 = soft) */
    public record FootageFetchResult(String uri, int width, int height, double durationSec, int sourceWidth,
                                     int sourceHeight, Double upscale, double startSec, String sourceUrl) {
    }

    // ---- M2c part 1: assembly (the edit)

    /** @param kind footage | final | draft (which clip the shot uses) */
    /** @param sfxQuery a sound for this shot on Freesound (e.g. "underwater ambience"), or null */
    public record AssemblyShot(int n, String uri, String kind, double startSec, double durationSec, String overlayText,
                               String sfxQuery) {
        public AssemblyShot(int n, String uri, String kind, double startSec, double durationSec, String overlayText) {
            this(n, uri, kind, startSec, durationSec, overlayText, null);
        }
    }

    public record AssemblyLine(String uri, double startSec, double tempo, String text, double durationSec) {
    }

    public record AssemblyRequest(String videoId, String title, String aspectRatio, String language, boolean captions,
                                  List<AssemblyShot> shots, List<AssemblyLine> narration, String musicUri, double totalSec) {
    }

    /** @param fits per shot: which clip, its length, how it was fitted (trimmed / slowed) */
    public record AssemblyResult(String masterUri, String packageUri, String srtUri, double durationSec, int width,
                                 int height, int fps, List<Map<String, Object>> fits, int captionChunks,
                                 int linesTimedByWhisper, List<String> sfxCredits) {
        public AssemblyResult(String masterUri, String packageUri, String srtUri, double durationSec, int width,
                              int height, int fps, List<Map<String, Object>> fits, int captionChunks,
                              int linesTimedByWhisper) {
            this(masterUri, packageUri, srtUri, durationSec, width, height, fps, fits, captionChunks, linesTimedByWhisper, null);
        }
    }

    // ---- M2c part 2a: smooth motion (RIFE 16 -> 24 fps on Modal)

    public record SmoothJob(String shotId, int shotNumber, String sourceAssetId, String uri, double srcFps) {
    }

    public record SmoothRequest(String videoId, String progressUrl, double dstFps, List<SmoothJob> jobs,
                                java.math.BigDecimal estimateInr) {
    }

    public record SmoothOutput(String shotId, String sourceAssetId, String uri, double gpuSeconds, int frames, double fps,
                               String error) {
    }

    public record SmoothResult(List<SmoothOutput> clips, int containers, boolean stopped) {
    }

    // ---- AI Producer (P1, owner 18 Nov 2026): it reads, looks at, watches and LISTENS to a stage's output

    /** One thing to judge: kind = text | image | video | audio; uri for media, text for text; expect = what it should be. */
    public record ProducerItem(String id, String label, String kind, String uri, String text, String expect) {
    }

    /** @param rules  the owner's learned rules and recent feedback, applied to every judgement */
    public record ProducerReviewRequest(String videoId, String stage, String model, String rubric, String context,
                                        List<String> rules, List<ProducerItem> items) {
    }

    public record ProducerItemVerdict(String id, int score, boolean ok, String problem, String fix) {
    }

    /** verdict approve | redo | ask; redoNotes = precise instructions for a redo (prompts, delivery). */
    public record ProducerReviewResult(String verdict, double confidence, String summary, String redoNotes,
                                       List<ProducerItemVerdict> items, String model, Usage usage) {
    }

    /** What the workflow shows the owner (advise-only in P1). */
    public record ProducerAdvice(String verdict, double confidence, String summary, String redoNotes, String decisionId,
                                 List<String> weakItems) {
        public ProducerAdvice(String verdict, double confidence, String summary, String redoNotes, String decisionId) {
            this(verdict, confidence, summary, redoNotes, decisionId, List.of());
        }
    }

    /** P2: the Producer's call on a money step. */
    public record SpendDecision(boolean approve, String text) {
    }
}
