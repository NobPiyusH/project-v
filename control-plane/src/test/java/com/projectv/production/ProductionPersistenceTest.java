package com.projectv.production;

import com.projectv.assets.AssetStore;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import com.projectv.media.MediaTypes.NarrationClip;
import com.projectv.media.MediaTypes.NarrationResult;
import com.projectv.media.MediaTypes.Usage;
import com.projectv.planner.StylePresets;
import com.projectv.production.ProductionService.StoredLine;
import com.projectv.production.ProductionService.StoredScript;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;
import tools.jackson.databind.json.JsonMapper;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Real SQL against a real (throwaway) Postgres with every migration applied: the parts the workflow tests fake out.
 * Skipped automatically when Docker isn't running.
 */
@Testcontainers(disabledWithoutDocker = true)
class ProductionPersistenceTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static JsonMapper json;
    static VoiceStage voice;
    static ShotStage shots;
    static Inspector inspector;
    static CreativeDirector creative;
    static BudgetGuard budget;

    UUID video;

    @BeforeAll
    static void setUp() throws Exception {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        var ds = new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword());
        jdbc = JdbcClient.create(ds);
        json = JsonMapper.builder().build();
        Pricing pricing = new Pricing("../config");
        budget = new BudgetGuard(jdbc, pricing);
        var deps = new ProductionDeps(jdbc, json, null, null, new AssetStore(jdbc), new CostLedger(jdbc), pricing, budget,
                new StylePresets("../config"), "gemini-3.6-flash", "gemini-3.8-flash-tts", "img", "music", "Charon", "app", "cls");
        voice = new VoiceStage(deps);
        shots = new ShotStage(deps);
        inspector = new Inspector(deps);
        creative = new CreativeDirector(deps, null, "gemini-3.6-flash");
    }

    @BeforeEach
    void seedVideo() {
        video = UUID.randomUUID();
        Map<String, Object> plan = Map.ofEntries(Map.entry("title", "Test"), Map.entry("summary", "s"),
                Map.entry("format", "short"), Map.entry("durationSec", 60), Map.entry("aspectRatio", "9:16"),
                Map.entry("outputResolution", "1080p"), Map.entry("stylePreset", "shorts_punchy"), Map.entry("tone", "calm"),
                Map.entry("language", "en"), Map.entry("needsResearch", false), Map.entry("narration", "full"),
                Map.entry("voice", ""), Map.entry("music", "support"), Map.entry("soundDesign", true),
                Map.entry("visualTypes", List.of()), Map.entry("shortsCount", 0), Map.entry("mustInclude", List.of()),
                Map.entry("rationale", ""));
        jdbc.sql("INSERT INTO video (id, prompt, status, plan_json) VALUES (:id, 'p', 'COMPLETED', CAST(:plan AS jsonb))")
                .param("id", video).param("plan", json.writeValueAsString(Map.of("plan", plan))).update();
        StoredScript script = new StoredScript(1, List.of(new StoredLine(0, "A star is born.", "Act 1", "hook", true),
                new StoredLine(1, "Silence.", "Act 1", "beat", false)), "Charon", "calm", "strings", null, null);
        jdbc.sql("INSERT INTO script (id, video_id, version, beat_sheet_json) VALUES (:id, :v, 1, CAST(:b AS jsonb))")
                .param("id", UUID.randomUUID()).param("v", video).param("b", json.writeValueAsString(script)).update();
    }

    private StoredLine line(int index) {
        String s = jdbc.sql("SELECT beat_sheet_json::text FROM script WHERE video_id = :v").param("v", video).query(String.class).single();
        return json.readValue(s, StoredScript.class).lines().stream().filter(l -> l.index() == index).findFirst().orElseThrow();
    }

    private static NarrationResult take(String uri, double seconds) {
        return new NarrationResult(List.of(new NarrationClip(0, uri, seconds, new Usage(10, 1000, 0, 1010), 1, true, "coverage 100%")),
                "gemini-3.8-flash-tts", "Charon");
    }

    @Test
    void inspectorFindsTheMissingVoiceAndStoresOneReportPerAttempt() {
        // The seeded script's spoken line has no recording yet.
        var report = inspector.evaluate(video, com.projectv.workflow.Stage.NARRATION, 1,
                com.projectv.media.MediaTypes.MediaInspectResult.NONE);
        assertEquals("FAIL", report.verdict());
        assertTrue(report.notes().contains("line 1: This line has no voice recording."), report.notes());
        // Re-inspecting the same attempt replaces the row; a new attempt adds one.
        inspector.evaluate(video, com.projectv.workflow.Stage.NARRATION, 1, null);
        voice.saveNarration(video, take("s3://projectv/l0.wav", 2.0));
        var second = inspector.evaluate(video, com.projectv.workflow.Stage.NARRATION, 2,
                new com.projectv.media.MediaTypes.MediaInspectResult(List.of(new com.projectv.media.MediaTypes.Finding(
                        "WARN", "CLIPPING", "Peak -0.1 dBTP", "line 1")), List.of("line 1")));
        assertEquals("WARN", second.verdict());
        assertEquals(List.of("FAIL", "WARN"), jdbc.sql("SELECT verdict FROM inspection WHERE video_id = :v ORDER BY attempt")
                .param("v", video).query(String.class).list());
    }

    @Test
    void allMigrationsApplyAndLicencesAreSeeded() {
        assertEquals(3, jdbc.sql("SELECT count(*) FROM licence").query(Integer.class).single());
        assertEquals(6, jdbc.sql("SELECT count(*) FROM flyway_schema_history WHERE success").query(Integer.class).single());
    }

    @Test
    void editVoiceAndSwitchTakesRoundTripThroughTheDatabase() {
        StoredLine edited = voice.editLine(video, 0, "A star ignites.", null);
        assertTrue(edited.edited());
        assertEquals(1, jdbc.sql("SELECT count(*) FROM preference_event WHERE video_id = :v AND kind = 'edit_diff'")
                .param("v", video).query(Integer.class).single(), "the edit is kept as taste-learning data");

        voice.saveLineNarration(video, take("s3://b/t1.wav", 4.0), "in a calm voice");
        StoredLine first = line(0);
        assertNotNull(first.audioAssetId());
        assertFalse(first.needsVoice());
        assertEquals("READY", first.voiceStatus());

        voice.saveLineNarration(video, take("s3://b/t2.wav", 5.0), null);
        StoredLine second = line(0);
        assertEquals(2, second.audioVersions().size());
        voice.selectLineVoice(video, 0, first.audioAssetId());
        assertEquals(first.audioAssetId(), line(0).audioAssetId(), "switched back to the first take");

        // Every take: an asset + provenance with a licence, and a priced cost entry.
        assertEquals(2, jdbc.sql("""
                        SELECT count(*) FROM asset a JOIN provenance p ON p.id = a.provenance_id
                        WHERE a.video_id = :v AND a.kind = 'audio' AND p.licence_id IS NOT NULL AND p.asset_id = a.id
                        """).param("v", video).query(Integer.class).single());
        BigDecimal spent = jdbc.sql("SELECT sum(amount_inr) FROM cost_entry WHERE video_id = :v").param("v", video)
                .query(BigDecimal.class).single();
        assertTrue(spent.signum() > 0);
        assertThrows(IllegalStateException.class, () -> voice.selectLineVoice(video, 0, UUID.randomUUID()));
    }

    @Test
    void creativeDirectorKeepsItsFixAsASuggestionUntilTheShotIsRedone() {
        UUID scene = UUID.randomUUID();
        UUID shot = UUID.randomUUID();
        jdbc.sql("INSERT INTO scene (id, video_id, ord, title) VALUES (:s, :v, 0, 'S')").param("s", scene).param("v", video).update();
        jdbc.sql("""
                        INSERT INTO shot (id, scene_id, ord, prompt_json)
                        VALUES (:id, :s, 0, CAST('{"keyframePrompt":"a flashlight beam","lineIndex":0}' AS jsonb))
                        """).param("id", shot).param("s", scene).update();
        var review = new com.projectv.agents.AgentTypes.CreativeReview(
                new com.projectv.agents.AgentTypes.VideoCritique(4, 4, 4, 4, "", ""), List.of(),
                List.of(new com.projectv.agents.AgentTypes.ShotCritique(1, 1, 4, 4, true, "a torch, not starlight",
                        "ancient light leaving a distant galaxy")));
        var findings = creative.saveImageReview(video, 1, new com.projectv.media.MediaTypes.ImageReviewResult(review,
                "gemini-3.6-flash", new Usage(5000, 800, 0, 5800), 1));
        assertEquals(1, findings.size());
        assertEquals(shot.toString(), findings.getFirst().targetId());
        String prompt = jdbc.sql("SELECT prompt_json::text FROM shot WHERE id = :s").param("s", shot).query(String.class).single();
        assertTrue(prompt.contains("\"keyframePrompt\": \"a flashlight beam\""), "not changed before the redo: " + prompt);
        assertTrue(prompt.contains("suggestedPrompt"));
        assertEquals(1, jdbc.sql("SELECT count(*) FROM creative_review WHERE video_id = :v").param("v", video)
                .query(Integer.class).single());
        assertTrue(jdbc.sql("SELECT count(*) FROM cost_entry WHERE video_id = :v AND note LIKE 'CREATIVE_REVIEW%'")
                .param("v", video).query(Integer.class).single() == 1);

        creative.applySuggestedPrompts(video, List.of(shot));
        prompt = jdbc.sql("SELECT prompt_json::text FROM shot WHERE id = :s").param("s", shot).query(String.class).single();
        assertTrue(prompt.contains("\"keyframePrompt\": \"ancient light leaving a distant galaxy\""), prompt);
        assertTrue(prompt.contains("\"originalPrompt\": \"a flashlight beam\""), prompt);
        assertFalse(prompt.contains("suggestedPrompt"), prompt);
    }

    @Test
    void aShotOnlyAcceptsItsOwnKeyframeVersions() {
        UUID scene = UUID.randomUUID();
        UUID shot = UUID.randomUUID();
        jdbc.sql("INSERT INTO scene (id, video_id, ord, title) VALUES (:s, :v, 0, 'S')").param("s", scene).param("v", video).update();
        jdbc.sql("INSERT INTO shot (id, scene_id, ord, prompt_json) VALUES (:id, :s, 0, CAST('{}' AS jsonb))")
                .param("id", shot).param("s", scene).update();
        UUID mine = UUID.randomUUID();
        UUID other = UUID.randomUUID();
        for (UUID a : List.of(mine, other)) {
            jdbc.sql("INSERT INTO asset (id, video_id, kind, uri_hot, shot_id) VALUES (:a, :v, 'image', 's3://b/x.png', :s)")
                    .param("a", a).param("v", video).param("s", a.equals(mine) ? shot : null).update();
        }
        shots.selectKeyframe(shot, mine);
        assertEquals(mine, jdbc.sql("SELECT keyframe_asset_id FROM shot WHERE id = :s").param("s", shot).query(UUID.class).single());
        assertThrows(IllegalStateException.class, () -> shots.selectKeyframe(shot, other));
        assertEquals(1, shots.shotVersions(shot).size());
    }

    @Test
    void theMonthlyGeminiCapStopsSpending() throws Exception {
        BigDecimal cap = new Pricing("../config").geminiMonthlyCapInr();
        BigDecimal before = budget.geminiSpentThisMonth();
        jdbc.sql("INSERT INTO cost_entry (id, video_id, provider_id, amount_inr) VALUES (:id, :v, 'gemini_api', :amt)")
                .param("id", UUID.randomUUID()).param("v", video).param("amt", cap.subtract(before).subtract(BigDecimal.valueOf(5)))
                .update();
        assertTrue(budget.canSpend(BigDecimal.valueOf(4)));
        assertFalse(budget.canSpend(BigDecimal.valueOf(6)));
        assertThrows(BudgetGuard.BudgetExceededException.class, () -> budget.require(BigDecimal.TEN, "test"));
        jdbc.sql("DELETE FROM cost_entry WHERE video_id = :v AND provider_id = 'gemini_api' AND amount_inr > 5").param("v", video).update();
    }
}
