package com.projectv.salad;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.Optional;
import java.util.UUID;

/** Persistence for the Salad route (table {@code salad_session}, V33, plus the cost ledger). Faked in tests. */
public interface SaladStore {

    void insert(String name, UUID dreamId, String url, String gpuClass, String priority, double hourlyUsd);

    Optional<SaladSessions.Session> open(UUID dreamId);

    void addBooked(UUID dreamId, double seconds);

    double booked(String groupName);

    void markDeleted(String groupName);

    /** A CostEntry (rule 6) for the Short: provider {@code salad}, estimated. */
    void cost(UUID dreamId, BigDecimal inr, double units, String note);

    @Component
    class Jdbc implements SaladStore {
        private final JdbcClient jdbc;

        public Jdbc(JdbcClient jdbc) {
            this.jdbc = jdbc;
        }

        @Override
        public void insert(String name, UUID dreamId, String url, String gpuClass, String priority, double hourlyUsd) {
            jdbc.sql("""
                            INSERT INTO salad_session (group_name, dream_short_id, url, gpu_class, priority, hourly_usd)
                            VALUES (:n, :d, :u, :g, :p, :r)
                            """).param("n", name).param("d", dreamId).param("u", url).param("g", gpuClass)
                    .param("p", priority).param("r", BigDecimal.valueOf(hourlyUsd)).update();
        }

        @Override
        public Optional<SaladSessions.Session> open(UUID dreamId) {
            return jdbc.sql("SELECT group_name, url, hourly_usd, gpu_class, created_at FROM salad_session "
                            + "WHERE dream_short_id = :d AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1").param("d", dreamId)
                    .query((rs, i) -> new SaladSessions.Session(rs.getString(1), rs.getString(2), rs.getBigDecimal(3).doubleValue(),
                            rs.getString(4), rs.getTimestamp(5).toInstant())).optional();
        }

        @Override
        public void addBooked(UUID dreamId, double seconds) {
            jdbc.sql("UPDATE salad_session SET booked_seconds = booked_seconds + :s WHERE dream_short_id = :d AND deleted_at IS NULL")
                    .param("s", seconds).param("d", dreamId).update();
        }

        @Override
        public double booked(String groupName) {
            return jdbc.sql("SELECT booked_seconds FROM salad_session WHERE group_name = :n").param("n", groupName)
                    .query(Double.class).single();
        }

        @Override
        public void markDeleted(String groupName) {
            jdbc.sql("UPDATE salad_session SET deleted_at = now() WHERE group_name = :n").param("n", groupName).update();
        }

        @Override
        public void cost(UUID dreamId, BigDecimal inr, double units, String note) {
            jdbc.sql("""
                            INSERT INTO cost_entry (id, video_id, dream_short_id, provider_id, amount_inr, units, unit_type, estimated, note)
                            VALUES (:id, NULL, :d, 'salad', :amt, :u, 'gpu_seconds', true, :note)
                            """).param("id", UUID.randomUUID()).param("d", dreamId).param("amt", inr)
                    .param("u", BigDecimal.valueOf(units)).param("note", note).update();
        }
    }
}
