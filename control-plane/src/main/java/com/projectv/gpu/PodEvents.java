package com.projectv.gpu;

import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.List;
import java.util.Map;

/** What the watchdog leaves behind when it terminates a pod: a CostEntry (rule 6) and a {@code gpu_pod_event} row. */
public interface PodEvents {

    /**
     * @param hours        how long the pod had been billing (from its start, else its creation)
     * @param estimatedUsd hours x the provider's live hourly rate: an estimate, the provider's bill is the truth
     */
    void terminated(String provider, PodApi.Pod pod, String reason, double hours, double estimatedUsd);

    /** The last time an orphan's running cost was booked by a dry-run sweep (so the real kill books only the rest). */
    default java.util.Optional<java.time.Instant> lastOrphanBooking(String podId) {
        return java.util.Optional.empty();
    }

    /** A dry-run sweep found a studio pod that should be gone but is still billing: book what it has cost since last time. */
    default void orphanBilled(String provider, PodApi.Pod pod, String reason, double hours, double estimatedUsd) {
    }

    @Component
    class Jdbc implements PodEvents {
        private final JdbcClient jdbc;
        private final CostLedger ledger;
        private final Pricing pricing;
        private final ObjectMapper json;

        public Jdbc(JdbcClient jdbc, CostLedger ledger, Pricing pricing, ObjectMapper json) {
            this.jdbc = jdbc;
            this.ledger = ledger;
            this.pricing = pricing;
            this.json = json;
        }

        @Override
        public void terminated(String provider, PodApi.Pod pod, String reason, double hours, double estimatedUsd) {
            BigDecimal inr = BigDecimal.valueOf(estimatedUsd * pricing.fx()).setScale(4, RoundingMode.HALF_UP);
            ledger.record(null, provider, inr, BigDecimal.valueOf(hours).setScale(4, RoundingMode.HALF_UP), "gpu_hour",
                    true, "GPU watchdog terminated pod %s (%s): %s; $%.4f/h live rate".formatted(
                            pod.id(), pod.name(), reason, pod.costPerHrUsd()));
            jdbc.sql("""
                            INSERT INTO gpu_pod_event (provider, pod_id, name, kind, reason, data)
                            VALUES (:p, :id, :n, 'terminated', :r, CAST(:d AS jsonb))
                            """).param("p", provider).param("id", pod.id()).param("n", pod.name()).param("r", reason)
                    .param("d", json.writeValueAsString(Map.of("hours", hours, "estimatedUsd", estimatedUsd,
                            "costPerHrUsd", pod.costPerHrUsd(), "status", pod.status())))
                    .update();
        }

        @Override
        public java.util.Optional<java.time.Instant> lastOrphanBooking(String podId) {
            return java.util.Optional.ofNullable(jdbc.sql("SELECT max(created_at) FROM gpu_pod_event WHERE pod_id = :id AND kind = 'orphan_billed'")
                    .param("id", podId).query((rs, i) -> rs.getTimestamp(1)).optional().orElse(null))
                    .map(java.sql.Timestamp::toInstant);
        }

        @Override
        public void orphanBilled(String provider, PodApi.Pod pod, String reason, double hours, double estimatedUsd) {
            BigDecimal inr = BigDecimal.valueOf(estimatedUsd * pricing.fx()).setScale(4, RoundingMode.HALF_UP);
            ledger.record(null, provider, inr, BigDecimal.valueOf(hours).setScale(4, RoundingMode.HALF_UP), "gpu_hour",
                    true, "GPU watchdog (dry-run): pod %s (%s) still billing, should be terminated: %s; $%.4f/h live rate".formatted(
                            pod.id(), pod.name(), reason, pod.costPerHrUsd()));
            jdbc.sql("""
                            INSERT INTO gpu_pod_event (provider, pod_id, name, kind, reason, data)
                            VALUES (:p, :id, :n, 'orphan_billed', :r, CAST(:d AS jsonb))
                            """).param("p", provider).param("id", pod.id()).param("n", pod.name()).param("r", reason)
                    .param("d", json.writeValueAsString(Map.of("hours", hours, "estimatedUsd", estimatedUsd,
                            "costPerHrUsd", pod.costPerHrUsd(), "status", pod.status())))
                    .update();
        }

        public List<Map<String, Object>> recent(int limit) {
            return jdbc.sql("""
                            SELECT provider, pod_id, name, kind, reason, data::text AS data, created_at
                            FROM gpu_pod_event ORDER BY created_at DESC LIMIT :n
                            """).param("n", limit).query().listOfRows();
        }
    }
}
