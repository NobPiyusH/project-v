package com.projectv.gpu;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * A lease is the studio's claim on a pod it started: written right after the pod is created, its heartbeat extended
 * while a job runs, released when the job ends. The watchdog terminates studio pods with no lease, a stale heartbeat
 * or a released lease. Table {@code gpu_pod_lease} (V31).
 */
public interface PodLeases {

    record Lease(String podId, String purpose, Instant heartbeatAt, Instant releasedAt) {
    }

    void open(String provider, String podId, String name, String purpose);

    void heartbeat(String podId);

    void release(String podId);

    Optional<Lease> find(String podId);

    @Component
    class Jdbc implements PodLeases {
        private final JdbcClient jdbc;

        public Jdbc(JdbcClient jdbc) {
            this.jdbc = jdbc;
        }

        @Override
        public void open(String provider, String podId, String name, String purpose) {
            jdbc.sql("""
                            INSERT INTO gpu_pod_lease (pod_id, provider, name, purpose) VALUES (:id, :p, :n, :purpose)
                            ON CONFLICT (pod_id) DO UPDATE SET heartbeat_at = now(), released_at = NULL
                            """).param("id", podId).param("p", provider).param("n", name).param("purpose", purpose).update();
        }

        @Override
        public void heartbeat(String podId) {
            jdbc.sql("UPDATE gpu_pod_lease SET heartbeat_at = now() WHERE pod_id = :id AND released_at IS NULL")
                    .param("id", podId).update();
        }

        @Override
        public void release(String podId) {
            jdbc.sql("UPDATE gpu_pod_lease SET released_at = now() WHERE pod_id = :id AND released_at IS NULL")
                    .param("id", podId).update();
        }

        @Override
        public Optional<Lease> find(String podId) {
            return jdbc.sql("SELECT pod_id, purpose, heartbeat_at, released_at FROM gpu_pod_lease WHERE pod_id = :id")
                    .param("id", podId)
                    .query((rs, i) -> new Lease(rs.getString(1), rs.getString(2), ts(rs.getTimestamp(3)), ts(rs.getTimestamp(4))))
                    .optional();
        }

        public List<Map<String, Object>> recent(int limit) {
            return jdbc.sql("""
                            SELECT pod_id, provider, name, purpose, created_at, heartbeat_at, released_at
                            FROM gpu_pod_lease ORDER BY created_at DESC LIMIT :n
                            """).param("n", limit).query().listOfRows();
        }

        private static Instant ts(java.sql.Timestamp t) {
            return t == null ? null : t.toInstant();
        }
    }
}
