package com.projectv.dream;

import com.projectv.dream.DreamTypes.DreamRequest;
import com.projectv.dream.DreamTypes.DreamStep;
import com.projectv.workflow.StageMode;
import com.projectv.workflow.TaskQueues;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import tools.jackson.databind.ObjectMapper;

import java.util.EnumMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** The Dream Shorts screen's backend: start a Short, read it, and pass the owner's decisions to its workflow. */
@Service
public class DreamStudio {

    public record NewDream(String idea, String look, Integer shots, String quality, String defaultMode,
                           Map<String, String> modes, Integer reviewMinutes, String music) {
    }

    private final JdbcClient jdbc;
    private final WorkflowClient temporal;
    private final ObjectMapper json;
    private final DreamService dreams;
    private final com.projectv.video.ClipProgress clipProgress;

    public DreamStudio(JdbcClient jdbc, WorkflowClient temporal, ObjectMapper json, DreamService dreams,
                       com.projectv.video.ClipProgress clipProgress) {
        this.jdbc = jdbc;
        this.temporal = temporal;
        this.json = json;
        this.dreams = dreams;
        this.clipProgress = clipProgress;
    }

    public UUID create(NewDream req) {
        if (req.idea() == null || req.idea().isBlank()) {
            throw new IllegalArgumentException("Type an idea first");
        }
        String look = DreamService.LOOKS.containsKey(req.look()) ? req.look() : DreamService.LOOK_ORDER.getFirst();
        int shots = req.shots() == null ? 5 : Math.max(2, Math.min(8, req.shots()));
        String quality = "720p".equals(req.quality()) ? "720p" : "480p";
        int review = req.reviewMinutes() == null ? 10 : Math.max(1, Math.min(240, req.reviewMinutes()));
        StageMode defaultMode = StageMode.parse(req.defaultMode() == null ? "REVIEW" : req.defaultMode());
        Map<DreamStep, StageMode> modes = new EnumMap<>(DreamStep.class);
        if (req.modes() != null) {
            req.modes().forEach((k, v) -> modes.put(DreamStep.valueOf(k.toUpperCase()), StageMode.parse(v)));
        }
        UUID id = UUID.randomUUID();
        String workflowId = "dream-" + id;
        Map<String, String> modeNames = new LinkedHashMap<>();
        for (DreamStep s : DreamStep.values()) {
            modeNames.put(s.name(), modes.getOrDefault(s, defaultMode).name());
        }
        jdbc.sql("""
                        INSERT INTO dream_short (id, idea, look, shot_count, quality, modes_json, review_minutes, status, step, workflow_id,
                                                 music_source)
                        VALUES (:id, :idea, :look, :n, :q, CAST(:modes AS jsonb), :r, 'RUNNING', 'WRITE', :wf, :music)
                        """)
                .param("music", "lyria".equalsIgnoreCase(req.music()) ? "lyria" : "bed")
                .param("id", id).param("idea", req.idea().strip()).param("look", look).param("n", shots).param("q", quality)
                .param("modes", json.writeValueAsString(modeNames)).param("r", review).param("wf", workflowId).update();
        DreamShortWorkflow wf = temporal.newWorkflowStub(DreamShortWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId(workflowId).setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new DreamRequest(id.toString(), defaultMode, modes, review));
        dreams.evidence().event(id, null, "create", null, req.idea(), Map.of("look", look, "shots", shots, "quality", quality,
                "music", "lyria".equalsIgnoreCase(req.music()) ? "lyria" : "bed", "mode", defaultMode.name()));
        return id;
    }

    /**
     * Redo a finished (or cancelled) Short from a step: that step and the ones after it run again; earlier results
     * stay. Modes come from the Short's saved settings.
     */
    public void rerun(UUID id, DreamStep from) {
        Map<String, Object> d = dreams.dream(id);
        String st = (String) d.get("status");
        if (!"COMPLETED".equals(st) && !"CANCELLED".equals(st)) {
            throw new IllegalStateException("This Short is still running: use approve / redo on its current step");
        }
        Map<String, String> saved = json.readValue(d.get("modes_json").toString(),
                new tools.jackson.core.type.TypeReference<Map<String, String>>() {
                });
        Map<DreamStep, StageMode> modes = new EnumMap<>(DreamStep.class);
        saved.forEach((k, v) -> {
            try {
                modes.put(DreamStep.valueOf(k), StageMode.parse(v));
            } catch (IllegalArgumentException ignored) { // a step that no longer exists
            }
        });
        jdbc.sql("UPDATE dream_short SET status = 'RUNNING', step = :s, message = NULL, updated_at = now() WHERE id = :id")
                .param("s", from.name()).param("id", id).update();
        DreamShortWorkflow wf = temporal.newWorkflowStub(DreamShortWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId("dream-" + id).setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new DreamRequest(id.toString(), StageMode.AUTO, modes,
                ((Number) d.get("review_minutes")).intValue(), from));
        dreams.evidence().event(id, from.name(), "rerun", null, null, null);
    }

    public List<Map<String, Object>> list() {
        return jdbc.sql("""
                        SELECT d.id, d.idea, d.title, d.look, d.status, d.step, d.message, d.final_asset_id, d.created_at, d.updated_at,
                               (SELECT p.picture_asset_id FROM dream_shot p WHERE p.dream_short_id = d.id ORDER BY p.ord LIMIT 1) AS thumb_asset_id,
                               COALESCE((SELECT sum(amount_inr) FROM cost_entry c WHERE c.dream_short_id = d.id), 0) AS spent_inr
                        FROM dream_short d ORDER BY d.created_at DESC LIMIT 100
                        """).query().listOfRows();
    }

    public Map<String, Object> detail(UUID id) {
        Map<String, Object> out = new LinkedHashMap<>(dreams.dream(id));
        out.remove("world_json");
        out.put("shots", jdbc.sql("""
                        SELECT id, ord, scene, picture_prompt, motion_prompt, picture_asset_id, clip_asset_id, picture_takes,
                               clip_takes, error FROM dream_shot WHERE dream_short_id = :d ORDER BY ord
                        """).param("d", id).query().listOfRows());
        out.put("costs", jdbc.sql("""
                        SELECT provider_id, amount_inr, units, unit_type, estimated, note, ts FROM cost_entry
                        WHERE dream_short_id = :d ORDER BY ts
                        """).param("d", id).query().listOfRows());
        out.put("decisions", jdbc.sql("""
                        SELECT stage, capability, chosen_provider_id, reason_text, candidates_json::text AS candidates, created_at
                        FROM routing_decision WHERE dream_short_id = :d ORDER BY created_at
                        """).param("d", id).query().listOfRows());
        out.put("events", dreams.evidence().events(id));
        out.put("metrics", dreams.evidence().metrics(id));
        var progress = clipProgress.get(id);
        out.put("clipProgress", progress == null ? null : progress.clips());
        try {
            out.put("view", stub(id).view());
        } catch (Exception e) { // finished workflows can still be queried; a missing one just has no live view
            out.put("view", null);
        }
        return out;
    }

    public void approve(UUID id, DreamStep step) {
        stub(id).approve(step);
        dreams.evidence().event(id, step.name(), "approve", null, null, null);
    }

    public void redo(UUID id, DreamStep step, List<String> shotIds, String notes) {
        stub(id).redo(step, shotIds, notes);
        dreams.evidence().event(id, step.name(), "redo", shotIds, notes, null);
    }

    public void setMode(UUID id, DreamStep step, StageMode mode) {
        stub(id).setMode(step, mode);
        dreams.evidence().event(id, step.name(), "mode", null, null, Map.of("mode", mode.name()));
        jdbc.sql("UPDATE dream_short SET modes_json = jsonb_set(modes_json, CAST(:path AS text[]), to_jsonb(CAST(:m AS text))) WHERE id = :id")
                .param("path", "{" + step.name() + "}").param("m", mode.name()).param("id", id).update();
    }

    public void retry(UUID id) {
        stub(id).retry();
        dreams.evidence().event(id, null, "retry", null, null, null);
    }

    public void cancel(UUID id) {
        clipProgress.requestStop(id);
        stub(id).cancel();
        dreams.evidence().event(id, null, "cancel", null, null, null);
    }

    /** The owner's Stop: clips in flight stop after the one being rendered (rule 8). */
    public void stopClips(UUID id) {
        clipProgress.requestStop(id);
        dreams.evidence().event(id, "CLIPS", "stop", null, null, null);
    }

    public List<Map<String, Object>> calibration() {
        return dreams.evidence().calibration();
    }

    private DreamShortWorkflow stub(UUID id) {
        return temporal.newWorkflowStub(DreamShortWorkflow.class, "dream-" + id);
    }
}
