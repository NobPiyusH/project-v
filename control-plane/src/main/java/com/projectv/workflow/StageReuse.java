package com.projectv.workflow;

import com.projectv.workflow.WorkflowTypes.StageResult;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

/**
 * Cost cut (flag {@code projectv.cost.reuse-stored-stages}, default off): after a backend restart or a duplicate run,
 * a paid text stage (research, concept, script, music, shot plan) that already finished with a stored result is not paid
 * for again. The decision is read-only: it hands back the earlier summary and artifact, it never touches the stored rows.
 * Only a full run uses it (the workflow checks); an owner's redo, a Reject or a REDO_WITH re-run always generates anew.
 */
@Component
public class StageReuse {

    static final String PREFIX = "Reused stored result (no model call): ";
    static final Set<String> ACCEPTED = Set.of("AUTO_PASSED", "APPROVED", "AUTO_APPROVED", "PRODUCER_APPROVED");

    /** Stages whose output is made from these stages: if one of them was genuinely re-made later, the stored result is stale. */
    static final Map<Stage, List<Stage>> UPSTREAM = Map.of(
            Stage.RESEARCH, List.of(),
            Stage.CONCEPT, List.of(Stage.RESEARCH),
            Stage.SCRIPT, List.of(Stage.CONCEPT, Stage.RESEARCH),
            Stage.MUSIC, List.of(Stage.SCRIPT),
            Stage.SHOT_PLAN, List.of(Stage.CONCEPT, Stage.SCRIPT, Stage.MUSIC, Stage.NARRATION));

    /** The table that must still hold the stage's real output (a stage_run row alone is not enough). */
    static final Map<Stage, String> OUTPUT_TABLE = Map.of(
            Stage.RESEARCH, "research", Stage.CONCEPT, "concept", Stage.SCRIPT, "script", Stage.MUSIC, "music_cue",
            Stage.SHOT_PLAN, "scene");

    private final JdbcClient jdbc;
    private final boolean enabled;

    public StageReuse(JdbcClient jdbc, @Value("${projectv.cost.reuse-stored-stages:false}") boolean enabled) {
        this.jdbc = jdbc;
        this.enabled = enabled;
    }

    public Optional<StageResult> reusable(UUID videoId, Stage stage) {
        // Music is the exception: a second song is never paid for while the Video holds its music (always on, bug-fix guard).
        boolean music = stage == Stage.MUSIC;
        if ((!enabled && !music) || !UPSTREAM.containsKey(stage)) {
            return Optional.empty();
        }
        Long output = jdbc.sql("SELECT count(*) FROM " + OUTPUT_TABLE.get(stage) + " WHERE video_id = :v") // constant table names
                .param("v", videoId).query(Long.class).single();
        if (output == 0) {
            return Optional.empty();
        }
        Map<String, Object> last = null;
        for (Map<String, Object> r : runs(videoId, stage)) { // newest first: the first genuine row decides
            String status = (String) r.get("status");
            if (status.equals("RUNNING") || (ACCEPTED.contains(status) && isReuse(r))) {
                continue;
            }
            last = r;
            break;
        }
        if (last == null || !ACCEPTED.contains((String) last.get("status")) || last.get("finished_at") == null
                || last.get("artifact_json") == null) {
            return Optional.empty();
        }
        java.time.Instant made = ((java.sql.Timestamp) last.get("finished_at")).toInstant();
        for (Stage up : music ? List.<Stage>of() : UPSTREAM.get(stage)) { // an owner who wants new music redoes MUSIC itself
            for (Map<String, Object> r : runs(videoId, up)) {
                if (ACCEPTED.contains((String) r.get("status")) && !isReuse(r) && r.get("finished_at") != null
                        && ((java.sql.Timestamp) r.get("finished_at")).toInstant().isAfter(made)) {
                    return Optional.empty(); // an input was re-made after this result
                }
            }
        }
        String summary = (String) last.get("summary");
        return Optional.of(new StageResult(PREFIX + (summary == null ? "" : summary), (String) last.get("artifact_json"), null));
    }

    private List<Map<String, Object>> runs(UUID videoId, Stage stage) {
        return jdbc.sql("""
                        SELECT status, summary, artifact_json::text AS artifact_json, finished_at
                        FROM stage_run WHERE video_id = :v AND stage = :s ORDER BY attempt DESC
                        """).param("v", videoId).param("s", stage.name()).query().listOfRows();
    }

    private static boolean isReuse(Map<String, Object> row) {
        return row.get("summary") instanceof String s && s.startsWith(PREFIX);
    }
}
