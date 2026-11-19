package com.projectv.dream;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Learning loop, phases 3-4 (owner, 19 Nov 2026): the owner rates a clip or a finished Short and writes a note; a note
 * becomes a PROPOSED lesson the owner can edit, make ACTIVE or retire. Active lessons go into the next Shorts' writer
 * and picture prompts and into the quality check. No model call: free.
 */
@Component
public class DreamLessons {

    public static final List<String> TARGETS = List.of("WRITER", "PICTURE", "ALL");
    private static final List<String> STATUSES = List.of("PROPOSED", "ACTIVE", "RETIRED");

    private final JdbcClient jdbc;

    public DreamLessons(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /** Saves the review; a note also becomes a proposed lesson (target WRITER for a clip, ALL for the whole Short). */
    @Transactional
    public UUID review(UUID dream, UUID shot, int rating, String note) {
        if (rating < 1 || rating > 5) {
            throw new IllegalArgumentException("Rating is 1 to 5");
        }
        UUID id = UUID.randomUUID();
        jdbc.sql("INSERT INTO dream_review (id, dream_short_id, shot_id, rating, note) VALUES (:id, :d, :s, :r, :n)")
                .param("id", id).param("d", dream).param("s", shot).param("r", rating).param("n", blankToNull(note)).update();
        if (blankToNull(note) != null) {
            jdbc.sql("INSERT INTO dream_lesson (id, rule, target, review_id) VALUES (:id, :rule, :t, :r)")
                    .param("id", UUID.randomUUID()).param("rule", note.strip()).param("t", shot == null ? "ALL" : "WRITER")
                    .param("r", id).update();
        }
        return id;
    }

    public List<Map<String, Object>> reviews(UUID dream) {
        return jdbc.sql("SELECT id, shot_id, rating, note, created_at FROM dream_review WHERE dream_short_id = :d ORDER BY created_at")
                .param("d", dream).query().listOfRows();
    }

    public List<Map<String, Object>> lessons() {
        return jdbc.sql("""
                        SELECT l.id, l.rule, l.target, l.status, l.created_at, r.rating, r.dream_short_id, s.ord AS shot_ord
                        FROM dream_lesson l LEFT JOIN dream_review r ON r.id = l.review_id
                        LEFT JOIN dream_shot s ON s.id = r.shot_id
                        WHERE l.status <> 'RETIRED' ORDER BY l.status, l.created_at DESC
                        """).query().listOfRows();
    }

    /** The owner edits, approves (ACTIVE) or retires a lesson; null fields stay as they are. */
    public void update(UUID id, String rule, String target, String status) {
        if (target != null && !TARGETS.contains(target)) {
            throw new IllegalArgumentException("Target is one of " + TARGETS);
        }
        if (status != null && !STATUSES.contains(status)) {
            throw new IllegalArgumentException("Status is one of " + STATUSES);
        }
        jdbc.sql("""
                        UPDATE dream_lesson SET rule = COALESCE(:rule, rule), target = COALESCE(:t, target),
                               status = COALESCE(:s, status), updated_at = now() WHERE id = :id
                        """).param("rule", blankToNull(rule)).param("t", target).param("s", status).param("id", id).update();
    }

    /** Active rules for these targets (ALL always included), oldest first. */
    public List<String> active(String target) {
        return jdbc.sql("SELECT rule FROM dream_lesson WHERE status = 'ACTIVE' AND target IN ('ALL', :t) ORDER BY created_at")
                .param("t", target).query(String.class).list();
    }

    /** One block for a prompt, or "" when there are none. */
    public String forPrompt(String target) {
        List<String> rules = active(target);
        return rules.isEmpty() ? "" : "\nThe owner's rules learned from earlier Shorts (always follow): - " + String.join(" - ", rules);
    }

    private static String blankToNull(String s) {
        return s == null || s.isBlank() ? null : s.strip();
    }
}
