package com.projectv.ledger;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** Every money- or quota-spending job writes a CostEntry (HANDOVER §8 rules, §21). Free calls are logged at ₹0. */
@Service
public class CostLedger {

    private final JdbcClient jdbc;

    public CostLedger(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    public void record(UUID videoId, String providerId, BigDecimal amountInr, BigDecimal units, String unitType,
                       boolean estimated, String note) {
        jdbc.sql("""
                        INSERT INTO cost_entry (id, video_id, provider_id, amount_inr, units, unit_type, estimated, note)
                        VALUES (:id, :video, :provider, :amount, :units, :unitType, :estimated, :note)
                        """)
                .param("id", UUID.randomUUID())
                .param("video", videoId)
                .param("provider", providerId)
                .param("amount", amountInr)
                .param("units", units)
                .param("unitType", unitType)
                .param("estimated", estimated)
                .param("note", note)
                .update();
        jdbc.sql("UPDATE video SET spent_inr = spent_inr + :amount, updated_at = now() WHERE id = :video")
                .param("amount", amountInr)
                .param("video", videoId)
                .update();
    }

    public List<Map<String, Object>> totalsByProvider() {
        return jdbc.sql("""
                        SELECT provider_id, count(*) AS entries, sum(amount_inr) AS amount_inr, sum(units) AS units,
                               min(unit_type) AS unit_type
                        FROM cost_entry GROUP BY provider_id ORDER BY provider_id
                        """)
                .query().listOfRows();
    }

    /**
     * Where a Video's money went, per production stage: each stage with its calls, total and the purposes inside it
     * (e.g. SHOT_PLAN: SHOT_PLAN 5 calls, DIAGRAM_SPEC 23 calls). The stage comes from the ledger note's purpose (what the
     * call was for); numbers in a note ("shot 7 take 2") are folded so a purpose is one row. Read-only.
     */
    public List<Map<String, Object>> stageSummary(UUID videoId) {
        List<Map<String, Object>> rows = jdbc.sql("""
                        SELECT regexp_replace(split_part(coalesce(note, provider_id), ' (', 1), '[0-9]+', 'N', 'g') AS purpose,
                               count(*) AS calls, coalesce(sum(amount_inr), 0) AS inr,
                               count(*) FILTER (WHERE amount_inr = 0) AS free_calls
                        FROM cost_entry WHERE video_id = :video GROUP BY 1 ORDER BY 3 DESC
                        """).param("video", videoId).query().listOfRows();
        Map<String, Map<String, Object>> stages = new java.util.LinkedHashMap<>();
        for (Map<String, Object> r : rows) {
            String stage = stageOf((String) r.get("purpose"));
            Map<String, Object> s = stages.computeIfAbsent(stage, k -> {
                Map<String, Object> m = new java.util.LinkedHashMap<>();
                m.put("stage", k);
                m.put("calls", 0L);
                m.put("inr", java.math.BigDecimal.ZERO);
                m.put("purposes", new java.util.ArrayList<Map<String, Object>>());
                return m;
            });
            s.put("calls", (Long) s.get("calls") + ((Number) r.get("calls")).longValue());
            s.put("inr", ((java.math.BigDecimal) s.get("inr")).add((java.math.BigDecimal) r.get("inr")));
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> purposes = (List<Map<String, Object>>) s.get("purposes");
            purposes.add(r);
        }
        List<Map<String, Object>> out = new java.util.ArrayList<>(stages.values());
        out.sort((a, b) -> ((java.math.BigDecimal) b.get("inr")).compareTo((java.math.BigDecimal) a.get("inr")));
        return out;
    }

    /** The production stage a ledger purpose belongs to (OTHER when unknown). */
    static String stageOf(String purpose) {
        String p = purpose == null ? "" : purpose.toUpperCase(java.util.Locale.ROOT);
        if (p.startsWith("SHOT_PLAN") || p.startsWith("DIAGRAM_SPEC") || p.startsWith("PRODUCER")) {
            return "SHOT_PLAN";
        }
        for (String[] m : new String[][]{{"PLAN", "PLAN"}, {"RESEARCH", "RESEARCH"}, {"CONCEPT", "CONCEPT"}, {"SCRIPT", "SCRIPT"},
                {"MUSIC", "MUSIC"}, {"NARRATION", "NARRATION"}, {"VOICE", "NARRATION"}, {"KEYFRAME", "KEYFRAMES"},
                {"CREATIVE_REVIEW", "KEYFRAMES"}, {"DRAFT", "DRAFT_GEN"}, {"WAN GPU", "DRAFT_GEN"}, {"FINAL", "FINAL_GEN"},
                {"SMOOTH", "UPSCALE"}, {"SHARPEN", "UPSCALE"}}) {
            if (p.startsWith(m[0])) {
                return m[1];
            }
        }
        return "OTHER";
    }

    public List<Map<String, Object>> forVideo(UUID videoId) {
        return jdbc.sql("""
                        SELECT provider_id, amount_inr, units, unit_type, estimated, note, ts
                        FROM cost_entry WHERE video_id = :video ORDER BY ts
                        """)
                .param("video", videoId)
                .query().listOfRows();
    }
}
