package com.projectv.workflow;

import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
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
import com.projectv.media.MediaTypes.Usage;
import com.projectv.planner.Planner;
import com.projectv.production.ProductionService;
import com.projectv.workflow.WorkflowTypes.StageResult;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/** Spring-managed implementation of the control activities. Must be idempotent: Temporal may retry any call. */
@Component
public class VideoActivitiesImpl implements VideoActivities {

    private final JdbcClient jdbc;
    private final Planner planner;
    private final CostLedger ledger;
    private final ObjectMapper json;
    private final ProductionService production;
    private final Pricing pricing;
    private final com.projectv.production.Inspector inspector;
    private final com.projectv.production.CreativeDirector creative;
    private final com.projectv.production.VideoStage video;
    private final com.projectv.video.ClipProgress clipProgress;
    private final com.projectv.production.FootageStage footage;
    private final com.projectv.production.AssemblyStage assembly;
    private final com.projectv.production.FinishStage finish;
    private final com.projectv.production.SharpenStage sharpen;
    private final com.projectv.agents.ClaudeResearcher researcher;

    public VideoActivitiesImpl(JdbcClient jdbc, Planner planner, CostLedger ledger, ObjectMapper json,
                               ProductionService production, Pricing pricing,
                               com.projectv.production.Inspector inspector,
                               com.projectv.production.CreativeDirector creative,
                               com.projectv.production.VideoStage video, com.projectv.video.ClipProgress clipProgress,
                               com.projectv.production.FootageStage footage,
                               com.projectv.production.AssemblyStage assembly,
                               com.projectv.production.FinishStage finish,
                               com.projectv.production.SharpenStage sharpen,
                               com.projectv.agents.ClaudeResearcher researcher) {
        this.jdbc = jdbc;
        this.planner = planner;
        this.ledger = ledger;
        this.json = json;
        this.production = production;
        this.pricing = pricing;
        this.inspector = inspector;
        this.creative = creative;
        this.video = video;
        this.clipProgress = clipProgress;
        this.footage = footage;
        this.assembly = assembly;
        this.finish = finish;
        this.sharpen = sharpen;
        this.researcher = researcher;
    }

    @Override
    public com.projectv.media.MediaTypes.ClipEstimate smoothEstimate(String videoId) {
        return finish.estimate(UUID.fromString(videoId));
    }

    @Override
    public com.projectv.media.MediaTypes.SmoothRequest smoothRequest(String videoId) {
        return finish.request(UUID.fromString(videoId));
    }

    @Override
    public StageResult saveSmooth(String videoId, com.projectv.media.MediaTypes.SmoothRequest request,
                                  com.projectv.media.MediaTypes.SmoothResult result) {
        return finish.save(UUID.fromString(videoId), request, result);
    }

    @Override
    public com.projectv.media.MediaTypes.ClipEstimate sharpEstimate(String videoId) {
        return sharpen.estimate(UUID.fromString(videoId));
    }

    @Override
    public com.projectv.media.MediaTypes.SmoothRequest sharpRequest(String videoId) {
        return sharpen.request(UUID.fromString(videoId));
    }

    @Override
    public StageResult saveSharp(String videoId, com.projectv.media.MediaTypes.SmoothRequest request,
                                 com.projectv.media.MediaTypes.SmoothResult result) {
        return sharpen.save(UUID.fromString(videoId), request, result);
    }

    @Override
    public com.projectv.media.MediaTypes.AssemblyRequest assemblyRequest(String videoId) {
        return assembly.assemblyRequest(UUID.fromString(videoId));
    }

    @Override
    public StageResult saveMaster(String videoId, com.projectv.media.MediaTypes.AssemblyResult result) {
        return assembly.saveMaster(UUID.fromString(videoId), result);
    }

    @Override
    public com.projectv.media.MediaTypes.FootageSearchRequest footageSearchRequest(String videoId, java.util.List<String> shotIds,
                                                                                 String query) {
        return footage.searchRequest(UUID.fromString(videoId), ids(shotIds), query);
    }

    @Override
    public StageResult saveFootageCandidates(String videoId, com.projectv.media.MediaTypes.FootageSearchResponse result) {
        return footage.saveCandidates(UUID.fromString(videoId), result);
    }

    @Override
    public com.projectv.media.MediaTypes.FootageFetchRequest footageFetchRequest(String videoId, String shotId, String nasaId,
                                                                               Double startSec) {
        return footage.fetchRequest(UUID.fromString(videoId), UUID.fromString(shotId), nasaId, startSec);
    }

    @Override
    public StageResult saveFootage(String videoId, com.projectv.media.MediaTypes.FootageFetchRequest request,
                                   com.projectv.media.MediaTypes.FootageFetchResult result) {
        return footage.saveFootage(UUID.fromString(videoId), request, result);
    }

    private static java.util.List<UUID> ids(java.util.List<String> shotIds) {
        return shotIds == null ? null : shotIds.stream().map(UUID::fromString).toList();
    }

    @Override
    public com.projectv.media.MediaTypes.ClipEstimate clipEstimate(String videoId, String kind, java.util.List<String> shotIds) {
        return video.estimate(UUID.fromString(videoId), kind, ids(shotIds));
    }

    @Override
    public com.projectv.media.MediaTypes.ClipRequest clipRequest(String videoId, String kind, java.util.List<String> shotIds) {
        var request = video.clipRequest(UUID.fromString(videoId), kind, ids(shotIds));
        clipProgress.startBatch(UUID.fromString(videoId));
        video.markClips(UUID.fromString(videoId), request.jobs().stream().map(j -> UUID.fromString(j.shotId())).toList(),
                "RENDERING", null);
        return request;
    }

    @Override
    public StageResult saveClips(String videoId, com.projectv.media.MediaTypes.ClipRequest request,
                                 com.projectv.media.MediaTypes.ClipResult result) {
        return video.saveClips(UUID.fromString(videoId), request, result);
    }

    @Override
    public void markClips(String videoId, java.util.List<String> shotIds, String status, String error) {
        video.markClips(UUID.fromString(videoId), ids(shotIds), status, error);
    }

    @Override
    public java.util.List<com.projectv.media.MediaTypes.Finding> creativeShotPlanReview(String videoId, int attempt) {
        return creative.reviewShotPlan(UUID.fromString(videoId), attempt);
    }

    @Override
    public com.projectv.media.MediaTypes.ImageReviewRequest imageReviewRequest(String videoId) {
        return creative.imageReviewRequest(UUID.fromString(videoId));
    }

    @Override
    public java.util.List<com.projectv.media.MediaTypes.Finding> saveImageReview(String videoId, int attempt,
                                                                                 com.projectv.media.MediaTypes.ImageReviewResult result) {
        return creative.saveImageReview(UUID.fromString(videoId), attempt, result);
    }

    @Override
    public KeyframeRequest fixKeyframeRequest(String videoId, java.util.List<String> shotIds, String ownerNotes) {
        java.util.List<UUID> ids = shotIds.stream().map(UUID::fromString).toList();
        creative.applySuggestedPrompts(UUID.fromString(videoId), ids);
        return production.shotKeyframeRequest(UUID.fromString(videoId), ids, ownerNotes, ProductionService.ImagePolicy.AUTO);
    }

    @Override
    public com.projectv.media.MediaTypes.MediaInspectRequest inspectionRequest(String videoId, Stage stage) {
        return inspector.mediaRequest(UUID.fromString(videoId), stage);
    }

    @Override
    public WorkflowTypes.InspectionReport saveInspection(String videoId, Stage stage, int attempt,
                                                         com.projectv.media.MediaTypes.MediaInspectResult media) {
        return inspector.evaluate(UUID.fromString(videoId), stage, attempt, media);
    }

    @Override
    public StageResult plan(String videoId, String prompt, String ownerNotes, int attempt) {
        Planner.Outcome outcome = planner.plan(prompt, ownerNotes);
        Map<String, Object> artifact = new LinkedHashMap<>();
        artifact.put("plan", outcome.plan());
        artifact.put("stages", outcome.stages());
        artifact.put("source", outcome.source());
        if (outcome.fallbackReason() != null) {
            artifact.put("fallbackReason", outcome.fallbackReason());
        }
        String artifactJson = json.writeValueAsString(artifact);
        jdbc.sql("UPDATE video SET plan_json = CAST(:plan AS jsonb), title = :title, updated_at = now() WHERE id = :id")
                .param("plan", artifactJson)
                .param("title", outcome.plan().title())
                .param("id", UUID.fromString(videoId))
                .update();
        if (outcome.source().startsWith("gemini")) {
            // Planner only reports the total; price it all as output tokens (an upper bound).
            long tokens = outcome.totalTokens() == null ? 0 : outcome.totalTokens();
            ledger.record(UUID.fromString(videoId), BudgetGuard.GEMINI, pricing.textInr(new Usage(0, tokens, 0, tokens)),
                    BigDecimal.valueOf(tokens), "tokens", true, "PLAN attempt " + attempt);
        }
        var p = outcome.plan();
        String summary = "%s: %s, %ds, %s, preset %s, narration %s, music %s, research %s (planned by %s)".formatted(
                p.title(), p.format(), p.durationSec(), p.aspectRatio(), p.stylePreset(), p.narration(), p.music(),
                p.needsResearch() ? "yes" : "no", outcome.source());
        return new StageResult(summary, artifactJson, outcome.stages());
    }

    @Override
    public StageResult runStage(String videoId, Stage stage, String ownerNotes, int attempt) {
        // M1 placeholder: the backbone (modes, approvals, restarts) is real; the stage's actual work lands in M2+.
        Map<String, Object> artifact = new LinkedHashMap<>();
        artifact.put("placeholder", true);
        artifact.put("stage", stage.name());
        artifact.put("willDo", stage.description());
        if (ownerNotes != null && !ownerNotes.isBlank()) {
            artifact.put("ownerNotesApplied", ownerNotes);
        }
        String summary = "Placeholder (real work arrives in M2+): " + stage.description() + "."
                + (ownerNotes == null || ownerNotes.isBlank() ? "" : " Notes received: \"" + ownerNotes + "\".");
        return StageResult.of(summary, json.writeValueAsString(artifact));
    }

    // ---- M2a stages: thin adapters over ProductionService (all logic and persistence live there)

    @Override
    public ResearchRequest researchRequest(String videoId, String ownerNotes) {
        return production.researchRequest(UUID.fromString(videoId), ownerNotes);
    }

    @Override
    public ResearchResult claudeResearch(String videoId, ResearchRequest request) {
        return researcher.research(UUID.fromString(videoId), request);
    }

    @Override
    public StageResult saveResearch(String videoId, ResearchResult result) {
        return production.saveResearch(UUID.fromString(videoId), result);
    }

    @Override
    public StageResult concept(String videoId, String ownerNotes) {
        return production.concept(UUID.fromString(videoId), ownerNotes);
    }

    @Override
    public StageResult script(String videoId, String ownerNotes) {
        return production.script(UUID.fromString(videoId), ownerNotes);
    }

    @Override
    public MusicRequest musicRequest(String videoId, String ownerNotes) {
        return production.musicRequest(UUID.fromString(videoId), ownerNotes);
    }

    @Override
    public StageResult saveMusic(String videoId, MusicResult result) {
        return production.saveMusic(UUID.fromString(videoId), result);
    }

    @Override
    public NarrationRequest narrationRequest(String videoId) {
        return production.narrationRequest(UUID.fromString(videoId));
    }

    @Override
    public StageResult saveNarration(String videoId, NarrationResult result) {
        return production.saveNarration(UUID.fromString(videoId), result);
    }

    @Override
    public int lastAttempt(String videoId, Stage stage) {
        return jdbc.sql("SELECT COALESCE(max(attempt), 0) FROM stage_run WHERE video_id = :v AND stage = :s")
                .param("v", UUID.fromString(videoId)).param("s", stage.name()).query(Integer.class).single();
    }

    @Override
    public StageResult shotPlan(String videoId, String ownerNotes) {
        return production.shotPlan(UUID.fromString(videoId), ownerNotes);
    }

    @Override
    public KeyframeRequest keyframeRequest(String videoId, String ownerNotes) {
        return production.keyframeRequest(UUID.fromString(videoId), ownerNotes);
    }

    @Override
    public StageResult saveKeyframes(String videoId, KeyframeRequest request, KeyframeResult result) {
        return production.saveKeyframes(UUID.fromString(videoId), request, result);
    }

    @Override
    public KeyframeRequest shotKeyframeRequest(String videoId, java.util.List<String> shotIds, String ownerNotes, String policy) {
        return production.shotKeyframeRequest(UUID.fromString(videoId), shotIds.stream().map(UUID::fromString).toList(),
                ownerNotes, ProductionService.ImagePolicy.valueOf(policy == null ? "AUTO" : policy));
    }

    @Override
    public void markShots(String videoId, java.util.List<String> shotIds, String status, String error) {
        production.markShots(UUID.fromString(videoId), shotIds.stream().map(UUID::fromString).toList(), status, error);
    }

    @Override
    public NarrationRequest lineNarrationRequest(String videoId, java.util.List<Integer> lineIndexes, String styleNotes) {
        return production.lineNarrationRequest(UUID.fromString(videoId), lineIndexes, styleNotes);
    }

    @Override
    public StageResult saveLineNarration(String videoId, NarrationResult result, String style) {
        return production.saveLineNarration(UUID.fromString(videoId), result, style);
    }

    @Override
    public void markLines(String videoId, java.util.List<Integer> lineIndexes, String status, String why) {
        production.markLines(UUID.fromString(videoId), lineIndexes, status, why);
    }

    @Override
    public AnimaticRequest animaticRequest(String videoId) {
        return production.animaticRequest(UUID.fromString(videoId));
    }

    @Override
    public StageResult saveAnimatic(String videoId, AnimaticResult result) {
        return production.saveAnimatic(UUID.fromString(videoId), result);
    }

    @Override
    public void recordStage(String videoId, Stage stage, int attempt, StageMode mode, String status, String summary,
                            String artifactJson, String ownerNotes) {
        UUID id = UUID.fromString(videoId);
        boolean done = !status.equals("RUNNING") && !status.equals("WAITING_APPROVAL");
        jdbc.sql("""
                        INSERT INTO stage_run (id, video_id, stage, attempt, mode, status, summary, artifact_json, owner_notes, finished_at)
                        VALUES (:rid, :vid, :stage, :attempt, :mode, :status, :summary, CAST(:artifact AS jsonb), :notes,
                                CASE WHEN :done THEN now() END)
                        ON CONFLICT (video_id, stage, attempt) DO UPDATE SET
                            mode = EXCLUDED.mode, status = EXCLUDED.status,
                            summary = COALESCE(EXCLUDED.summary, stage_run.summary),
                            artifact_json = COALESCE(EXCLUDED.artifact_json, stage_run.artifact_json),
                            owner_notes = COALESCE(EXCLUDED.owner_notes, stage_run.owner_notes),
                            finished_at = EXCLUDED.finished_at,
                            review_deadline = CASE WHEN EXCLUDED.status = 'WAITING_APPROVAL' THEN stage_run.review_deadline END
                        """)
                .param("rid", UUID.randomUUID())
                .param("vid", id)
                .param("stage", stage.name())
                .param("attempt", attempt)
                .param("mode", mode.name())
                .param("status", status)
                .param("summary", summary)
                .param("artifact", artifactJson)
                .param("notes", ownerNotes)
                .param("done", done)
                .update();
        if (status.equals("SKIPPED")) {
            return;
        }
        String videoStatus = status.equals("WAITING_APPROVAL") ? "WAITING" : "RUNNING";
        jdbc.sql("UPDATE video SET status = :status, current_stage = :stage, updated_at = now() WHERE id = :id AND status NOT IN ('COMPLETED','CANCELLED')")
                .param("status", videoStatus)
                .param("stage", stage.name())
                .param("id", id)
                .update();
    }

    @Override
    public void setReviewDeadline(String videoId, Stage stage, int attempt, String deadlineIso) {
        jdbc.sql("UPDATE stage_run SET review_deadline = CAST(:d AS timestamptz) WHERE video_id = :v AND stage = :s AND attempt = :a")
                .param("d", deadlineIso).param("v", UUID.fromString(videoId)).param("s", stage.name()).param("a", attempt)
                .update();
    }

    @Override
    public void setVideoStatus(String videoId, String status) {
        jdbc.sql("""
                        UPDATE video SET status = :status,
                            current_stage = CASE WHEN :status = 'COMPLETED' THEN NULL ELSE current_stage END,
                            updated_at = now()
                        WHERE id = :id
                        """)
                .param("status", status)
                .param("id", UUID.fromString(videoId))
                .update();
    }
}
