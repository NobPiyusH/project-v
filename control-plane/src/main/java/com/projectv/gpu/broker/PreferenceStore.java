package com.projectv.gpu.broker;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * What the studio learns from the owner's manual GPU picks (table preference_event, kind {@code gpu_choice}).
 * Deliberately simple and explainable: when 3 of the owner's last 5 picks were the same option and each time it was NOT the
 * cheapest, Auto mode prefers that option (still only if it is available, inside its cap, and at most
 * {@link #MAX_PREMIUM}x the cheapest price). The routing decision says when a choice came from this.
 */
public interface PreferenceStore {

    /** Auto mode never pays more than this multiple of the cheapest price for a learned preference. */
    double MAX_PREMIUM = 1.5;

    PreferenceStore NONE = new PreferenceStore() {
        public Optional<String> learnedKey() {
            return Optional.empty();
        }

        public void recordChoice(UUID shortId, String chosenKey, String cheapestKey, Map<String, Object> context) {
        }
    };

    /** "provider|gpu" the owner keeps choosing over the cheapest, if any. */
    Optional<String> learnedKey();

    void recordChoice(UUID shortId, String chosenKey, String cheapestKey, Map<String, Object> context);

    @Component
    class Jdbc implements PreferenceStore {
        private final JdbcClient jdbc;
        private final ObjectMapper json;

        public Jdbc(JdbcClient jdbc, ObjectMapper json) {
            this.jdbc = jdbc;
            this.json = json;
        }

        @Override
        public Optional<String> learnedKey() {
            List<Map<String, Object>> rows = jdbc.sql("""
                            SELECT chosen, rejected FROM preference_event WHERE kind = 'gpu_choice' ORDER BY ts DESC LIMIT 5
                            """).query().listOfRows();
            return rows.stream().filter(r -> r.get("chosen") != null && !r.get("chosen").equals(r.get("rejected")))
                    .collect(java.util.stream.Collectors.groupingBy(r -> r.get("chosen").toString(), java.util.stream.Collectors.counting()))
                    .entrySet().stream().filter(e -> e.getValue() >= 3).map(Map.Entry::getKey).findFirst();
        }

        @Override
        public void recordChoice(UUID shortId, String chosenKey, String cheapestKey, Map<String, Object> context) {
            jdbc.sql("""
                            INSERT INTO preference_event (id, kind, context_json, chosen, rejected)
                            VALUES (:id, 'gpu_choice', CAST(:ctx AS jsonb), :chosen, :rejected)
                            """).param("id", UUID.randomUUID()).param("ctx", json.writeValueAsString(context))
                    .param("chosen", chosenKey).param("rejected", cheapestKey).update();
        }
    }
}
