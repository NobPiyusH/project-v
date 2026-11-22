package com.projectv.gpu.broker;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Where a rental's boot time goes. Three sources: the broker's "rent accepted" time, the first time the provider reported
 * the pod RUNNING, and the worker's own timeline from its /health ({@code timing}: marks plus one row per weight
 * download). {@link #summary} joins them into one table of seconds since the rent, which the GPU API returns and the log
 * prints. Table {@code gpu_boot_timing} (V42). Never throws into a rental: a lost timing row costs nothing.
 */
public interface BootTimings {

    record Row(String podId, String provider, String image, Instant rentedAt, Instant runningAt, Map<String, Object> worker) {
    }

    void rented(String podId, String provider, String image, Instant at);

    /** First call wins: later "running" sightings do not move it. */
    void running(String podId, Instant at);

    /** The worker's timeline (its /health "timing" object); the first report per pod wins. */
    void workerReport(String podId, Map<String, Object> timing);

    Optional<Row> get(String podId);

    List<Row> recent(int limit);

    BootTimings NONE = new BootTimings() {
        public void rented(String podId, String provider, String image, Instant at) {
        }

        public void running(String podId, Instant at) {
        }

        public void workerReport(String podId, Map<String, Object> timing) {
        }

        public Optional<Row> get(String podId) {
            return Optional.empty();
        }

        public List<Row> recent(int limit) {
            return List.of();
        }
    };

    /**
     * Seconds since the rent for each step we know, in order: {@code rented}, {@code provider_running},
     * {@code container_started} (the worker's first mark: image pulled and the process started), {@code deps_installed},
     * {@code comfyui_fetched}, {@code weights_done}, {@code comfyui_up}, {@code ready} (first job can be taken), plus the
     * {@code downloads} rows and {@code totalSeconds}. Worker times come from the pod's clock: a skewed clock shows as an
     * odd gap before {@code container_started}, which is why the worker's own {@code since_start_s} is returned too.
     */
    static Map<String, Object> summary(Row row) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("podId", row.podId());
        out.put("provider", row.provider());
        out.put("image", row.image());
        out.put("rentedAt", row.rentedAt().toString());
        List<Map<String, Object>> steps = new ArrayList<>();
        steps.add(step("rented", 0, null));
        if (row.runningAt() != null) {
            steps.add(step("provider_running", round1(Duration.between(row.rentedAt(), row.runningAt()).toMillis() / 1000.0), null));
        }
        Double total = null;
        if (row.worker() != null) {
            if (row.worker().get("marks") instanceof List<?> marks) {
                for (Object o : marks) {
                    if (o instanceof Map<?, ?> m && m.get("at") instanceof Number at) {
                        double since = round1(at.doubleValue() - row.rentedAt().toEpochMilli() / 1000.0);
                        String phase = String.valueOf(m.get("phase"));
                        steps.add(step("worker_started".equals(phase) ? "container_started" : phase, since,
                                m.get("since_start_s") instanceof Number s ? s.doubleValue() : null));
                        if ("ready".equals(phase)) {
                            total = since;
                        }
                    }
                }
            }
            if (row.worker().get("downloads") instanceof List<?> dl) {
                out.put("downloads", dl);
            }
        }
        out.put("steps", steps);
        out.put("totalSeconds", total);
        return out;
    }

    private static Map<String, Object> step(String phase, double secondsSinceRent, Double workerSinceStart) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("phase", phase);
        m.put("secondsSinceRent", secondsSinceRent);
        if (workerSinceStart != null) {
            m.put("workerSinceStartSeconds", workerSinceStart);
        }
        return m;
    }

    private static double round1(double v) {
        return Math.round(v * 10) / 10.0;
    }

    /** In memory: for tests. */
    class Memory implements BootTimings {
        private final Map<String, Row> rows = new ConcurrentHashMap<>();

        public void rented(String podId, String provider, String image, Instant at) {
            rows.put(podId, new Row(podId, provider, image, at, null, null));
        }

        public void running(String podId, Instant at) {
            rows.computeIfPresent(podId, (k, r) -> r.runningAt() != null ? r
                    : new Row(r.podId(), r.provider(), r.image(), r.rentedAt(), at, r.worker()));
        }

        public void workerReport(String podId, Map<String, Object> timing) {
            rows.computeIfPresent(podId, (k, r) -> r.worker() != null ? r
                    : new Row(r.podId(), r.provider(), r.image(), r.rentedAt(), r.runningAt(), timing));
        }

        public Optional<Row> get(String podId) {
            return Optional.ofNullable(rows.get(podId));
        }

        public List<Row> recent(int limit) {
            return rows.values().stream().sorted((a, b) -> b.rentedAt().compareTo(a.rentedAt())).limit(limit).toList();
        }
    }

    @Component
    class Jdbc implements BootTimings {
        private static final String COLUMNS = "pod_id, provider, image, rented_at, running_at, worker::text AS worker";
        private final JdbcClient jdbc;
        private final ObjectMapper json;

        public Jdbc(JdbcClient jdbc, ObjectMapper json) {
            this.jdbc = jdbc;
            this.json = json;
        }

        public void rented(String podId, String provider, String image, Instant at) {
            jdbc.sql("""
                    INSERT INTO gpu_boot_timing (pod_id, provider, image, rented_at) VALUES (:id, :p, :i, :t)
                    ON CONFLICT (pod_id) DO NOTHING
                    """).param("id", podId).param("p", provider).param("i", image)
                    .param("t", java.sql.Timestamp.from(at)).update();
        }

        public void running(String podId, Instant at) {
            jdbc.sql("UPDATE gpu_boot_timing SET running_at = :t WHERE pod_id = :id AND running_at IS NULL")
                    .param("t", java.sql.Timestamp.from(at)).param("id", podId).update();
        }

        public void workerReport(String podId, Map<String, Object> timing) {
            jdbc.sql("""
                    UPDATE gpu_boot_timing SET worker = CAST(:w AS jsonb), reported_at = now()
                    WHERE pod_id = :id AND worker IS NULL
                    """).param("w", json.writeValueAsString(timing)).param("id", podId).update();
        }

        @SuppressWarnings("unchecked")
        private Row map(java.sql.ResultSet rs) throws java.sql.SQLException {
            String w = rs.getString("worker");
            return new Row(rs.getString("pod_id"), rs.getString("provider"), rs.getString("image"),
                    rs.getTimestamp("rented_at").toInstant(),
                    rs.getTimestamp("running_at") == null ? null : rs.getTimestamp("running_at").toInstant(),
                    w == null ? null : json.readValue(w, Map.class));
        }

        public Optional<Row> get(String podId) {
            return jdbc.sql("SELECT " + COLUMNS + " FROM gpu_boot_timing WHERE pod_id = :id")
                    .param("id", podId).query((rs, i) -> map(rs)).optional();
        }

        public List<Row> recent(int limit) {
            return jdbc.sql("SELECT " + COLUMNS + " FROM gpu_boot_timing ORDER BY rented_at DESC LIMIT :n")
                    .param("n", limit).query((rs, i) -> map(rs)).list();
        }
    }
}
