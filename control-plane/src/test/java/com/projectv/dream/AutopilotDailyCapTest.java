package com.projectv.dream;

import com.projectv.ledger.Pricing;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;
import tools.jackson.databind.json.JsonMapper;

import java.math.BigDecimal;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** The autopilot's 50 rupee daily cap and the Producer's 100 / "ask above 50" lines hold for Dream Shorts. */
@Testcontainers(disabledWithoutDocker = true)
class AutopilotDailyCapTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static DreamProducer producer;

    @BeforeAll
    static void setUp() throws Exception {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        jdbc = JdbcClient.create(new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()));
        producer = new DreamProducer(jdbc, JsonMapper.builder().build(), null, null, new Pricing("src/test/resources/config"), "gemini-3.6-flash");
    }

    UUID dream(boolean autopilot) {
        UUID d = UUID.randomUUID();
        jdbc.sql("INSERT INTO dream_short (id, idea, status) VALUES (:id, 'x', 'RUNNING')").param("id", d).update();
        if (autopilot) {
            jdbc.sql("INSERT INTO autopilot_run (id, run_date, status, dream_short_id) VALUES (:id, CURRENT_DATE, 'RUNNING', :d)")
                    .param("id", UUID.randomUUID()).param("d", d).update();
        }
        return d;
    }

    void spend(UUID d, String inr) {
        jdbc.sql("INSERT INTO cost_entry (id, dream_short_id, provider_id, amount_inr) VALUES (:id, :d, 'gemini_api', :a)")
                .param("id", UUID.randomUUID()).param("d", d).param("a", new BigDecimal(inr)).update();
    }

    @Test
    void anAutopilotShortIsHeldToTheDefaultFiftyRupeeDailyCap() {
        UUID d = dream(true);
        assertEquals(0, new BigDecimal("50").compareTo(producer.limit(d)), "default daily cap 50 is below the 100 Short budget");
        spend(d, "40");
        assertTrue(producer.mayRedo(d.toString(), 1));
        spend(d, "10");
        assertFalse(producer.mayRedo(d.toString(), 1), "50 spent: the autopilot Short may not redo or spend more");
    }

    @Test
    void aNormalShortUsesTheHundredRupeeBudgetAndAsksAboveFifty() {
        UUID d = dream(false);
        BigDecimal ask = new BigDecimal("50");
        BigDecimal limit = producer.limit(d);
        assertEquals(0, new BigDecimal("100").compareTo(limit));
        assertTrue(DreamProducer.fits(new BigDecimal("50"), ask, BigDecimal.ZERO, limit));
        assertFalse(DreamProducer.fits(new BigDecimal("50.01"), ask, BigDecimal.ZERO, limit), "above 50: ask the owner");
        assertTrue(DreamProducer.fits(new BigDecimal("40"), ask, new BigDecimal("60"), limit));
        assertFalse(DreamProducer.fits(new BigDecimal("41"), ask, new BigDecimal("60"), limit), "over the 100 Short budget");
    }
}
