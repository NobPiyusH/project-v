package com.projectv.workflow;

import com.projectv.ledger.CostLedger;
import com.projectv.planner.Planner;
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

    public VideoActivitiesImpl(JdbcClient jdbc, Planner planner, CostLedger ledger, ObjectMapper json) {
        this.jdbc = jdbc;
        this.planner = planner;
        this.ledger = ledger;
        this.json = json;
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
            // Free tier today; logged at ₹0 so usage is visible and pricing can be applied later.
            ledger.record(UUID.fromString(videoId), "gemini_api", BigDecimal.ZERO,
                    outcome.totalTokens() == null ? null : BigDecimal.valueOf(outcome.totalTokens()), "tokens", true,
                    "PLAN attempt " + attempt);
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
                            finished_at = EXCLUDED.finished_at
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
