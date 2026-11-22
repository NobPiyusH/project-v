package com.projectv.ledger;

import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;

/** The per-stage cost summary the owner reads to see where a Video's money went. */
@Testcontainers(disabledWithoutDocker = true)
class StageCostSummaryTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static CostLedger ledger;

    @BeforeAll
    static void setUp() {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        jdbc = JdbcClient.create(new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()));
        ledger = new CostLedger(jdbc);
    }

    private void spend(UUID v, String note, String inr) {
        ledger.record(v, "gemini", new BigDecimal(inr), BigDecimal.ONE, "tokens", true, note);
    }

    @Test
    void groupsCallsByPurposeInsideEachStageBiggestFirst() {
        UUID v = UUID.randomUUID();
        jdbc.sql("INSERT INTO video (id, prompt, status) VALUES (:id, 'p', 'RUNNING')").param("id", v).update();
        spend(v, "SHOT_PLAN (claude:claude-sonnet-5-5, 3489 in / 8000 out)", "32");
        spend(v, "SHOT_PLAN (claude:claude-sonnet-5-5, 3532 in / 8069 out)", "32");
        for (int i = 0; i < 3; i++) {
            spend(v, "DIAGRAM_SPEC (gemini-3.6-flash)", "2.5");
        }
        spend(v, "MUSIC (lyria-3.5)", "11.5");
        spend(v, "DRAFT clip shot 7 take 2 (wan)", "0");
        spend(v, "DRAFT clip shot 9 take 1 (wan)", "0");
        List<Map<String, Object>> s = ledger.stageSummary(v);
        assertEquals("SHOT_PLAN", s.get(0).get("stage"));
        assertEquals(5L, s.get(0).get("calls"));
        assertEquals(0, new BigDecimal("71.5").compareTo((BigDecimal) s.get(0).get("inr")));
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> purposes = (List<Map<String, Object>>) s.get(0).get("purposes");
        assertEquals(List.of("SHOT_PLAN", "DIAGRAM_SPEC"), purposes.stream().map(p -> p.get("purpose")).toList());
        assertEquals(2L, purposes.get(0).get("calls"));
        assertEquals(3L, purposes.get(1).get("calls"));
        Map<String, Object> draft = s.stream().filter(x -> x.get("stage").equals("DRAFT_GEN")).findFirst().orElseThrow();
        assertEquals(1, ((List<?>) draft.get("purposes")).size(), "shot and take numbers are folded into one purpose");
    }

    @Test
    void stageOfKnowsTheLedgerPurposes() {
        assertEquals("SHOT_PLAN", CostLedger.stageOf("DIAGRAM_SPEC_REPAIR"));
        assertEquals("RESEARCH", CostLedger.stageOf("RESEARCH backup"));
        assertEquals("PLAN", CostLedger.stageOf("PLAN attempt 2"));
        assertEquals("UPSCALE", CostLedger.stageOf("SMOOTH N clips to N fps"));
        assertEquals("OTHER", CostLedger.stageOf("canary rental N"));
    }
}
