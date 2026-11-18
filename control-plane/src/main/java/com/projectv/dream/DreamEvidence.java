package com.projectv.dream;

import org.springframework.jdbc.core.simple.JdbcClient;
import tools.jackson.databind.ObjectMapper;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * The learning loop's memory, phase 1 (docs/plans/dream-learning-loop.md): what the owner decided, what failed, what
 * every finished Short measured, what each paid step was estimated to cost against what it did cost, and real GPU
 * timings. The timings recalibrate the clip estimate after every batch (lesson 1: never quote a scaled guess).
 */
public class DreamEvidence {

    /** Fewer clips than this and the config's number stands. */
    static final int MIN_SAMPLES = 2;
    /** Only the most recent clips count: the GPU, the model or Modal can change. */
    static final int RECENT = 30;

    private final JdbcClient jdbc;
    private final ObjectMapper json;

    public DreamEvidence(JdbcClient jdbc, ObjectMapper json) {
        this.jdbc = jdbc;
        this.json = json;
    }

    // ------------------------------------------------------------------------------------------- events

    public void event(UUID dreamId, String step, String kind, List<String> shotIds, String notes, Map<String, ?> data) {
        jdbc.sql("""
                        INSERT INTO dream_event (id, dream_short_id, step, kind, shot_ids, notes, data)
                        VALUES (:id, :d, :step, :kind, CAST(:shots AS jsonb), :notes, CAST(:data AS jsonb))
                        """)
                .param("id", UUID.randomUUID()).param("d", dreamId).param("step", step).param("kind", kind)
                .param("shots", shotIds == null || shotIds.isEmpty() ? null : json.writeValueAsString(shotIds))
                .param("notes", notes == null || notes.isBlank() ? null : notes.strip())
                .param("data", data == null || data.isEmpty() ? null : json.writeValueAsString(data)).update();
    }

    /** Estimate shown before a paid step vs what it really cost (both in ₹). */
    public void costCheck(UUID dreamId, String step, double estimateInr, double actualInr) {
        event(dreamId, step, "cost", null, null, Map.of("estimateInr", round(estimateInr), "actualInr", round(actualInr),
                "ratio", estimateInr > 0 ? round(actualInr / estimateInr) : 0));
    }

    public List<Map<String, Object>> events(UUID dreamId) {
        return jdbc.sql("""
                        SELECT step, kind, shot_ids::text AS shot_ids, notes, data::text AS data, created_at
                        FROM dream_event WHERE dream_short_id = :d ORDER BY created_at
                        """).param("d", dreamId).query().listOfRows();
    }

    // ------------------------------------------------------------------------------------------- measurements

    public void metrics(UUID dreamId, UUID assetId, String look, Map<String, Double> metrics) {
        if (metrics == null || metrics.isEmpty()) {
            return;
        }
        jdbc.sql("""
                        INSERT INTO dream_metric (asset_id, dream_short_id, look, metrics) VALUES (:a, :d, :l, CAST(:m AS jsonb))
                        ON CONFLICT (asset_id) DO UPDATE SET metrics = EXCLUDED.metrics
                        """).param("a", assetId).param("d", dreamId).param("l", look).param("m", json.writeValueAsString(metrics)).update();
    }

    public List<Map<String, Object>> metrics(UUID dreamId) {
        return jdbc.sql("""
                        SELECT asset_id, look, metrics::text AS metrics, created_at FROM dream_metric
                        WHERE dream_short_id = :d ORDER BY created_at DESC
                        """).param("d", dreamId).query().listOfRows();
    }

    // ------------------------------------------------------------------------------------------- GPU timings → calibration

    public void gpuTiming(UUID dreamId, String kind, int frames, double seconds, boolean first) {
        if (seconds <= 0 || frames <= 0) {
            return;
        }
        jdbc.sql("""
                        INSERT INTO gpu_timing (id, kind, frames, seconds, first_in_batch, dream_short_id)
                        VALUES (:id, :k, :f, :s, :first, :d)
                        """).param("id", UUID.randomUUID()).param("k", kind).param("f", frames).param("s", seconds)
                .param("first", first).param("d", dreamId).update();
    }

    /**
     * Recompute {@code <kind>.per81} (median GPU seconds per 81 frames, warm clips only) and {@code <kind>.first_extra}
     * (median extra seconds of a batch's first clip) from the most recent clips. Returns what changed, for the log.
     */
    public List<String> recalibrate(String kind) {
        List<String> changed = new ArrayList<>();
        List<double[]> rows = jdbc.sql("""
                        SELECT frames, seconds, first_in_batch FROM gpu_timing WHERE kind = :k ORDER BY created_at DESC LIMIT :n
                        """).param("k", kind).param("n", RECENT)
                .query((rs, i) -> new double[]{rs.getInt(1), rs.getDouble(2), rs.getBoolean(3) ? 1 : 0}).list();
        List<Double> warm = rows.stream().filter(r -> r[2] == 0).map(r -> r[1] * 81 / r[0]).toList();
        if (warm.size() < MIN_SAMPLES) {
            return changed;
        }
        double per81 = median(warm);
        save(kind + ".per81", per81, warm.size(), "median of the last %d warm clips".formatted(warm.size()));
        changed.add("%s: %.0f s per 81 frames (%d clips)".formatted(kind, per81, warm.size()));
        List<Double> extra = rows.stream().filter(r -> r[2] == 1).map(r -> Math.max(0, r[1] - per81 * r[0] / 81)).toList();
        if (!extra.isEmpty()) {
            double e = median(extra);
            save(kind + ".first_extra", e, extra.size(), "median warm-up of the last %d first clips".formatted(extra.size()));
            changed.add("%s: first clip +%.0f s (%d batches)".formatted(kind, e, extra.size()));
        }
        return changed;
    }

    public Optional<Double> calibrated(String key) {
        return jdbc.sql("SELECT value FROM calibration WHERE key = :k").param("k", key).query(Double.class).optional();
    }

    public List<Map<String, Object>> calibration() {
        return jdbc.sql("SELECT key, value, samples, source, updated_at FROM calibration ORDER BY key").query().listOfRows();
    }

    private void save(String key, double value, int samples, String source) {
        jdbc.sql("""
                        INSERT INTO calibration (key, value, samples, source) VALUES (:k, :v, :n, :s)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, samples = EXCLUDED.samples,
                               source = EXCLUDED.source, updated_at = now()
                        """).param("k", key).param("v", value).param("n", samples).param("s", source).update();
    }

    static double median(List<Double> xs) {
        List<Double> s = xs.stream().sorted().toList();
        int n = s.size();
        return n % 2 == 1 ? s.get(n / 2) : (s.get(n / 2 - 1) + s.get(n / 2)) / 2;
    }

    private static double round(double x) {
        return Math.round(x * 100) / 100.0;
    }
}
