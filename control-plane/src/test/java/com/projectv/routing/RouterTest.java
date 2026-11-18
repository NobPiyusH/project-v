package com.projectv.routing;

import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.BudgetGuard.BudgetExceededException;
import com.projectv.ledger.Pricing;
import com.projectv.routing.Router.Candidate;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
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
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** M3a router core against a real Postgres: preference, budget fallback, deferral, cooldowns, logging (rule 6). */
@Testcontainers(disabledWithoutDocker = true)
class RouterTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static Router router;
    static final String CAP = "video.i2v.final";
    UUID video;

    @BeforeAll
    static void setUp() throws Exception {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        jdbc = JdbcClient.create(new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()));
        Pricing pricing = new Pricing("../config");
        router = new Router(jdbc, JsonMapper.builder().build(), new BudgetGuard(jdbc, pricing), pricing);
    }

    @BeforeEach
    void reset() {
        jdbc.sql("DELETE FROM routing_decision").update();
        jdbc.sql("DELETE FROM provider_health").update();
        jdbc.sql("UPDATE provider SET owner_disabled = false").update();
        jdbc.sql("DELETE FROM cost_entry").update();
        provider("modal_wan", 6, "gpu", true, "[\"video.i2v.draft\",\"video.i2v.final\"]");
        provider("gemini_omni", 8, "gemini", true, "[\"video.i2v.draft\",\"video.i2v.final\"]");
        provider("other_video", 9, "none", false, "[\"video.i2v.final\"]");
        video = UUID.randomUUID();
        jdbc.sql("INSERT INTO video (id, prompt, status) VALUES (:id, 'router test', 'RUNNING')").param("id", video).update();
    }

    static void provider(String id, int quality, String budget, boolean enabled, String caps) {
        jdbc.sql("""
                        INSERT INTO provider (id, name, kind, capabilities_json, config_json, enabled)
                        VALUES (:id, :id, 'api', CAST(:caps AS jsonb), CAST(:cfg AS jsonb), :en)
                        ON CONFLICT (id) DO UPDATE SET capabilities_json = EXCLUDED.capabilities_json,
                            config_json = EXCLUDED.config_json, enabled = EXCLUDED.enabled
                        """).param("id", id).param("caps", caps)
                .param("cfg", "{\"quality\":" + quality + ",\"budget\":\"" + budget + "\"}").param("en", enabled).update();
    }

    static List<Candidate> both(int wanInr, int omniInr) {
        return List.of(new Candidate("modal_wan", BigDecimal.valueOf(wanInr)), new Candidate("gemini_omni", BigDecimal.valueOf(omniInr)));
    }

    int logged() {
        return jdbc.sql("SELECT count(*) FROM routing_decision WHERE video_id = :v").param("v", video).query(Integer.class).single();
    }

    @Test
    void ownersPreferenceWinsWhenItFitsAndTheDecisionIsLogged() {
        var c = router.choose(video, "FINAL_GEN", CAP, both(40, 90), "modal_wan", true);
        assertEquals("modal_wan", c.providerId(), "preferred over the higher quality tier");
        assertNotNull(c.decisionId());
        assertEquals(1, logged());
        String chosen = jdbc.sql("SELECT chosen_provider_id FROM routing_decision WHERE id = :id").param("id", c.decisionId())
                .query(String.class).single();
        assertEquals("modal_wan", chosen);
    }

    @Test
    void withoutAPreferenceTheHighestQualityEligibleProviderWins() {
        assertEquals("gemini_omni", router.choose(video, "FINAL_GEN", CAP, both(40, 90), null, true).providerId());
    }

    @Test
    void preferredProviderOverItsCapFallsBackToTheNextProvider() {
        var c = router.choose(video, "FINAL_GEN", CAP, both(40, 5000), "gemini_omni", true);
        assertEquals("modal_wan", c.providerId(), "Gemini cap (₹1000) can't take ₹5000: finals go to Wan");
        assertTrue(c.reason().contains("gemini cap"), c.reason());
    }

    @Test
    void nothingEligibleDefersWithReasonsAndLogsIt() {
        var e = assertThrows(BudgetExceededException.class,
                () -> router.choose(video, "FINAL_GEN", CAP, both(99_999, 99_999), "gemini_omni", true));
        assertTrue(e.getMessage().contains("modal_wan") && e.getMessage().contains("gemini_omni"), e.getMessage());
        assertEquals(1, jdbc.sql("SELECT count(*) FROM routing_decision WHERE video_id = :v AND chosen_provider_id IS NULL")
                .param("v", video).query(Integer.class).single(), "the deferral itself is a logged decision");
    }

    @Test
    void failingProviderCoolsDownAndSuccessClearsIt() {
        router.reportFailure("gemini_omni", "503 overloaded");
        assertEquals("modal_wan", router.choose(video, "FINAL_GEN", CAP, both(40, 90), "gemini_omni", false).providerId());
        router.reportSuccess("gemini_omni");
        assertEquals("gemini_omni", router.choose(video, "FINAL_GEN", CAP, both(40, 90), "gemini_omni", false).providerId());
    }

    @Test
    void repeatedFailuresGrowTheCooldownUpToThirtyMinutes() {
        for (int i = 0; i < 20; i++) {
            router.reportFailure("modal_wan", "timeout");
        }
        long sec = router.cooldownUntil("modal_wan").getEpochSecond() - java.time.Instant.now().getEpochSecond();
        assertTrue(sec > 25 * 60 && sec <= Router.MAX_COOLDOWN_SEC, "capped at 30 min, was " + sec);
    }

    @Test
    void disabledOrIncapableProvidersAreSkipped() {
        var c = router.choose(video, "FINAL_GEN", CAP, List.of(new Candidate("other_video", BigDecimal.ONE),
                new Candidate("modal_wan", BigDecimal.TEN), new Candidate("unknown", BigDecimal.ONE)), "other_video", true);
        assertEquals("modal_wan", c.providerId());
        assertTrue(c.considered().stream().anyMatch(r -> "disabled".equals(r.get("why"))));
        assertTrue(c.considered().stream().anyMatch(r -> "not in the provider registry".equals(r.get("why"))));
    }

    // ---- the scoring formula (owner, 18 Nov 2026): floor, then min(quality, enough) - lambda x cost

    @Test
    void draftsGoToTheCheapToolBecauseExtraQualityBuysNothing() {
        var c = router.choose(video, "DRAFT_GEN", "video.i2v.draft", both(3, 46), null, false);
        assertEquals("modal_wan", c.providerId(), c.reason());
    }

    @Test
    void finalsGoToTheOnlyProviderAboveTheFloor() {
        var c = router.choose(video, "FINAL_GEN", CAP, both(46, 46), null, false);
        assertEquals("gemini_omni", c.providerId(), "Wan (6) is below the finals floor (7)");
    }

    @Test
    void belowTheFloorIsANotedFallbackWhenNothingElseFits() {
        var c = router.choose(video, "FINAL_GEN", CAP, both(46, 5000), null, true);
        assertEquals("modal_wan", c.providerId());
        assertTrue(c.reason().contains("BELOW the quality floor"), c.reason());
    }

    @Test
    void aBudgetRunningLowTipsTheChoiceToTheCheaperGoodEnoughProvider() {
        provider("omni_premium", 9, "gemini", true, "[\"video.i2v.final\"]");
        provider("omni_lite", 8, "gemini", true, "[\"video.i2v.final\"]");
        var pair = List.of(new Candidate("omni_premium", BigDecimal.valueOf(40)), new Candidate("omni_lite", BigDecimal.valueOf(5)));
        assertEquals("omni_premium", router.choose(video, "FINAL_GEN", CAP, pair, null, false).providerId(),
                "full budget: quality wins (9 - 0.8 > 8 - 0.1)");
        jdbc.sql("INSERT INTO cost_entry (id, video_id, provider_id, amount_inr, units, unit_type, estimated, note) "
                + "VALUES (:id, :v, 'gemini_api', 900, 1, 'call', true, 'spent')")
                .param("id", UUID.randomUUID()).param("v", video).update();
        assertEquals("omni_lite", router.choose(video, "FINAL_GEN", CAP, pair, null, false).providerId(),
                "10% of the Gemini cap left: the cheaper good-enough tool wins");
    }

    @Test
    void aBatchWhereEveryJobFailedCoolsTheProviderAndAnySuccessClearsIt() {
        router.reportBatch("gemini_omni", 0, 3, "503 overloaded");
        assertNotNull(router.cooldownUntil("gemini_omni"));
        assertEquals("modal_wan", router.choose(video, "FINAL_GEN", CAP, both(40, 90), null, false).providerId());
        router.reportBatch("gemini_omni", 1, 2, "one failed");
        assertEquals("gemini_omni", router.choose(video, "FINAL_GEN", CAP, both(40, 90), null, false).providerId());
    }

    @Test
    void workerDecisionsAreLoggedToo() {
        router.recordExternal(video, "NARRATION", "tts.narration", "chatterbox", "the channel voice", null);
        assertEquals(1, logged());
    }

    @Test
    void ownerSwitchOffIsRespectedAndReported() {
        router.setOwnerDisabled("gemini_omni", true);
        var c = router.choose(video, "FINAL_GEN", CAP, both(40, 90), "gemini_omni", false);
        assertEquals("modal_wan", c.providerId());
        assertTrue(c.considered().stream().anyMatch(r -> "switched off by the owner".equals(r.get("why"))));
        assertTrue(router.providers().stream().anyMatch(p -> "gemini_omni".equals(p.get("id"))
                && Boolean.TRUE.equals(p.get("owner_disabled"))));
        router.setOwnerDisabled("gemini_omni", false);
        router.reportFailure("gemini_omni", "x");
        router.resetHealth("gemini_omni");
        assertEquals("gemini_omni", router.choose(video, "FINAL_GEN", CAP, both(40, 90), "gemini_omni", false).providerId());
        assertEquals(3, router.budgets().size());
    }

    @Test
    void previewDecisionsAreNotLogged() {
        router.choose(video, "FINAL_GEN", CAP, both(40, 90), null, false);
        assertEquals(0, logged());
    }
}
