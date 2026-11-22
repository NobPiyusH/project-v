package com.projectv.production;

import com.projectv.agents.AgentTypes.PlannedShot;
import com.projectv.assets.AssetStore;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import com.projectv.media.MediaTypes.StockProvenance;
import com.projectv.media.MediaTypes.StockResult;
import com.projectv.planner.StylePresets;
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

import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Stock footage lane (default off): routing, fallback to the AI lane, provenance and credits. Real Postgres, no network. */
@Testcontainers(disabledWithoutDocker = true)
class StockLaneTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static JsonMapper json;
    static ProductionDeps deps;
    UUID video;
    UUID scene;

    @BeforeAll
    static void setUp() throws Exception {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        jdbc = JdbcClient.create(new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()));
        json = JsonMapper.builder().build();
        Pricing pricing = new Pricing("src/test/resources/config");
        deps = new ProductionDeps(jdbc, json, null, null, new AssetStore(jdbc), new CostLedger(jdbc), pricing,
                new BudgetGuard(jdbc, pricing), new StylePresets("src/test/resources/config"), "gemini-3.6-flash", "tts", "img",
                "music", "Charon", "app", "cls");
    }

    @BeforeEach
    void seed() {
        video = UUID.randomUUID();
        scene = UUID.randomUUID();
        Map<String, Object> plan = Map.ofEntries(Map.entry("title", "TV"), Map.entry("summary", "s"), Map.entry("format", "long"),
                Map.entry("durationSec", 180), Map.entry("aspectRatio", "16:9"), Map.entry("outputResolution", "1080p"),
                Map.entry("stylePreset", "shorts_punchy"), Map.entry("tone", "calm"), Map.entry("language", "en"),
                Map.entry("needsResearch", false), Map.entry("narration", "full"), Map.entry("voice", ""), Map.entry("music", "support"),
                Map.entry("soundDesign", true), Map.entry("visualTypes", List.of()), Map.entry("shortsCount", 0),
                Map.entry("mustInclude", List.of()), Map.entry("rationale", ""));
        jdbc.sql("INSERT INTO video (id, prompt, status, plan_json) VALUES (:id, 'p', 'COMPLETED', CAST(:plan AS jsonb))")
                .param("id", video).param("plan", json.writeValueAsString(Map.of("plan", plan))).update();
        jdbc.sql("INSERT INTO scene (id, video_id, ord, title) VALUES (:s, :v, 0, 'S')").param("s", scene).param("v", video).update();
    }

    private UUID shot(int ord, String promptJson) {
        UUID id = UUID.randomUUID();
        jdbc.sql("""
                INSERT INTO shot (id, scene_id, ord, duration_target_sec, description, prompt_json)
                VALUES (:id, :s, :o, 5, 'a living room with a lit television', CAST(:p AS jsonb))
                """).param("id", id).param("s", scene).param("o", ord).param("p", promptJson).update();
        return id;
    }

    private static final String STOCK = "{\"lane\":\"stock\",\"stockQuery\":\"living room lit television\",\"keyframePrompt\":\"k\"}";

    private StockStage stage(boolean lane, boolean pexels, boolean commons) {
        StockStage s = new StockStage(deps);
        s.configure(lane, pexels, false, commons);
        return s;
    }

    private static StockResult hit(String source, String licence, boolean credit, String attribution) {
        return new StockResult(true, null, List.of("living room tv"), List.of(), "s3://b/stock.mp4", 1920, 1080, 5.0,
                new StockProvenance(source, "id-1", "https://page/1", "Jane Roe", licence, "https://lic", attribution, credit,
                        "2026-11-22T10:00:00+00:00"));
    }

    @Test
    void shotPlanMarksStockOnlyWhenTheSwitchIsOnAndDefaultStaysAi() {
        PlannedShot s = new PlannedShot("S", 0, 4, "wide", "static", "d", "k", "m", "standard", null, null, null, null, "stock");
        assertTrue(s.stock());
        assertFalse(new PlannedShot("S", 0, 4, "wide", "static", "d", "k", "m", "standard", null, null, null, null, null).stock(),
                "absent lane = AI video, unchanged");
    }

    @Test
    void switchesOffMeanNoRequests() {
        shot(0, STOCK);
        assertTrue(stage(false, true, true).requests(video).isEmpty(), "master switch off");
        assertTrue(stage(true, false, false).requests(video).isEmpty(), "no source enabled");
        var req = stage(true, true, true).requests(video);
        assertEquals(1, req.size());
        assertEquals("living room lit television", req.getFirst().description());
        assertEquals(List.of("pexels", "commons"), req.getFirst().sources());
        assertEquals(5.0, req.getFirst().durationSec());
    }

    @Test
    void stockShotsAreRoutedAwayFromKeyframesAndAiClips() {
        UUID a = shot(0, STOCK);
        shot(1, "{\"motionPrompt\":\"push\"}");
        var rows = new ShotStage(deps).shots(video);
        assertEquals(1, rows.stream().filter(StageSupport::stockShot).count());
        assertEquals(1, stage(true, true, false).requests(video).size());
        assertEquals(a.toString(), stage(true, true, false).requests(video).getFirst().shotId());
    }

    @Test
    void foundClipIsStoredWithProvenanceAndUsedByTheShot() {
        UUID a = shot(0, STOCK);
        StockStage s = stage(true, true, false);
        var req = s.requests(video).getFirst();
        s.save(video, req, hit("pexels", "Pexels License", true, "Video by Jane Roe on Pexels (https://page/1)"));
        var row = jdbc.sql("""
                SELECT p.provider, p.source_url, p.source_id, p.author, p.attribution_text, p.credit_required, p.fetched_at, l.name AS licence
                FROM shot sh JOIN asset a ON a.id = sh.footage_asset_id JOIN provenance p ON p.id = a.provenance_id
                JOIN licence l ON l.id = p.licence_id WHERE sh.id = :s
                """).param("s", a).query().singleRow();
        assertEquals("pexels", row.get("provider"));
        assertEquals("https://page/1", row.get("source_url"));
        assertEquals("Jane Roe", row.get("author"));
        assertEquals("Pexels License", row.get("licence"));
        assertEquals(true, row.get("credit_required"));
        assertTrue(row.get("fetched_at") != null);
        assertEquals(0, s.requests(video).size(), "already has its clip");
        assertEquals("READY", jdbc.sql("SELECT clip_status FROM shot WHERE id = :s").param("s", a).query(String.class).single());
    }

    @Test
    void noMatchRevertsTheShotToTheAiLaneAndNeverFails() {
        UUID a = shot(0, STOCK);
        StockStage s = stage(true, true, false);
        var res = s.save(video, s.requests(video).getFirst(),
                new StockResult(false, "no candidates", List.of("q"), List.of("pexels: no PEXELS_API_KEY"), null, null, null, null, null));
        assertTrue(res.summary().contains("AI lane"));
        String prompt = jdbc.sql("SELECT prompt_json::text FROM shot WHERE id = :s").param("s", a).query(String.class).single();
        assertTrue(prompt.contains("\"ai_video\"") && prompt.contains("no candidates"));
        assertFalse(StageSupport.stockShot(Map.of("prompt_json", prompt)), "keyframes will now be made for it");
        assertEquals(0, s.requests(video).size());
    }

    @Test
    void creditsListOnlyClipsThatNeedCredit() {
        shot(0, STOCK);
        shot(1, STOCK);
        shot(2, STOCK);
        StockStage s = stage(true, true, true);
        var reqs = s.requests(video);
        s.save(video, reqs.get(0), hit("commons", "CC BY 4.0", true, "\"Old TV\" by Jane Roe, CC BY 4.0 (https://lic), via Wikimedia Commons: https://page/1; trimmed"));
        s.save(video, reqs.get(1), hit("commons", "Public domain", false, "PD clip"));
        s.save(video, reqs.get(2), hit("pixabay", "Pixabay Content License", false, "Video by X on Pixabay"));
        List<String> credits = new AssemblyStage(deps).stockCredits(video);
        assertEquals(1, credits.size());
        assertTrue(credits.getFirst().contains("Jane Roe") && credits.getFirst().contains("CC BY 4.0"));
    }
}
