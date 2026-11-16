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

    AnimaticRequest animaticRequest(String videoId);

    StageResult saveAnimatic(String videoId, AnimaticResult result);

    /** Upsert the stage_run row and the video's status / current stage. */
    void recordStage(String videoId, Stage stage, int attempt, StageMode mode, String status,
                     String summary, String artifactJson, String ownerNotes);

    void setVideoStatus(String videoId, String status);

    /** When a REVIEW-mode stage will continue on its own (ISO-8601 instant), or null once it is held/decided. */
    void setReviewDeadline(String videoId, Stage stage, int attempt, String deadlineIso);
}
