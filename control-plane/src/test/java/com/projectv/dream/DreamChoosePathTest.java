package com.projectv.dream;

import com.projectv.agents.Llm;
import com.projectv.assets.AssetStore;
import com.projectv.dream.DreamBranches.PathPick;
import com.projectv.dream.DreamStudio.NewDream;
import com.projectv.dream.DreamTypes.DreamWorld;
import com.projectv.dream.DreamTypes.PlannedDreamShot;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.Pricing;
import com.projectv.routing.Router;
import com.projectv.video.ClipProgress;
import io.temporal.testing.TestWorkflowEnvironment;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
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
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.clearInvocations;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * "Choose the path" against a real Postgres with a fake writer: off by default; on, the writer is asked for two paths, the
 * Finish gets the end card, a finished Short keeps its branch memory, and "Continue this dream" starts episode 2 from the
 * parent's last frame in the same world. No GPU, no Gemini, no spend (the started workflows have no worker and just wait).
 */
@Testcontainers(disabledWithoutDocker = true)
class DreamChoosePathTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static Llm llm;
    static DreamService dreams;
    static DreamStudio studio;
    static TestWorkflowEnvironment env;

    @BeforeAll
    static void setUp() throws Exception {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        jdbc = JdbcClient.create(new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()));
        Pricing pricing = new Pricing("src/test/resources/config");
        BudgetGuard budget = new BudgetGuard(jdbc, pricing);
        var json = JsonMapper.builder().build();
        llm = mock(Llm.class);
        dreams = new DreamService(jdbc, json, llm, new AssetStore(jdbc), pricing, budget, new Router(jdbc, json, budget, pricing),
                new ClipProgress(), new DreamSettings("projectv-zimage", "ZImage", "projectv-wan", "Wan", "http://127.0.0.1:8080",
                BigDecimal.ZERO, 3.5, "", "lyria-3.5", "s3://projectv/music-beds/x.m4a", "gemini-3.6-flash",
                "claude:claude-sonnet-5-5", true));
        env = TestWorkflowEnvironment.newInstance();
        studio = new DreamStudio(jdbc, env.getWorkflowClient(), json, dreams, new ClipProgress(), mock(DreamProducer.class));
    }

    @AfterAll
    static void tearDown() {
        env.close();
    }

    static NewDream form(boolean pathChoice) {
        return new NewDream("a glass city", "lonely", 3, "480p", "REVIEW", null, 10, "bed", false, true, true, true, null,
                null, null, null, true, null, null, null, null, pathChoice);
    }

    static DreamWorld world(List<String> paths) {
        return new DreamWorld("Glass City", "a city of twisted glass", "teal and amber", "blue hour", "a red kiosk",
                List.of(new PlannedDreamShot("s1", "m1"), new PlannedDreamShot("s2", "m2"),
                        new PlannedDreamShot("an arch, a door on the left, stairs on the right", "m3")), null, null, paths);
    }

    /** The writer's prompts (system, user). */
    static String[] write(UUID id, List<String> paths) {
        clearInvocations(llm);
        when(llm.call(any(), eq("dream.write"), any(), any(), eq(DreamWorld.class))).thenReturn(world(paths));
        dreams.writeShots(id, null);
        ArgumentCaptor<String> sys = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<String> user = ArgumentCaptor.forClass(String.class);
        verify(llm).call(any(), eq("dream.write"), sys.capture(), user.capture(), eq(DreamWorld.class));
        return new String[]{sys.getValue(), user.getValue()};
    }

    static UUID asset(String uri) {
        boolean video = uri.endsWith(".mp4");
        return new AssetStore(jdbc).create(new AssetStore.NewAsset(null, video ? "video" : "image", uri,
                video ? "video/mp4" : "image/png", 480, 832, null, "test", "m", "p", 1L, AssetStore.LICENCE_ZIMAGE, true));
    }

    static Map<String, Object> row(UUID id) {
        return jdbc.sql("SELECT * FROM dream_short WHERE id = :d").param("d", id).query().singleRow();
    }

    /** Pictures and clips for every shot, then a finished Short with its branch memory, as the real steps would leave it. */
    static void finish(UUID id) {
        var shots = jdbc.sql("SELECT id FROM dream_shot WHERE dream_short_id = :d ORDER BY ord").param("d", id).query(UUID.class).list();
        for (UUID s : shots) {
            jdbc.sql("UPDATE dream_shot SET picture_asset_id = :p, clip_asset_id = :c WHERE id = :s")
                    .param("p", asset("s3://b/p-" + s + ".png")).param("c", asset("s3://b/c-" + s + ".mp4")).param("s", s).update();
        }
        jdbc.sql("UPDATE dream_short SET final_asset_id = :a, status = 'COMPLETED' WHERE id = :d")
                .param("a", asset("s3://b/final-" + id + ".mp4")).param("d", id).update();
        dreams.saveBranch(id);
    }

    @Test
    void offByDefaultNothingChanges() {
        UUID id = studio.create(form(false));
        assertEquals(false, row(id).get("path_choice"));
        assertEquals(1, row(id).get("episode"));
        assertFalse(write(id, List.of("the door", "the stairs"))[0].contains("\"paths\""), "the writer is not asked");
        assertNull(row(id).get("path_a"), "options the Short did not ask for are never stored");
        finish(id);
        assertNull(dreams.finishRequest(id).pathA(), "no end card");
        assertNull(row(id).get("branch_json"));
        assertThrows(IllegalStateException.class, () -> studio.continueDream(id, PathPick.owner("A")));
    }

    @Test
    void onTheWriterWritesTwoPathsTheOwnerCanEditAndTheFinishBurnsThem() {
        UUID id = studio.create(form(true));
        assertTrue(write(id, List.of("\"the door\"", "the stairs"))[0].contains("\"paths\""));
        assertEquals("the door", row(id).get("path_a"));
        assertEquals("the stairs", row(id).get("path_b"));

        studio.setPaths(id, "the red door", "the long stairs"); // the owner's edit in review/manual
        write(id, List.of("x", "y"));
        assertEquals("the red door", row(id).get("path_a"), "a rewrite never replaces the owner's options");
        assertThrows(IllegalArgumentException.class, () -> dreams.setPaths(id, "ok", "  "));

        finish(id);
        var fin = dreams.finishRequest(id);
        assertEquals("the red door", fin.pathA());
        assertEquals("the long stairs", fin.pathB());
        assertTrue(fin.keepOrder(), "the card asks about the LAST shot, so pacing never reshuffles it");
    }

    @Test
    void continueStartsEpisodeTwoFromTheParentsLastFrameInTheSameWorld() {
        UUID parent = studio.create(form(true));
        write(parent, List.of("the door", "the stairs"));
        assertThrows(IllegalStateException.class, () -> studio.continueDream(parent, PathPick.owner("B")), "not finished yet");
        finish(parent);
        var lastClip = jdbc.sql("SELECT clip_asset_id FROM dream_shot WHERE dream_short_id = :d ORDER BY ord DESC LIMIT 1")
                .param("d", parent).query(UUID.class).single();
        String branch = row(parent).get("branch_json").toString();
        assertTrue(branch.contains("a city of twisted glass") && branch.contains(lastClip.toString()) && branch.contains("the stairs"), branch);

        UUID child = studio.continueDream(parent, PathPick.owner("B"));
        var c = row(child);
        assertEquals(parent, c.get("parent_id"));
        assertEquals(2, c.get("episode"));
        assertEquals("the stairs", c.get("chosen_path"));
        assertEquals("owner", c.get("pick_source"));
        assertEquals(lastClip, c.get("start_clip_asset_id"));
        assertEquals(DreamService.sceneSeed(parent, row(parent)), ((Number) c.get("scene_seed")).longValue(), "same world seed");
        assertEquals(true, c.get("path_choice"), "the series keeps going");
        assertEquals(false, c.get("scout"));
        assertEquals(true, c.get("chain_shots"), "the parent's settings are inherited");

        String user = write(child, List.of("the roof", "the well"))[1];
        assertTrue(user.contains("EPISODE 2") && user.contains("a city of twisted glass") && user.contains("THE STAIRS"), user);
        assertTrue(user.contains("stairs on the right"), "the writer knows where the last episode ended");

        // shot 1 has no picture: its clip starts from the last frame of the parent's last clip
        var shots = jdbc.sql("SELECT id FROM dream_shot WHERE dream_short_id = :d ORDER BY ord").param("d", child).query(UUID.class).list();
        var jobs = dreams.clipJobs(child, null);
        assertEquals(List.of(1, 2, 3), jobs.stream().map(j -> j.shotNumber()).toList(),
                "no pictures needed: shot 1 from the parent, the rest from the clip before (made in the batch)");
        assertNull(jobs.getFirst().imageUri());
        assertEquals("s3://b/c-" + jdbc.sql("SELECT id FROM dream_shot WHERE clip_asset_id = :a").param("a", lastClip)
                .query(UUID.class).single() + ".mp4", jobs.getFirst().chainFromUri());
        assertEquals(3, shots.size());

        finish(child);
        UUID third = studio.continueDream(child, new PathPick("A", DreamBranches.COMMENTS, Map.of("A", 9, "B", 4)));
        assertEquals("comments", row(third).get("pick_source"), "the comment vote plugs in as just another picker");
        var chain = studio.chain(third);
        assertEquals(List.of(1, 2, 3), chain.stream().map(r -> ((Number) r.get("episode")).intValue()).toList());
        assertEquals(List.of(parent, child, third), chain.stream().map(r -> (UUID) r.get("id")).toList());
    }
}
