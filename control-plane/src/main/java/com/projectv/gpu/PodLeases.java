package com.projectv.gpu;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * A lease is the studio's claim on a pod it started: written right after the pod is created, its heartbeat extended
 * while a job runs, released when the job ends. The watchdog terminates studio pods with no lease, a stale heartbeat
 * or a released lease. Table {@code gpu_pod_lease} (V31).
 */
public interface PodLeases {

    record Lease(String podId, String purpose, Instant heartbeatAt, Instant releasedAt) {
    }

    void open(String provider, String podId, String name, String purpose);

    /** A lease still held (not released), with what the broker needs to rebuild its rental after a restart. */
    record OpenLease(String podId, String provider, String name, UUID dreamShortId, double hourlyUsd, String gpu,
                     Instant createdAt) {
    }

    /** Opens the lease and remembers the Short, live rate and GPU (default: the plain lease, for fakes). */
    default void open(String provider, String podId, String name, String purpose, UUID dreamShortId, double hourlyUsd, String gpu) {
        open(provider, podId, name, purpose);
    }

    /** Unreleased leases that belong to a Dream Short, oldest first. */
    default List<OpenLease> openForShorts() {
        return List.of();
    }

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
        public void open(String provider, String podId, String name, String purpose, UUID dreamShortId, double hourlyUsd, String gpu) {
            jdbc.sql("""
                            INSERT INTO gpu_pod_lease (pod_id, provider, name, purpose, dream_short_id, hourly_usd, gpu)
                            VALUES (:id, :p, :n, :purpose, :d, :h, :g)
                            ON CONFLICT (pod_id) DO UPDATE SET heartbeat_at = now(), released_at = NULL
                            """).param("id", podId).param("p", provider).param("n", name).param("purpose", purpose)
                    .param("d", dreamShortId).param("h", hourlyUsd).param("g", gpu).update();
        }

        @Override
        public List<OpenLease> openForShorts() {
            return jdbc.sql("""
                            SELECT pod_id, provider, name, dream_short_id, COALESCE(hourly_usd, 0), gpu, created_at
                            FROM gpu_pod_lease WHERE released_at IS NULL AND dream_short_id IS NOT NULL ORDER BY created_at
                            """)
                    .query((rs, i) -> new OpenLease(rs.getString(1), rs.getString(2), rs.getString(3), rs.getObject(4, UUID.class),
                            rs.getDouble(5), rs.getString(6), ts(rs.getTimestamp(7))))
                    .list();
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
