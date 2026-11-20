package com.projectv.production;

import com.projectv.assets.AssetStore;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import com.projectv.planner.StylePresets;
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
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** The money caps really stop spending (cap values come from config/providers.yaml and are not changed here). */
@Testcontainers(disabledWithoutDocker = true)
class MoneyCapsTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static Pricing pricing;
    static BudgetGuard budget;
    static ProducerService producer;

    @BeforeAll
    static void setUp() throws Exception {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        jdbc = JdbcClient.create(new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()));
        var json = JsonMapper.builder().build();
        pricing = new Pricing("src/test/resources/config");
        budget = new BudgetGuard(jdbc, pricing);
        var deps = new ProductionDeps(jdbc, json, null, null, new AssetStore(jdbc), new CostLedger(jdbc), pricing, budget,
                new StylePresets("src/test/resources/config"), "gemini-3.6-flash", "gemini-3.8-flash-tts", "img", "music", "Charon", "app", "cls");
        producer = new ProducerService(deps, "gemini-3.6-flash");
    }

    static UUID shortVideo() {
        UUID id = UUID.randomUUID();
        Map<String, Object> plan = Map.ofEntries(Map.entry("title", "T"), Map.entry("summary", "s"), Map.entry("format", "short"),
                Map.entry("durationSec", 60), Map.entry("aspectRatio", "9:16"), Map.entry("outputResolution", "1080p"),
                Map.entry("stylePreset", "shorts_punchy"), Map.entry("tone", "calm"), Map.entry("language", "en"),
                Map.entry("needsResearch", false), Map.entry("narration", "full"), Map.entry("voice", ""), Map.entry("music", "support"),
                Map.entry("soundDesign", true), Map.entry("visualTypes", List.of()), Map.entry("shortsCount", 0),
                Map.entry("mustInclude", List.of()), Map.entry("rationale", ""));
        jdbc.sql("INSERT INTO video (id, prompt, status, plan_json) VALUES (:id, 'p', 'COMPLETED', CAST(:plan AS jsonb))")
                .param("id", id).param("plan", JsonMapper.builder().build().writeValueAsString(Map.of("plan", plan))).update();
        return id;
    }

    static void spend(UUID video, String provider, BigDecimal inr) {
        jdbc.sql("INSERT INTO cost_entry (id, video_id, provider_id, amount_inr) VALUES (:id, :v, :p, :a)")
                .param("id", UUID.randomUUID()).param("v", video).param("p", provider).param("a", inr).update();
    }

    @Test
    void geminiCapIsOneThousandRupeesAndRefusesAnythingThatWouldBreakIt() {
        assertEquals(0, new BigDecimal("1000").compareTo(pricing.geminiMonthlyCapInr()));
        UUID v = shortVideo();
        spend(v, BudgetGuard.GEMINI, pricing.geminiMonthlyCapInr().subtract(budget.geminiSpentThisMonth()).subtract(BigDecimal.TEN));
        assertTrue(budget.canSpend(BigDecimal.TEN), "exactly what is left still fits");
        assertFalse(budget.canSpend(new BigDecimal("10.01")));
        assertThrows(BudgetGuard.BudgetExceededException.class, () -> budget.require(new BigDecimal("11"), "test"));
        spend(v, BudgetGuard.GEMINI, BigDecimal.TEN);
        assertEquals(0.0, budget.remainingShare("gemini"), "nothing left at the cap");
        jdbc.sql("DELETE FROM cost_entry WHERE video_id = :v").param("v", v).update();
    }

    @Test
    void gpuCapStopsModalJobsOnceTheMonthIsUsedUp() {
        BigDecimal cap = pricing.gpuMonthlyCapInr();
        UUID v = shortVideo();
        spend(v, "modal_wan", cap.subtract(budget.gpuSpentThisMonth()).subtract(new BigDecimal("5")));
        budget.requireGpu(new BigDecimal("4"), "a clip");
        BudgetGuard.BudgetExceededException e = assertThrows(BudgetGuard.BudgetExceededException.class,
                () -> budget.requireGpu(new BigDecimal("6"), "a clip"));
        assertTrue(e.getMessage().contains("GPU cap"));
        spend(v, "modal_zimage", new BigDecimal("5"));
        assertThrows(BudgetGuard.BudgetExceededException.class, () -> budget.requireGpu(new BigDecimal("0.5"), "a picture"));
        assertEquals(0.0, budget.remainingShare("gpu"));
        jdbc.sql("DELETE FROM cost_entry WHERE video_id = :v").param("v", v).update();
    }

    @Test
    void producerApprovesUpToFiftyAndAsksAboveItOrOverTheHundredRupeeShortBudget() {
        assertEquals(0, new BigDecimal("100").compareTo(pricing.producerBudgetInr(true)));
        assertEquals(0, new BigDecimal("50").compareTo(pricing.producerAskAboveInr()));
        UUID v = shortVideo();
        assertTrue(producer.spendAdvice(v, new BigDecimal("50")).contains("would APPROVE"));
        assertTrue(producer.spendAdvice(v, new BigDecimal("50.01")).contains("would ASK YOU"), "above 50 goes to the owner");

        spend(v, BudgetGuard.GEMINI, new BigDecimal("60"));
        assertTrue(producer.spendAdvice(v, new BigDecimal("40")).contains("would APPROVE"), "60 + 40 = exactly the 100 budget");
        assertTrue(producer.spendAdvice(v, new BigDecimal("41")).contains("would ASK YOU"), "over the 100 budget");
    }

    @Test
    void producerStopsRedoingOnceTheShortHasUsedItsBudget() {
        UUID v = shortVideo();
        spend(v, BudgetGuard.GEMINI, new BigDecimal("99"));
        assertTrue(producer.mayRedo(v, 1));
        assertFalse(producer.mayRedo(v, 99), "rounds are limited too");
        spend(v, BudgetGuard.GEMINI, BigDecimal.ONE);
        assertFalse(producer.mayRedo(v, 1), "100 spent: no more redos");
    }
}
