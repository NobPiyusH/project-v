package com.projectv.gpu.broker;

import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/** What the broker leaves in the ledger (CLAUDE.md rule 6): a RoutingDecision per acquire, a CostEntry per rental. */
public interface BrokerRecords {

    void decision(UUID dreamShortId, String capability, List<Map<String, Object>> considered, String chosen, String reason);

    void cost(UUID dreamShortId, String provider, double hours, double hourlyUsd, String note);

    @Component
    class Jdbc implements BrokerRecords {
        private final JdbcClient jdbc;
        private final ObjectMapper json;
        private final CostLedger ledger;
        private final Pricing pricing;

        public Jdbc(JdbcClient jdbc, ObjectMapper json, CostLedger ledger, Pricing pricing) {
            this.jdbc = jdbc;
            this.json = json;
            this.ledger = ledger;
            this.pricing = pricing;
        }

        private boolean isDream(UUID id) {
            return jdbc.sql("SELECT count(*) FROM dream_short WHERE id = :d").param("d", id).query(Integer.class).single() > 0;
        }

        @Override
        public void decision(UUID dreamShortId, String capability, List<Map<String, Object>> considered, String chosen, String reason) {
            if (!isDream(dreamShortId)) {
                return; // a Video's rental: the Video stage logs its own routing decision (rule 6)
            }
            jdbc.sql("""
                            INSERT INTO routing_decision (id, dream_short_id, stage, capability, candidates_json, chosen_provider_id, reason_text)
                            VALUES (:id, :d, 'DREAM_GPU', :c, CAST(:cands AS jsonb), :chosen, :r)
                            """)
                    .param("id", UUID.randomUUID()).param("d", dreamShortId).param("c", capability)
                    .param("cands", json.writeValueAsString(considered)).param("chosen", chosen).param("r", reason).update();
        }

        @Override
        public void cost(UUID dreamShortId, String provider, double hours, double hourlyUsd, String note) {
            BigDecimal inr = BigDecimal.valueOf(hours * hourlyUsd * pricing.fx()).setScale(4, RoundingMode.HALF_UP);
            // The id is a Dream Short, or a Video that rented the shared pod (Video path, projectv.video.provider-*=broker):
            // a Video's rental is booked on the Video's own ledger.
            boolean dream = isDream(dreamShortId);
            jdbc.sql("""
                            INSERT INTO cost_entry (id, video_id, dream_short_id, provider_id, amount_inr, units, unit_type, estimated, note)
                            VALUES (:id, :v, :d, :p, :amt, :u, 'gpu_hour', true, :note)
                            """)
                    .param("id", UUID.randomUUID()).param("v", dream ? null : dreamShortId, java.sql.Types.OTHER)
                    .param("d", dream ? dreamShortId : null, java.sql.Types.OTHER).param("p", provider).param("amt", inr)
                    .param("u", BigDecimal.valueOf(hours).setScale(4, RoundingMode.HALF_UP))
                    .param("note", "%s ($%.4f/h live)".formatted(note, hourlyUsd)).update();
        }
    }
}
