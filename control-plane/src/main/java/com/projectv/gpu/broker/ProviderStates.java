package com.projectv.gpu.broker;

import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Per-provider state kept by the broker: {@code proven} (a real worker has run on it; only proven providers are searched by
 * Auto or listed as rentable) and the last free pre-check. Table {@code gpu_provider_state} (V41).
 */
public interface ProviderStates {

    record State(boolean proven, String provenNote, Boolean preflightOk, Instant preflightAt, String preflightNote) {
        static final State UNKNOWN = new State(false, null, null, null, null);
    }

    State get(String provider);

    void recordPreflight(String provider, boolean ok, String note);

    void markProven(String provider, String note);

    default boolean proven(String provider) {
        return get(provider).proven();
    }

    /** Every provider counts as proven (tests and callers that never asked for the gate). */
    ProviderStates ALL_PROVEN = new ProviderStates() {
        public State get(String provider) {
            return new State(true, "gate off", null, null, null);
        }

        public void recordPreflight(String provider, boolean ok, String note) {
        }

        public void markProven(String provider, String note) {
        }
    };

    /** In memory only: for tests. */
    class Memory implements ProviderStates {
        private final Map<String, State> map = new ConcurrentHashMap<>();

        public Memory(String... proven) {
            for (String p : proven) {
                map.put(p, new State(true, "seeded", null, null, null));
            }
        }

        public State get(String provider) {
            return map.getOrDefault(provider, State.UNKNOWN);
        }

        public void recordPreflight(String provider, boolean ok, String note) {
            State s = get(provider);
            map.put(provider, new State(s.proven(), s.provenNote(), ok, Instant.now(), note));
        }

        public void markProven(String provider, String note) {
            State s = get(provider);
            map.put(provider, new State(true, note, s.preflightOk(), s.preflightAt(), s.preflightNote()));
        }
    }

    @Component
    class Jdbc implements ProviderStates {
        private final JdbcClient jdbc;

        public Jdbc(JdbcClient jdbc) {
            this.jdbc = jdbc;
        }

        @Override
        public State get(String provider) {
            return jdbc.sql("SELECT proven, proven_note, preflight_ok, preflight_at, preflight_note FROM gpu_provider_state WHERE provider = :p")
                    .param("p", provider).query((rs, i) -> new State(rs.getBoolean(1), rs.getString(2),
                            (Boolean) rs.getObject(3), rs.getTimestamp(4) == null ? null : rs.getTimestamp(4).toInstant(), rs.getString(5)))
                    .optional().orElse(State.UNKNOWN);
        }

        @Override
        public void recordPreflight(String provider, boolean ok, String note) {
            jdbc.sql("""
                    INSERT INTO gpu_provider_state (provider, preflight_ok, preflight_at, preflight_note) VALUES (:p, :ok, now(), :n)
                    ON CONFLICT (provider) DO UPDATE SET preflight_ok = :ok, preflight_at = now(), preflight_note = :n
                    """).param("p", provider).param("ok", ok).param("n", note).update();
        }

        @Override
        public void markProven(String provider, String note) {
            jdbc.sql("""
                    INSERT INTO gpu_provider_state (provider, proven, proven_at, proven_note) VALUES (:p, true, now(), :n)
                    ON CONFLICT (provider) DO UPDATE SET proven = true, proven_at = now(), proven_note = :n
                    """).param("p", provider).param("n", note).update();
        }
    }
}
