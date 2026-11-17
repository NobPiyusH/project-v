package com.projectv.video;

import com.projectv.workflow.Stage;
import com.projectv.workflow.StageMode;
import com.projectv.workflow.TaskQueues;
import com.projectv.workflow.VideoProductionWorkflow;
import com.projectv.workflow.WorkflowTypes.VideoRequest;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.util.EnumMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.UUID;

/**
 * Owner-facing operations. The workflow is the source of truth for what happens next; the database mirrors it
 * for the dashboard. Mode changes and decisions are signalled to the workflow AND recorded here (approval and
 * preference_event rows) so taste learning can use them later.
 */
@Service
public class VideoService {

    /**
     * What must be redone together, so outputs never go stale: e.g. a new script needs a new voice, new shots,
     * new keyframes and a new animatic; a new voice only needs a new animatic.
     */
    public static final Map<Stage, List<Stage>> REDO_WITH = Map.ofEntries(
            Map.entry(Stage.RESEARCH, List.of(Stage.RESEARCH)),
            Map.entry(Stage.CONCEPT, List.of(Stage.CONCEPT)),
            Map.entry(Stage.SCRIPT, List.of(Stage.SCRIPT, Stage.NARRATION, Stage.SHOT_PLAN, Stage.KEYFRAMES, Stage.ANIMATIC)),
            Map.entry(Stage.MUSIC, List.of(Stage.MUSIC, Stage.ANIMATIC)),
            Map.entry(Stage.NARRATION, List.of(Stage.NARRATION, Stage.ANIMATIC)),
            Map.entry(Stage.SHOT_PLAN, List.of(Stage.SHOT_PLAN, Stage.KEYFRAMES, Stage.ANIMATIC)),
            Map.entry(Stage.KEYFRAMES, List.of(Stage.KEYFRAMES, Stage.ANIMATIC)),
            Map.entry(Stage.ANIMATIC, List.of(Stage.ANIMATIC)),
            // Clips cost GPU money: each runs alone, after showing its estimate (finals follow drafts on request).
            Map.entry(Stage.DRAFT_GEN, List.of(Stage.DRAFT_GEN)),
            Map.entry(Stage.FINAL_GEN, List.of(Stage.FINAL_GEN)),
            Map.entry(Stage.UPSCALE, List.of(Stage.UPSCALE, Stage.ASSEMBLY)),
            Map.entry(Stage.ASSEMBLY, List.of(Stage.ASSEMBLY)));

    private final JdbcClient jdbc;
    private final WorkflowClient temporal;
    private final ObjectMapper json;

    public VideoService(JdbcClient jdbc, WorkflowClient temporal, ObjectMapper json) {
        this.jdbc = jdbc;
        this.temporal = temporal;
        this.json = json;
    }

    public record CreateVideo(String prompt, StageMode defaultMode, Map<Stage, StageMode> modes, Integer reviewMinutes) {
    }

    /** Not transactional on purpose: the row must be committed before the workflow's first activity reads it. */
    public UUID create(CreateVideo req) {
        UUID id = UUID.randomUUID();
        String workflowId = "video-" + id;
        StageMode defaultMode = req.defaultMode() == null ? StageMode.AUTO : req.defaultMode();
        Map<Stage, StageMode> modes = req.modes() == null || req.modes().isEmpty() ? new EnumMap<>(Stage.class) : new EnumMap<>(req.modes());
        int reviewMinutes = req.reviewMinutes() == null || req.reviewMinutes() < 1 ? 10 : Math.min(req.reviewMinutes(), 24 * 60);
        jdbc.sql("""
                        INSERT INTO video (id, prompt, status, modes_json, workflow_id, review_minutes)
                        VALUES (:id, :prompt, 'CREATED', CAST(:modes AS jsonb), :wf, :review)
                        """)
                .param("review", reviewMinutes)
                .param("id", id)
                .param("prompt", req.prompt().strip())
                .param("modes", modesJson(defaultMode, modes))
                .param("wf", workflowId)
                .update();
        VideoProductionWorkflow wf = temporal.newWorkflowStub(VideoProductionWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId(workflowId).setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new VideoRequest(id.toString(), req.prompt().strip(), defaultMode, modes, null, reviewMinutes));
        return id;
    }

    /**
     * Redo one stage of a finished video, reusing everything else. Stages before the animatic also rebuild the
     * animatic so the owner can watch the change. The workflow id is reused (allowed once the previous run closed).
     */
    public List<Stage> rerun(UUID id, Stage stage) {
        Map<String, Object> v = jdbc.sql("SELECT prompt, status, modes_json::text AS modes, review_minutes FROM video WHERE id = :id")
                .param("id", id).query().listOfRows().stream().findFirst()
                .orElseThrow(() -> new NoSuchElementException("No video " + id));
        String status = (String) v.get("status");
        if (List.of("RUNNING", "WAITING", "PAUSED", "CREATED").contains(status)) {
            throw new IllegalStateException("The video is still in production (" + status + "); finish or let it complete first");
        }
        List<Stage> stages = REDO_WITH.get(stage);
        if (stages == null) {
            throw new IllegalStateException(stage.label() + " can't be redone yet (it isn't built until a later milestone)");
        }
        Map<String, Object> modes = json.readValue((String) v.get("modes"), new tools.jackson.core.type.TypeReference<>() {
        });
        StageMode defaultMode = StageMode.parse(String.valueOf(modes.getOrDefault("default", "AUTO")));
        VideoProductionWorkflow wf = temporal.newWorkflowStub(VideoProductionWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId("video-" + id).setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new VideoRequest(id.toString(), (String) v.get("prompt"), defaultMode,
                new EnumMap<>(Stage.class), stages, ((Number) v.get("review_minutes")).intValue()));
        jdbc.sql("UPDATE video SET status = 'RUNNING', current_stage = :s, updated_at = now() WHERE id = :id")
                .param("s", stage.name()).param("id", id).update();
        recordDecision(id, stage, "rerun", stages.toString());
        return stages;
    }

    /** Starts a per-shot redo; returns the redo's workflow id. Blocked only while the main run is rendering keyframes. */
    public String redoShots(UUID id, List<UUID> shotIds, String notes, String policy) {
        if (shotIds == null || shotIds.isEmpty()) {
            throw new IllegalStateException("Pick at least one shot");
        }
        Map<String, Object> v = jdbc.sql("SELECT status, current_stage FROM video WHERE id = :id").param("id", id)
                .query().listOfRows().stream().findFirst().orElseThrow(() -> new NoSuchElementException("No video " + id));
        if ("RUNNING".equals(v.get("status")) && List.of("KEYFRAMES", "ANIMATIC").contains(v.get("current_stage"))) {
            throw new IllegalStateException("Keyframes or the animatic are being made right now; wait until that finishes");
        }
        String p = policy == null ? "AUTO" : policy.toUpperCase();
        if (!List.of("AUTO", "PREMIUM", "FREE").contains(p)) {
            throw new IllegalStateException("policy must be AUTO, PREMIUM or FREE");
        }
        String workflowId = "shot-redo-" + id + "-" + UUID.randomUUID();
        com.projectv.workflow.ShotRedoWorkflow wf = temporal.newWorkflowStub(com.projectv.workflow.ShotRedoWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId(workflowId).setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new com.projectv.workflow.ShotRedoWorkflow.ShotRedoRequest(id.toString(),
                shotIds.stream().map(UUID::toString).toList(), notes, p));
        recordDecision(id, Stage.KEYFRAMES, "shot_redo", (notes == null ? "" : notes) + " [" + shotIds.size() + " shot(s), " + p + "]");
        return workflowId;
    }

    /** Redo the clips of the given shots (draft or final). Blocked while the main run is making clips. */
    public String redoClips(UUID id, List<UUID> shotIds, String kind) {
        if (shotIds == null || shotIds.isEmpty()) {
            throw new IllegalStateException("Pick at least one shot");
        }
        if (!List.of("draft", "final").contains(kind)) {
            throw new IllegalStateException("kind must be draft or final");
        }
        Map<String, Object> v = jdbc.sql("SELECT status, current_stage FROM video WHERE id = :id").param("id", id)
                .query().listOfRows().stream().findFirst().orElseThrow(() -> new NoSuchElementException("No video " + id));
        if ("RUNNING".equals(v.get("status")) && List.of("DRAFT_GEN", "FINAL_GEN").contains(v.get("current_stage"))) {
            throw new IllegalStateException("Clips are being made right now; wait until that batch finishes");
        }
        String workflowId = "clip-redo-" + id + "-" + UUID.randomUUID();
        com.projectv.workflow.ClipRedoWorkflow wf = temporal.newWorkflowStub(com.projectv.workflow.ClipRedoWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId(workflowId).setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new com.projectv.workflow.ClipRedoWorkflow.ClipRedoRequest(id.toString(),
                shotIds.stream().map(UUID::toString).toList(), kind));
        recordDecision(id, "draft".equals(kind) ? Stage.DRAFT_GEN : Stage.FINAL_GEN, "clip_redo", kind + " " + shotIds.size() + " shot(s)");
        return workflowId;
    }

    /** Real NASA footage for one shot: search (nasaId null) or use a picked clip. Free. */
    public String footage(UUID id, UUID shotId, String query, String nasaId, Double startSec) {
        String workflowId = "footage-" + id + "-" + UUID.randomUUID();
        com.projectv.workflow.FootageWorkflow wf = temporal.newWorkflowStub(com.projectv.workflow.FootageWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId(workflowId).setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new com.projectv.workflow.FootageWorkflow.FootageRequest(id.toString(),
                shotId.toString(), query, nasaId, startSec));
        return workflowId;
    }

    /** Creative Director review of the existing images (nothing is regenerated). */
    public String reviewImages(UUID id) {
        Integer images = jdbc.sql("""
                        SELECT count(*) FROM shot s JOIN scene sc ON sc.id = s.scene_id
                        WHERE sc.video_id = :id AND s.keyframe_asset_id IS NOT NULL
                        """).param("id", id).query(Integer.class).single();
        if (images == 0) {
            throw new IllegalStateException("This video has no images to review yet");
        }
        String workflowId = "creative-review-" + id + "-" + UUID.randomUUID();
        com.projectv.workflow.CreativeReviewWorkflow wf = temporal.newWorkflowStub(com.projectv.workflow.CreativeReviewWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId(workflowId).setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new com.projectv.workflow.CreativeReviewWorkflow.CreativeReviewRequest(id.toString()));
        return workflowId;
    }

    /** Re-voice only the given lines. Blocked while the main run is voicing or building the animatic. */
    public String redoLines(UUID id, List<Integer> lineIndexes, String styleNotes) {
        if (lineIndexes == null || lineIndexes.isEmpty()) {
            throw new IllegalStateException("Pick at least one line");
        }
        Map<String, Object> v = jdbc.sql("SELECT status, current_stage FROM video WHERE id = :id").param("id", id)
                .query().listOfRows().stream().findFirst().orElseThrow(() -> new NoSuchElementException("No video " + id));
        if ("RUNNING".equals(v.get("status")) && List.of("NARRATION", "ANIMATIC").contains(v.get("current_stage"))) {
            throw new IllegalStateException("The voice or the animatic is being made right now; wait until that finishes");
        }
        String workflowId = "voice-redo-" + id + "-" + UUID.randomUUID();
        com.projectv.workflow.VoiceRedoWorkflow wf = temporal.newWorkflowStub(com.projectv.workflow.VoiceRedoWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId(workflowId).setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new com.projectv.workflow.VoiceRedoWorkflow.VoiceRedoRequest(id.toString(),
                List.copyOf(lineIndexes), styleNotes));
        recordDecision(id, Stage.NARRATION, "voice_redo", (styleNotes == null ? "" : styleNotes) + " " + lineIndexes);
        return workflowId;
    }

    public List<Map<String, Object>> list() {
        return jdbc.sql("""
                        SELECT v.id, v.title, v.prompt, v.status, v.current_stage, v.spent_inr, v.created_at, v.updated_at,
                               v.master_asset_id,
                               (SELECT s.keyframe_asset_id FROM shot s JOIN scene sc ON sc.id = s.scene_id
                                WHERE sc.video_id = v.id AND s.keyframe_asset_id IS NOT NULL
                                ORDER BY s.ord LIMIT 1) AS thumb_asset_id,
                               (SELECT count(*) FROM shot s JOIN scene sc ON sc.id = s.scene_id WHERE sc.video_id = v.id) AS shots,
                               (SELECT r.summary FROM stage_run r WHERE r.video_id = v.id AND r.stage = v.current_stage
                                ORDER BY r.attempt DESC LIMIT 1) AS current_summary
                        FROM video v ORDER BY v.created_at DESC
                        """)
                .query().listOfRows();
    }

    public Map<String, Object> detail(UUID id) {
        Map<String, Object> video = jdbc.sql("""
                        SELECT id, title, prompt, status, current_stage, spent_inr, workflow_id, created_at, updated_at,
                               plan_json::text AS plan_json, modes_json::text AS modes_json, review_minutes,
                               master_asset_id, master_package_asset_id, master_captions_asset_id
                        FROM video WHERE id = :id
                        """)
                .param("id", id).query().listOfRows().stream().findFirst()
                .orElseThrow(() -> new NoSuchElementException("No video " + id));
        List<Map<String, Object>> runs = jdbc.sql("""
                        SELECT stage, attempt, mode, status, summary, artifact_json::text AS artifact_json, owner_notes,
                               started_at, finished_at, review_deadline
                        FROM stage_run WHERE video_id = :id ORDER BY started_at, attempt
                        """)
                .param("id", id).query().listOfRows();
        Map<String, Object> out = new LinkedHashMap<>(video);
        out.put("stageRuns", runs);
        return out;
    }

    public void approve(UUID id, Stage stage, String notes) {
        stub(id).approve(stage, notes);
        recordDecision(id, stage, "approve", notes);
    }

    public void reject(UUID id, Stage stage, String notes) {
        stub(id).reject(stage, notes);
        recordDecision(id, stage, "reject", notes);
    }

    @Transactional
    public void setMode(UUID id, Stage stage, StageMode mode) {
        stub(id).setMode(stage, mode);
        jdbc.sql("""
                        UPDATE video SET modes_json = jsonb_set(modes_json, ARRAY['stages', :stage], to_jsonb(CAST(:mode AS text)), true),
                               updated_at = now()
                        WHERE id = :id
                        """)
                .param("stage", stage.name()).param("mode", mode.name()).param("id", id).update();
        recordDecision(id, stage, "mode_change", mode.name());
    }

    @Transactional
    public void setAllModes(UUID id, StageMode mode) {
        stub(id).setAllModes(mode);
        jdbc.sql("UPDATE video SET modes_json = CAST(:modes AS jsonb), updated_at = now() WHERE id = :id")
                .param("modes", modesJson(mode, Map.of())).param("id", id).update();
        recordDecision(id, null, "mode_change", "ALL:" + mode.name());
    }

    public void setReviewMinutes(UUID id, int minutes) {
        int m = Math.max(1, Math.min(minutes, 24 * 60));
        stub(id).setReviewMinutes(m);
        jdbc.sql("UPDATE video SET review_minutes = :m, updated_at = now() WHERE id = :id").param("m", m).param("id", id).update();
    }

    public void pause(UUID id) {
        stub(id).pause();
        jdbc.sql("UPDATE video SET status = 'PAUSED', updated_at = now() WHERE id = :id AND status IN ('RUNNING','WAITING')")
                .param("id", id).update();
    }

    public void resume(UUID id) {
        stub(id).resume();
    }

    private VideoProductionWorkflow stub(UUID id) {
        return temporal.newWorkflowStub(VideoProductionWorkflow.class, "video-" + id);
    }

    private void recordDecision(UUID id, Stage stage, String decision, String notes) {
        String stageName = stage == null ? "ALL" : stage.name();
        jdbc.sql("INSERT INTO approval (id, video_id, stage, decision, notes, actor) VALUES (:aid, :id, :stage, :decision, :notes, 'owner')")
                .param("aid", UUID.randomUUID()).param("id", id).param("stage", stageName)
                .param("decision", decision).param("notes", notes).update();
        // Every owner action is a taste signal (HANDOVER §18).
        String kind = decision.equals("mode_change") ? "mode_change" : "rating";
        jdbc.sql("""
                        INSERT INTO preference_event (id, kind, video_id, context_json, chosen)
                        VALUES (:pid, :kind, :id, CAST(:ctx AS jsonb), :chosen)
                        """)
                .param("pid", UUID.randomUUID()).param("kind", kind).param("id", id)
                .param("ctx", json.writeValueAsString(Map.of("stage", stageName, "decision", decision)))
                .param("chosen", notes).update();
    }

    private String modesJson(StageMode defaultMode, Map<Stage, StageMode> modes) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("default", defaultMode.name());
        Map<String, String> stages = new LinkedHashMap<>();
        modes.forEach((k, v) -> stages.put(k.name(), v.name()));
        m.put("stages", stages);
        return json.writeValueAsString(m);
    }
}
