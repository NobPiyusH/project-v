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

    private final JdbcClient jdbc;
    private final WorkflowClient temporal;
    private final ObjectMapper json;

    public VideoService(JdbcClient jdbc, WorkflowClient temporal, ObjectMapper json) {
        this.jdbc = jdbc;
        this.temporal = temporal;
        this.json = json;
    }

    public record CreateVideo(String prompt, StageMode defaultMode, Map<Stage, StageMode> modes) {
    }

    /** Not transactional on purpose: the row must be committed before the workflow's first activity reads it. */
    public UUID create(CreateVideo req) {
        UUID id = UUID.randomUUID();
        String workflowId = "video-" + id;
        StageMode defaultMode = req.defaultMode() == null ? StageMode.AUTO : req.defaultMode();
        Map<Stage, StageMode> modes = req.modes() == null ? new EnumMap<>(Stage.class) : new EnumMap<>(req.modes());
        jdbc.sql("""
                        INSERT INTO video (id, prompt, status, modes_json, workflow_id)
                        VALUES (:id, :prompt, 'CREATED', CAST(:modes AS jsonb), :wf)
                        """)
                .param("id", id)
                .param("prompt", req.prompt().strip())
                .param("modes", modesJson(defaultMode, modes))
                .param("wf", workflowId)
                .update();
        VideoProductionWorkflow wf = temporal.newWorkflowStub(VideoProductionWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId(workflowId).setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new VideoRequest(id.toString(), req.prompt().strip(), defaultMode, modes));
        return id;
    }

    public List<Map<String, Object>> list() {
        return jdbc.sql("""
                        SELECT id, title, prompt, status, current_stage, spent_inr, created_at, updated_at
                        FROM video ORDER BY created_at DESC
                        """)
                .query().listOfRows();
    }

    public Map<String, Object> detail(UUID id) {
        Map<String, Object> video = jdbc.sql("""
                        SELECT id, title, prompt, status, current_stage, spent_inr, workflow_id, created_at, updated_at,
                               plan_json::text AS plan_json, modes_json::text AS modes_json
                        FROM video WHERE id = :id
                        """)
                .param("id", id).query().listOfRows().stream().findFirst()
                .orElseThrow(() -> new NoSuchElementException("No video " + id));
        List<Map<String, Object>> runs = jdbc.sql("""
                        SELECT stage, attempt, mode, status, summary, artifact_json::text AS artifact_json, owner_notes,
                               started_at, finished_at
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
