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

        public List<Map<String, Object>> recent(int limit) {
            return jdbc.sql("""
                            SELECT provider, pod_id, name, kind, reason, data::text AS data, created_at
                            FROM gpu_pod_event ORDER BY created_at DESC LIMIT :n
                            """).param("n", limit).query().listOfRows();
        }
    }
}
