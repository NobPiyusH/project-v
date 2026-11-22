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
import com.projectv.workflow.WorkflowTypes.StageResult;
import io.temporal.activity.ActivityInterface;

/** Control-plane activities (Java). All database and LLM I/O happens here, never in workflow code. */
@ActivityInterface
public interface VideoActivities {

    /** PLAN stage: prompt (+ owner notes from a rejected attempt) -> VideoPlan, stored on the video. */
    StageResult plan(String videoId, String prompt, String ownerNotes, int attempt);

    /** Stages not built yet (M2b+): records what the stage will do. */
    StageResult runStage(String videoId, Stage stage, String ownerNotes, int attempt);

    // ---- M2a: research -> concept -> script -> music -> (narration + shot plan) -> keyframes -> animatic
    ResearchRequest researchRequest(String videoId, String ownerNotes);

    StageResult saveResearch(String videoId, ResearchResult result);

    /** Backup researcher (Claude + web search), used only when the Gemini research activity failed. */
    ResearchResult claudeResearch(String videoId, ResearchRequest request);

    StageResult concept(String videoId, String ownerNotes);

    StageResult script(String videoId, String ownerNotes);

    MusicRequest musicRequest(String videoId, String ownerNotes);

    StageResult saveMusic(String videoId, MusicResult result);

    NarrationRequest narrationRequest(String videoId);

    StageResult saveNarration(String videoId, NarrationResult result);

    /** Highest attempt recorded for a stage (0 if none). Used by re-runs so attempt numbers keep increasing. */
    int lastAttempt(String videoId, Stage stage);

    StageResult shotPlan(String videoId, String ownerNotes);

    KeyframeRequest keyframeRequest(String videoId, String ownerNotes);

    StageResult saveKeyframes(String videoId, KeyframeRequest request, KeyframeResult result);

    // ---- per-shot redo
    KeyframeRequest shotKeyframeRequest(String videoId, java.util.List<String> shotIds, String ownerNotes, String policy);

    void markShots(String videoId, java.util.List<String> shotIds, String status, String error);

    // ---- per-line voice redo
    NarrationRequest lineNarrationRequest(String videoId, java.util.List<Integer> lineIndexes, String styleNotes);

    StageResult saveLineNarration(String videoId, NarrationResult result, String style);

    void markLines(String videoId, java.util.List<Integer> lineIndexes, String status, String why);

    AnimaticRequest animaticRequest(String videoId);

    StageResult saveAnimatic(String videoId, AnimaticResult result);

    // ---- Inspector (second-level checks)

    /** File checks for the media worker, or null when the stage has none. */
    com.projectv.media.MediaTypes.MediaInspectRequest inspectionRequest(String videoId, Stage stage);

    /** Structural checks + the worker's file findings -> stored report and verdict. */
    WorkflowTypes.InspectionReport saveInspection(String videoId, Stage stage, int attempt,
                                                  com.projectv.media.MediaTypes.MediaInspectResult media);

    // ---- Creative Director (story review: shot, paragraph, whole video)

    /** Text review of the shot plan; rewrites weak shot prompts in place. Paid LLM call. */
    java.util.List<com.projectv.media.MediaTypes.Finding> creativeShotPlanReview(String videoId, int attempt);

    /** Images + their lines/paragraphs for the vision review on the media worker. Budget-checked. */
    com.projectv.media.MediaTypes.ImageReviewRequest imageReviewRequest(String videoId);

    java.util.List<com.projectv.media.MediaTypes.Finding> saveImageReview(String videoId, int attempt,
                                                                          com.projectv.media.MediaTypes.ImageReviewResult result);

    /** Targeted redo of weak images: applies the Creative Director's suggested prompts, then builds the request. */
    KeyframeRequest fixKeyframeRequest(String videoId, java.util.List<String> shotIds, String ownerNotes);

    // ---- M2b: moving video per shot (Wan on Modal)

    /** What a batch of clips would cost and take; whether the owner must approve it first. */
    com.projectv.media.MediaTypes.ClipEstimate clipEstimate(String videoId, String kind, java.util.List<String> shotIds);

    /** The batch for the media worker (null shotIds = every shot that needs one). Refused over the GPU cap. */
    com.projectv.media.MediaTypes.ClipRequest clipRequest(String videoId, String kind, java.util.List<String> shotIds);

    StageResult saveClips(String videoId, com.projectv.media.MediaTypes.ClipRequest request,
                          com.projectv.media.MediaTypes.ClipResult result);

    void markClips(String videoId, java.util.List<String> shotIds, String status, String error);

    // ---- Stock footage lane (off by default)

    java.util.List<com.projectv.media.MediaTypes.StockRequest> stockRequests(String videoId);

    StageResult saveStock(String videoId, com.projectv.media.MediaTypes.StockRequest request,
                          com.projectv.media.MediaTypes.StockResult result);

    // ---- Free real footage (NASA)

    com.projectv.media.MediaTypes.FootageSearchRequest footageSearchRequest(String videoId, java.util.List<String> shotIds,
                                                                          String query);

    StageResult saveFootageCandidates(String videoId, com.projectv.media.MediaTypes.FootageSearchResponse result);

    com.projectv.media.MediaTypes.FootageFetchRequest footageFetchRequest(String videoId, String shotId, String nasaId,
                                                                        Double startSec);

    StageResult saveFootage(String videoId, com.projectv.media.MediaTypes.FootageFetchRequest request,
                            com.projectv.media.MediaTypes.FootageFetchResult result);

    // ---- M2c part 1: assembly

    /** Fails (non-retryable for the owner to act on) when a shot has no moving video yet. */
    com.projectv.media.MediaTypes.AssemblyRequest assemblyRequest(String videoId);

    StageResult saveMaster(String videoId, com.projectv.media.MediaTypes.AssemblyResult result);

    // ---- M2c part 2a: smooth motion

    com.projectv.media.MediaTypes.ClipEstimate smoothEstimate(String videoId);

    com.projectv.media.MediaTypes.SmoothRequest smoothRequest(String videoId);

    StageResult saveSmooth(String videoId, com.projectv.media.MediaTypes.SmoothRequest request,
                           com.projectv.media.MediaTypes.SmoothResult result);

    // ---- M2c part 2b: sharpness (SeedVR2)

    com.projectv.media.MediaTypes.ClipEstimate sharpEstimate(String videoId);

    com.projectv.media.MediaTypes.SmoothRequest sharpRequest(String videoId);

    StageResult saveSharp(String videoId, com.projectv.media.MediaTypes.SmoothRequest request,
                          com.projectv.media.MediaTypes.SmoothResult result);

    /** Upsert the stage_run row and the video's status / current stage. */
    void recordStage(String videoId, Stage stage, int attempt, StageMode mode, String status,
                     String summary, String artifactJson, String ownerNotes);

    void setVideoStatus(String videoId, String status);

    /** When a REVIEW-mode stage will continue on its own (ISO-8601 instant), or null once it is held/decided. */
    void setReviewDeadline(String videoId, Stage stage, int attempt, String deadlineIso);
}
