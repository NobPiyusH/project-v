package com.projectv.production;

import com.projectv.agents.AgentTypes.PlannedShot;
import com.projectv.agents.AgentTypes.ShotPlan;
import com.projectv.agents.DirectorAgent;
import com.projectv.agents.Llm;
import com.projectv.assets.AssetStore;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import com.projectv.planner.StylePresets;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
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
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;

/** Re-running the Shot plan must not pay for the same diagram specs twice (flag projectv.cost.diagram-spec-cache). */
@Testcontainers(disabledWithoutDocker = true)
class DiagramSpecCacheTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static JsonMapper json;

    @BeforeAll
    static void setUp() {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        jdbc = JdbcClient.create(new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()));
        json = JsonMapper.builder().build();
    }

    private static DiagramSpecs.SceneSpec good() {
        return new DiagramSpecs.SceneSpec("t", 8.0,
                List.of(Map.of("id", "gun", "type", "box", "x", -4, "y", 0, "w", 2, "h", 1, "text", "Gun")),
                List.of(Map.of("at", 0.5, "dur", 1.0, "do", "show", "target", "gun")));
    }

    /** Two diagram shots on two lines; the second run re-words the descriptions (as a real re-plan does). */
    private static ShotPlan plan(String suffix) {
        return new ShotPlan(List.of(
                new PlannedShot("S", 0, 6, "wide", "static", "gun fires" + suffix, "k", "m", "hero", null, null, null, null, "diagram"),
                new PlannedShot("S", 1, 6, "wide", "static", "mask sorts" + suffix, "k", "m", "standard", null, null, null, null, "diagram")));
    }

    private UUID seed() {
        UUID video = UUID.randomUUID();
        Map<String, Object> p = Map.ofEntries(Map.entry("title", "TV"), Map.entry("summary", "s"), Map.entry("format", "long"),
                Map.entry("durationSec", 12), Map.entry("aspectRatio", "16:9"), Map.entry("outputResolution", "1080p"),
                Map.entry("stylePreset", "shorts_punchy"), Map.entry("tone", "calm"), Map.entry("language", "en"),
                Map.entry("needsResearch", false), Map.entry("narration", "full"), Map.entry("voice", ""), Map.entry("music", "support"),
                Map.entry("soundDesign", true), Map.entry("visualTypes", List.of()), Map.entry("shortsCount", 0),
                Map.entry("mustInclude", List.of()), Map.entry("rationale", ""));
        jdbc.sql("INSERT INTO video (id, prompt, status, plan_json) VALUES (:id, 'p', 'COMPLETED', CAST(:plan AS jsonb))")
                .param("id", video).param("plan", json.writeValueAsString(Map.of("plan", p))).update();
        jdbc.sql("INSERT INTO concept (id, video_id, big_idea, clock_definition, acts_json, version) VALUES (:id, :v, 'idea', CAST('{}' AS jsonb), CAST('[]' AS jsonb), 1)")
                .param("id", UUID.randomUUID()).param("v", video).update();
        ProductionService.StoredScript script = new ProductionService.StoredScript(1, List.of(
                new ProductionService.StoredLine(0, "Electrons are fired.", "A", "p", true, null, null, null, null, null, null, null, null, null, null),
                new ProductionService.StoredLine(1, "A mask sorts them.", "A", "p", true, null, null, null, null, null, null, null, null, null, null)), "Charon", "calm", "strings", null, null);
        jdbc.sql("INSERT INTO script (id, video_id, version, beat_sheet_json) VALUES (:id, :v, 1, CAST(:b AS jsonb))")
                .param("id", UUID.randomUUID()).param("v", video).param("b", json.writeValueAsString(script)).update();
        return video;
    }

    private ShotStage stage(DirectorAgent director, Llm llm, boolean cache) throws Exception {
        Pricing pricing = new Pricing("src/test/resources/config");
        var deps = new ProductionDeps(jdbc, json, null, director, new AssetStore(jdbc), new CostLedger(jdbc), pricing,
                new BudgetGuard(jdbc, pricing), new StylePresets("src/test/resources/config"), "gemini-3.6-flash", "tts", "img",
                "music", "Charon", "app", "cls");
        DiagramSpecs specs = new DiagramSpecs(llm, json, true);
        specs.setCache(cache);
        ShotStage s = new ShotStage(deps);
        s.setDiagramSpecs(specs);
        return s;
    }

    private static Llm llm() {
        Llm llm = Mockito.mock(Llm.class);
        Mockito.when(llm.call(any(), anyString(), anyString(), anyString(), eq(DiagramSpecs.SceneSpec.class))).thenReturn(good());
        return llm;
    }

    @Test
    void secondShotPlanReusesPaidSpecsWhenCacheIsOn() throws Exception {
        UUID video = seed();
        DirectorAgent director = Mockito.mock(DirectorAgent.class);
        Mockito.when(director.plan(any(), any(), any(), any(), Mockito.anyDouble(), Mockito.anyDouble(), any()))
                .thenReturn(plan("")).thenReturn(plan(" (re-worded)"));
        Llm llm = llm();
        ShotStage st = stage(director, llm, true);
        st.shotPlan(video, null);
        var again = st.shotPlan(video, null);
        Mockito.verify(llm, Mockito.times(2)).call(any(), anyString(), anyString(), anyString(), eq(DiagramSpecs.SceneSpec.class));
        assertTrue(again.summary().contains("2 diagram specs reused"), again.summary());
        assertEquals(2L, jdbc.sql("SELECT count(*) FROM shot s JOIN scene c ON c.id = s.scene_id WHERE c.video_id = :v AND jsonb_exists(s.prompt_json, 'diagramSpec')")
                .param("v", video).query(Long.class).single());
    }

    @Test
    void cacheOffKeepsPayingEveryRun() throws Exception {
        UUID video = seed();
        DirectorAgent director = Mockito.mock(DirectorAgent.class);
        Mockito.when(director.plan(any(), any(), any(), any(), Mockito.anyDouble(), Mockito.anyDouble(), any())).thenReturn(plan(""));
        Llm llm = llm();
        ShotStage st = stage(director, llm, false);
        st.shotPlan(video, null);
        st.shotPlan(video, null);
        Mockito.verify(llm, Mockito.times(4)).call(any(), anyString(), anyString(), anyString(), eq(DiagramSpecs.SceneSpec.class));
    }

    @Test
    void keyIgnoresDescriptionButNotLineOrLength() {
        assertEquals(DiagramSpecs.cacheKey("TV", "line", 6.2), DiagramSpecs.cacheKey("TV", "line", 7.0), "both round up to 7 s");
        assertNotEquals(DiagramSpecs.cacheKey("TV", "line", 6.2), DiagramSpecs.cacheKey("TV", "other", 6.2));
        assertNotEquals(DiagramSpecs.cacheKey("TV", "line", 6.2), DiagramSpecs.cacheKey("TV", "line", 9));
    }
}
