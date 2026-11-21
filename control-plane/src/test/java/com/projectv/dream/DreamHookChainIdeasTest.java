package com.projectv.dream;

import com.projectv.agents.Llm;
import com.projectv.assets.AssetStore;
import com.projectv.dream.DreamStudio.NewDream;
import com.projectv.dream.DreamTypes.DreamWorld;
import com.projectv.dream.DreamTypes.PlannedDreamShot;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.Pricing;
import com.projectv.media.MediaTypes.ClipOutput;
import com.projectv.media.MediaTypes.ClipResult;
import com.projectv.routing.Router;
import com.projectv.video.ClipProgress;
import io.temporal.testing.TestWorkflowEnvironment;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
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
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * The three Dream Shorts options (hook title, chained shots, idea pack) against a real Postgres, with a fake writer: all
 * three are OFF unless asked for, and switching them on changes only what it should. No GPU, no Gemini, no spend.
 */
@Testcontainers(disabledWithoutDocker = true)
class DreamHookChainIdeasTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static Llm llm;
    static DreamService dreams;
    static DreamStudio studio;
    static TestWorkflowEnvironment env;
    UUID dream;
    UUID shot1;
    UUID shot2;
    UUID shot3;

    @BeforeAll
    static void setUp() throws Exception {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        jdbc = JdbcClient.create(new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()));
        Pricing pricing = new Pricing("src/test/resources/config");
        BudgetGuard budget = new BudgetGuard(jdbc, pricing);
        var json = JsonMapper.builder().build();
        Router router = new Router(jdbc, json, budget, pricing);
        llm = mock(Llm.class);
        dreams = new DreamService(jdbc, json, llm, new AssetStore(jdbc), pricing, budget, router, new ClipProgress(),
                new DreamSettings("projectv-zimage", "ZImage", "projectv-wan", "Wan", "http://127.0.0.1:8080", BigDecimal.ZERO, 3.5, "", "lyria-3.5",
                "s3://projectv/music-beds/x.m4a", "gemini-3.6-flash", "claude:claude-sonnet-5-5", true));
        jdbc.sql("""
                        INSERT INTO provider (id, name, kind, capabilities_json, config_json, enabled)
                        VALUES ('modal_wan', 'Wan', 'gpu_serverless', CAST('["video.i2v.dream"]' AS jsonb),
                                CAST('{"quality":6,"budget":"gpu"}' AS jsonb), true)
                        ON CONFLICT (id) DO NOTHING
                        """).update();
        env = TestWorkflowEnvironment.newInstance(); // no worker: a started workflow just waits, the test reads the row
        studio = new DreamStudio(jdbc, env.getWorkflowClient(), json, dreams, new ClipProgress(), mock(DreamProducer.class));
    }

    @AfterAll
    static void tearDown() {
        env.close();
    }

    @BeforeEach
    void newShort() {
        dream = insert("a pink pier");
        shot1 = shot(dream, 0);
        shot2 = shot(dream, 1);
        shot3 = shot(dream, 2);
    }

    static UUID insert(String idea) {
        UUID id = UUID.randomUUID();
        jdbc.sql("INSERT INTO dream_short (id, idea, status) VALUES (:id, :i, 'RUNNING')").param("id", id).param("i", idea).update();
        return id;
    }

    static UUID shot(UUID dream, int ord) {
        UUID s = UUID.randomUUID();
        jdbc.sql("""
                        INSERT INTO dream_shot (id, dream_short_id, ord, scene, picture_prompt, motion_prompt)
                        VALUES (:id, :d, :o, 'scene', 'picture prompt', 'walk forward')
                        """).param("id", s).param("d", dream).param("o", ord).update();
        return s;
    }

    /** Gives the shot a picture asset and (optionally) a clip asset, as the real steps would. */
    static void media(UUID shot, String picture, String clip) {
        jdbc.sql("UPDATE dream_shot SET picture_asset_id = :a WHERE id = :s").param("a", asset(picture)).param("s", shot).update();
        if (clip != null) {
            jdbc.sql("UPDATE dream_shot SET clip_asset_id = :a WHERE id = :s").param("a", asset(clip)).param("s", shot).update();
        }
    }

    static UUID asset(String uri) {
        boolean video = uri.endsWith(".mp4");
        return new AssetStore(jdbc).create(new AssetStore.NewAsset(null, video ? "video" : "image", uri,
                video ? "video/mp4" : "image/png", 480, 832, null, "test", "m", "p", 1L, AssetStore.LICENCE_ZIMAGE, true));
    }

    static String text(String sql, UUID id) {
        return jdbc.sql(sql).param("d", id).query(String.class).optional().orElse(null);
    }

    DreamWorld world(String hook) {
        return new DreamWorld("T", "a city of twisted glass", "p", "l", "", List.of(new PlannedDreamShot("s1", "m1"),
                new PlannedDreamShot("s2", "m2"), new PlannedDreamShot("s3", "m3")), hook, "forward down the avenue, rising, then turning left");
    }

    /** The writer's prompts (system, user) for this Short, with a fake writer that answers {@code hook}. */
    String[] write(UUID id, String hook) {
        when(llm.call(any(), eq("dream.write"), any(), any(), eq(DreamWorld.class), any())).thenReturn(world(hook));
        ArgumentCaptor<String> sys = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<String> user = ArgumentCaptor.forClass(String.class);
        jdbc.sql("UPDATE dream_short SET shot_count = 3 WHERE id = :d").param("d", id).update();
        dreams.writeShots(id, null);
        verify(llm, atLeastOnce()).call(any(), eq("dream.write"), sys.capture(), user.capture(), eq(DreamWorld.class), any());
        if (id.equals(dream)) { // the writer replaces the shots: follow the new rows
            var ids = jdbc.sql("SELECT id FROM dream_shot WHERE dream_short_id = :d ORDER BY ord").param("d", id).query(UUID.class).list();
            shot1 = ids.get(0);
            shot2 = ids.get(1);
            shot3 = ids.get(2);
        }
        return new String[]{sys.getValue(), user.getValue()};
    }

    // ---------------------------------------------------------------------------------------------------- defaults

    @Test
    void everythingIsOffByDefaultAndThePromptsAreUnchanged() {
        var p = write(dream, "IGNORED *HOOK*");
        assertFalse(p[0].contains("\"hook\""), "no hook field asked for");
        assertFalse(p[0].contains("ONE continuous camera move through ONE world"), "the look's own structure is used");
        assertFalse(p[1].contains("Concept seed"));
        assertNull(text("SELECT hook_text FROM dream_short WHERE id = :d", dream), "a hook the Short did not ask for is never stored");
        for (UUID s : List.of(shot1, shot2, shot3)) {
            media(s, "s3://b/p-" + s + ".png", null);
        }
        // (clipRequest itself is cap-checked: the Modal monthly cap is 0 until 1 Dec, so the jobs and the flag are checked directly)
        assertFalse(dreams.chained(dream));
        assertTrue(dreams.clipJobs(dream, null).stream().allMatch(j -> j.chainFromUri() == null));
        for (UUID s : List.of(shot1, shot2, shot3)) {
            jdbc.sql("UPDATE dream_shot SET clip_asset_id = :a WHERE id = :s").param("a", asset("s3://b/c-" + s + ".mp4")).param("s", s).update();
        }
        jdbc.sql("UPDATE dream_short SET hook_text = 'X *Y* Z' WHERE id = :d").param("d", dream).update();
        assertNull(dreams.finishRequest(dream).hookText(), "hook off: nothing is burned in");
    }

    // ---------------------------------------------------------------------------------------------------- hook title

    @Test
    void theWriterIsAskedForAHookAndItIsStoredUnlessTheOwnerSetOne() {
        jdbc.sql("UPDATE dream_short SET hook_title = true WHERE id = :d").param("d", dream).update();
        assertTrue(write(dream, "THE STREET *FOLDED* LIKE PAPER")[0].contains("\"hook\""));
        assertEquals("THE STREET *FOLDED* LIKE PAPER", text("SELECT hook_text FROM dream_short WHERE id = :d", dream));

        dreams.setHook(dream, "MY *OWN* WORDS", "yellow"); // the owner edits it in the review step
        write(dream, "WRITER *REWRITE*");
        assertEquals("MY *OWN* WORDS", text("SELECT hook_text FROM dream_short WHERE id = :d", dream),
                "a rewrite never replaces the owner's hook");
        assertEquals("yellow", text("SELECT hook_color FROM dream_short WHERE id = :d", dream));
    }

    @Test
    void theFinishRequestCarriesTheHookOnlyWhenOnAndFilled() {
        for (UUID s : List.of(shot1, shot2, shot3)) {
            media(s, "s3://b/p-" + s + ".png", "s3://b/c-" + s + ".mp4");
        }
        jdbc.sql("UPDATE dream_short SET hook_title = true, hook_text = '  THESE *TOWERS* WONT  ', hook_color = 'cyan' WHERE id = :d")
                .param("d", dream).update();
        var fin = dreams.finishRequest(dream);
        assertEquals("THESE *TOWERS* WONT", fin.hookText());
        assertEquals("cyan", fin.hookColor());
        dreams.setHook(dream, "   ", null);
        assertNull(dreams.finishRequest(dream).hookText(), "a blank hook clears it");
        assertEquals("cyan", dreams.finishRequest(dream).hookColor(), "and keeps the colour");
    }

    // ---------------------------------------------------------------------------------------------------- chained shots

    @Test
    void chainedShotsAreDescribedAsOneContinuousMoveAndClipsRunInOrderFromTheLastFrame() {
        jdbc.sql("UPDATE dream_short SET chain_shots = true WHERE id = :d").param("d", dream).update();
        assertTrue(write(dream, null)[0].contains("ONE continuous camera move through ONE world"));

        media(shot1, "s3://b/p1.png", "s3://b/c1.mp4"); // shot 1 already has a clip (an earlier batch)
        media(shot2, "s3://b/p2.png", null);
        media(shot3, "s3://b/p3.png", null);
        assertTrue(dreams.chained(dream), "the worker runs the clips one at a time, each from the last frame of the one before");
        var jobs = dreams.clipJobs(dream, null);
        assertEquals(List.of(2, 3), jobs.stream().map(j -> j.shotNumber()).toList());
        assertEquals("s3://b/c1.mp4", jobs.getFirst().chainFromUri(), "shot 2 continues from shot 1's finished clip");
        assertNull(jobs.get(1).chainFromUri(), "shot 3 continues from shot 2, made in this batch");

        // redoing only shot 3 continues from shot 2's clip once it exists
        jdbc.sql("UPDATE dream_shot SET clip_asset_id = :a WHERE id = :s").param("a", asset("s3://b/c2.mp4")).param("s", shot2).update();
        assertEquals("s3://b/c2.mp4", dreams.clipJobs(dream, List.of(shot3.toString())).getFirst().chainFromUri());
    }

    @Test
    void chainedShotsAfterTheFirstNeedNoPictureOfTheirOwn() {
        media(shot1, "s3://b/p1.png", null); // only the first shot has a picture
        assertEquals(1, dreams.clipJobs(dream, null).size(), "unchained: a shot without a picture waits for it");
        jdbc.sql("UPDATE dream_short SET chain_shots = true WHERE id = :d").param("d", dream).update();
        var jobs = dreams.clipJobs(dream, null);
        assertEquals(List.of(1, 2, 3), jobs.stream().map(j -> j.shotNumber()).toList());
        assertEquals("s3://b/p1.png", jobs.getFirst().imageUri());
        assertNull(jobs.get(1).imageUri(), "no picture to fall back on: the worker continues from the clip before or fails");
    }

    @Test
    void chainsOfTwoPairShotsOneWithTwoAndThreeWithFourAndEachPairStartsFromAPicture() {
        assertEquals(List.of(false, true, false, true, false),
                List.of(0, 1, 2, 3, 4).stream().map(o -> DreamService.continues(true, 2, o)).toList());
        assertEquals(List.of(false, true, true), List.of(0, 1, 2).stream().map(o -> DreamService.continues(true, 0, o)).toList());
        assertFalse(DreamService.continues(false, 2, 1));

        UUID s4 = shot(dream, 3);
        jdbc.sql("UPDATE dream_short SET chain_shots = true, chain_group = 2 WHERE id = :d").param("d", dream).update();
        media(shot1, "s3://b/p1.png", "s3://b/c1.mp4");
        media(shot3, "s3://b/p3.png", null); // shots 2 and 4 have no picture: they continue from the clip before
        var jobs = dreams.clipJobs(dream, null);
        assertEquals(List.of(2, 3, 4), jobs.stream().map(j -> j.shotNumber()).toList());
        assertEquals("s3://b/c1.mp4", jobs.get(0).chainFromUri());
        assertNull(jobs.get(1).chainFromUri(), "shot 3 starts a new pair, from its own picture");
        assertEquals("s3://b/p3.png", jobs.get(1).imageUri());
        assertNull(jobs.get(2).chainFromUri(), "shot 4 continues from shot 3, made in this batch");
    }

    @Test
    void aLockedSceneHasOneCameraPathOneWorldAndOneSeed() {
        jdbc.sql("UPDATE dream_short SET chain_shots = true, chain_locked = true WHERE id = :d").param("d", dream).update();
        assertTrue(dreams.locked(dream));
        String sys = write(dream, null)[0];
        assertTrue(sys.contains("ONE locked scene") && sys.contains("\"cameraPath\""));
        var mp = jdbc.sql("SELECT motion_prompt FROM dream_shot WHERE dream_short_id = :d ORDER BY ord").param("d", dream).query(String.class).list();
        assertTrue(mp.stream().allMatch(m -> m.contains("forward down the avenue, rising, then turning left") && m.contains("a city of twisted glass")), mp.toString());
        for (UUID s : List.of(shot1, shot2, shot3)) {
            media(s, "s3://b/p-" + s + ".png", null);
        }
        var seeds = dreams.clipJobs(dream, null).stream().map(j -> j.seed() - j.take()).distinct().toList();
        assertEquals(1, seeds.size(), "every clip of the scene shares one seed");
        jdbc.sql("UPDATE dream_short SET chain_group = 2 WHERE id = :d").param("d", dream).update();
        assertFalse(dreams.locked(dream), "pairs are separate scenes: not locked");
    }

    @Test
    void eachChainedClipKeepsItsOwnCostEntryAndAFailedOneIsReportedForTheOwner() {
        jdbc.sql("UPDATE dream_short SET chain_shots = true WHERE id = :d").param("d", dream).update();
        for (UUID s : List.of(shot1, shot2, shot3)) {
            media(s, "s3://b/p-" + s + ".png", null);
        }
        var req = new com.projectv.media.MediaTypes.ClipRequest(dream.toString(), "a", "b", "u", dreams.clipJobs(dream, null), 1, 0,
                null, "wan", null, null, 0, null, 0, true);
        String said = dreams.saveClips(dream, req, new ClipResult(List.of(
                new ClipOutput(shot1.toString(), 1, "dream", "s3://b/c1.mp4", 20, 57, 16, "fast", null, 0),
                new ClipOutput(shot2.toString(), 1, "dream", null, 0, 0, 0, "fast", "pod lost", 0),
                new ClipOutput(shot3.toString(), 1, "dream", null, 0, 0, 0, "fast", "shot 2 has no clip", 0)), 1, false));
        assertTrue(said.contains("chained") && said.contains("1/3 clips made") && said.contains("shots after a failed one wait"), said);
        assertEquals("pod lost", text("SELECT error FROM dream_shot WHERE id = :d", shot2), "the failed shot waits for the owner (rule 2b)");
        assertEquals(1, jdbc.sql("SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND note LIKE 'Dream Short clip, shot 1%'")
                .param("d", dream).query(Integer.class).single(), "one cost entry per clip that used the GPU");
    }

    // ---------------------------------------------------------------------------------------------------- first-last frame

    @Test
    void firstLastFrameClipsTravelFromTheirPictureToTheNextAndTheLastEndsFreely() {
        jdbc.sql("UPDATE dream_short SET chain_bridge = true WHERE id = :d").param("d", dream).update();
        assertTrue(dreams.bridged(dream));
        assertFalse(dreams.chained(dream), "first-last is not last-frame chaining");
        assertTrue(write(dream, null)[0].contains("the exact view at the END of the shot before it"));
        media(shot1, "s3://b/p1.png", null);
        media(shot2, "s3://b/p2.png", null);
        media(shot3, "s3://b/p3.png", null);
        var jobs = dreams.clipJobs(dream, null);
        assertEquals(List.of("s3://b/p1.png", "s3://b/p2.png", "s3://b/p3.png"), jobs.stream().map(j -> j.imageUri()).toList(),
                "every shot starts from its own picture");
        assertEquals("s3://b/p2.png", jobs.get(0).endImageUri());
        assertEquals("s3://b/p3.png", jobs.get(1).endImageUri());
        assertNull(jobs.get(2).endImageUri(), "the last clip ends freely");
        assertTrue(jobs.stream().allMatch(j -> j.chainFromUri() == null));

        jdbc.sql("UPDATE dream_short SET chain_bridge = false WHERE id = :d").param("d", dream).update();
        assertTrue(dreams.clipJobs(dream, null).stream().allMatch(j -> j.endImageUri() == null), "off: as before");
    }

    @Test
    void aNewPictureMakesTheClipThatEndsOnItStaleToo() {
        jdbc.sql("UPDATE dream_short SET chain_bridge = true WHERE id = :d").param("d", dream).update();
        media(shot1, "s3://b/p1.png", "s3://b/c1.mp4");
        media(shot2, "s3://b/p2.png", "s3://b/c2.mp4");
        media(shot3, "s3://b/p3.png", "s3://b/c3.mp4");
        var job = new com.projectv.media.MediaTypes.KeyframeJob(shot2.toString(), 2, "zimage", "p", 768, 1344, "9:16", "1K", null, 1L);
        dreams.savePictures(dream, new com.projectv.media.MediaTypes.KeyframeRequest(dream.toString(), null, "a", "b", List.of(job)),
                new com.projectv.media.MediaTypes.KeyframeResult(List.of(new com.projectv.media.MediaTypes.KeyframeOutput(
                        shot2.toString(), "s3://b/p2-new.png", "zimage", "m", 1L, 1.0, null, null, null)), 0));
        assertFalse(hasClip(shot2), "its own clip");
        assertFalse(hasClip(shot1), "shot 1 ended on the old picture");
        assertTrue(hasClip(shot3), "shot 3 does not touch picture 2's end");
    }

    static boolean hasClip(UUID shot) {
        return jdbc.sql("SELECT clip_asset_id IS NOT NULL FROM dream_shot WHERE id = :s").param("s", shot).query(Boolean.class).single();
    }

    @Test
    void firstLastWinsOverLastFrameWhenBothAreAsked() {
        UUID id = studio.create(new NewDream("a pink pier", "lonely", 4, "480p", "AUTO", null, 10, "bed", false, true, true, true,
                null, null, null, null, true, null, null, 2, true, null, true));
        var row = jdbc.sql("SELECT * FROM dream_short WHERE id = :d").param("d", id).query().singleRow();
        assertEquals(true, row.get("chain_bridge"));
        assertEquals(false, row.get("chain_shots"));
        assertEquals(0, ((Number) row.get("chain_group")).intValue());
        assertEquals(false, row.get("chain_locked"));
    }

    @Test
    void newShortsMakeFluxPicturesByDefaultAndZImageStaysSelectable() {
        UUID def = studio.create(new NewDream("a pink pier", "lonely", 4, "480p", "AUTO", null, 10, "bed", false, true, true, true,
                null, null, null, null, null, null, null, null, null, null, null));
        UUID z = studio.create(new NewDream("a pink pier", "lonely", 4, "480p", "AUTO", null, 10, "bed", false, true, true, true,
                null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, "zimage"));
        UUID junk = studio.create(new NewDream("a pink pier", "lonely", 4, "480p", "AUTO", null, 10, "bed", false, true, true, true,
                null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, "sdxl"));
        java.util.function.Function<UUID, String> model = d -> jdbc.sql("SELECT picture_model FROM dream_short WHERE id = :d")
                .param("d", d).query(String.class).single();
        assertEquals("flux2-klein", model.apply(def), "owner's pick 21 Nov 2026: FLUX.2 klein is the studio default");
        assertEquals("zimage", model.apply(z));
        assertEquals("flux2-klein", model.apply(junk), "an unknown model falls back to the default");
    }

    // ---------------------------------------------------------------------------------------------------- unfiltered

    static NewDream unfiltered(String prompt) {
        // every option that would change the owner's words is switched ON here: Unfiltered must turn them off
        return new NewDream(prompt, "unfiltered", 5, "480p", "MANUAL", null, 10, "bed", true, true, true, true, true,
                true, "A *BIG* HOOK", "red", false, "impossible-architecture", "folding-street", null, null, true, null, "manual", 15);
    }

    @Test
    void unfilteredTurnsOffEverythingThatRewritesOrJudgesThePromptAndKeepsOneShotPerLine() {
        UUID id = studio.create(unfiltered("a goalkeeper dives in the rain\n\n  a crowd of fans roaring  \nthe ball hits the net"));
        var row = jdbc.sql("SELECT * FROM dream_short WHERE id = :d").param("d", id).query().singleRow();
        assertEquals("unfiltered", row.get("look"));
        assertEquals(false, row.get("scout"));
        assertEquals(false, row.get("hook_title"));
        assertEquals(false, row.get("path_choice"));
        assertEquals(false, row.get("producer_decides"));
        assertEquals(false, row.get("edit_music"));
        assertEquals(false, row.get("edit_ambience"));
        assertEquals(false, row.get("edit_pacing"));
        assertNull(row.get("idea_ref"), "no idea pack");
        assertEquals(3, ((Number) row.get("shot_count")).intValue(), "one shot per non-empty line");
        assertEquals("manual", row.get("gpu_hold"));
        assertEquals(15, ((Number) row.get("gpu_idle_minutes")).intValue());
    }

    @Test
    void unfilteredUsesTheOwnersWordsExactlyWithoutCallingAWriter() {
        UUID id = studio.create(unfiltered("a goalkeeper dives in the rain, anime style, a man is crying\nthe ball hits the net"));
        org.mockito.Mockito.clearInvocations(llm);
        String said = dreams.writeShots(id, "some notes that must not reach any writer");
        org.mockito.Mockito.verifyNoInteractions(llm);
        assertTrue(said.contains("2 shots") && said.contains("exactly as written"), said);
        var rows = jdbc.sql("SELECT scene, picture_prompt, motion_prompt FROM dream_shot WHERE dream_short_id = :d ORDER BY ord")
                .param("d", id).query().listOfRows();
        assertEquals(2, rows.size());
        assertEquals("a goalkeeper dives in the rain, anime style, a man is crying", rows.get(0).get("picture_prompt"));
        assertEquals(rows.get(0).get("picture_prompt"), rows.get(0).get("motion_prompt"), "the same words drive the picture and the motion");
        assertEquals("the ball hits the net", rows.get(1).get("picture_prompt"));
        assertEquals("a goalkeeper dives in the rain,…", text("SELECT title FROM dream_short WHERE id = :d", id));

        media(UUID.fromString(jdbc.sql("SELECT id::text FROM dream_shot WHERE dream_short_id = :d AND ord = 0").param("d", id).query(String.class).single()),
                "s3://b/p1.png", null);
        var jobs = dreams.clipJobs(id, null);
        assertEquals(1, jobs.size());
        assertEquals("a goalkeeper dives in the rain, anime style, a man is crying", jobs.getFirst().prompt(), "no look style, camera or 'no people' added");
        assertNull(jobs.getFirst().negative(), "no negative prompt added");
        assertEquals(0, DreamService.look("unfiltered").dissolveSec(), "hard cuts");
        assertEquals(1.0, DreamService.look("unfiltered").speed());
        assertEquals(81, DreamService.frames(DreamService.look("unfiltered").shotSeconds()), "the longest Wan clip: 5 s per line of the script");
        assertEquals("", DreamService.look("unfiltered").pictureStyle());
    }

    @Test
    void unfilteredSkipsTheQualityChecksThatJudgeNoPeopleAndFeelGood() {
        UUID id = studio.create(unfiltered("a goalkeeper dives in the rain"));
        dreams.writeShots(id, null);
        UUID s = UUID.fromString(jdbc.sql("SELECT id::text FROM dream_shot WHERE dream_short_id = :d").param("d", id).query(String.class).single());
        media(s, "s3://b/p1.png", null);
        assertNull(dreams.pictureCheckRequest(id), "no picture check");
        assertNull(dreams.checkRequest(id), "no quality check");
    }

    // ---------------------------------------------------------------------------------------------------- idea pack

    @Test
    void anIdeaFromThePackSeedsTheShortAndSkipsTheScout() {
        UUID id = studio.create(new NewDream("", "lonely", 5, "480p", "AUTO", null, 10, "bed", true, true, true, true, null,
                true, null, "yellow", true, "impossible-architecture", "folding-street"));
        var row = jdbc.sql("SELECT * FROM dream_short WHERE id = :d").param("d", id).query().singleRow();
        assertEquals("architecture", row.get("look"), "the pack decides the look");
        assertEquals(false, row.get("scout"), "no researching: the idea comes from the pack");
        assertEquals("impossible-architecture/folding-street", row.get("idea_ref"));
        assertTrue(row.get("idea").toString().contains("folds upward like a page"));
        assertEquals(true, row.get("hook_title"));
        assertEquals("THE STREET *FOLDED* LIKE PAPER", row.get("hook_text"), "the idea's own hook, locked so the writer keeps it");
        assertEquals(true, row.get("hook_locked"));
        assertEquals("yellow", row.get("hook_color"));
        assertEquals(true, row.get("chain_shots"));

        var p = write(id, "WRITER *HOOK*");
        assertTrue(p[0].contains("glides slowly and continuously"), "the architecture look's own camera rule");
        assertTrue(p[1].contains("Concept seed from the studio's idea pack") && p[1].contains("Steady walk toward the fold"));
        assertEquals("THE STREET *FOLDED* LIKE PAPER", text("SELECT hook_text FROM dream_short WHERE id = :d", id));
    }

    @Test
    void theOldFormStillCreatesAShortWithAllThreeOptionsOff() {
        UUID id = studio.create(new NewDream("a pink pier", "lonely", 5, "480p", "AUTO", null, 10, "bed", true, true, true, true));
        var row = jdbc.sql("SELECT * FROM dream_short WHERE id = :d").param("d", id).query().singleRow();
        assertEquals(false, row.get("hook_title"));
        assertEquals(false, row.get("chain_shots"));
        assertNull(row.get("idea_ref"));
        assertEquals(true, row.get("scout"));
        assertEquals("lonely", row.get("look"));
        assertThrows(IllegalArgumentException.class, () -> studio.create(new NewDream("x", "lonely", 5, "480p", "AUTO", null, 10, "bed",
                true, true, true, true, null, null, null, null, null, "impossible-architecture", "nope")));
    }

    // ---------------------------------------------------------------------------------------------------- SVI scenes

    @Test
    void plainShortsKeepTheirWriterPromptWordForWord() {
        var p = write(dream, null);
        assertTrue(p[0].contains("\"shots\": [{\"scene\": what this shot shows (one or two sentences, concrete), \"motion\": the camera move and\n"
                + "the small motion inside the scene}]} with exactly 3 shots."), p[0]);
        assertFalse(p[0].contains("\"scenes\""));
    }

    @Test
    void sviScenesAreWrittenAsScenesOfBeatsWithOneSharedStyle() {
        jdbc.sql("UPDATE dream_short SET svi_scenes = 2, svi_scene_seconds = 10, look = 'architecture' WHERE id = :d").param("d", dream).update();
        when(llm.call(any(), eq("dream.write"), any(), any(), eq(DreamWorld.class), any())).thenReturn(new DreamWorld("T", "w", "p", "l", "",
                null, null, null, null, "35 mm, soft teal haze",
                List.of(new DreamTypes.PlannedScene("a folding street", List.of("glide forward", "tilt up", "keep rising")),
                        new DreamTypes.PlannedScene("a stair to the sky", List.of("climb")))));
        ArgumentCaptor<String> sys = ArgumentCaptor.forClass(String.class);
        String said = dreams.writeShots(dream, null);
        verify(llm, atLeastOnce()).call(any(), eq("dream.write"), sys.capture(), any(), eq(DreamWorld.class), any());
        assertTrue(sys.getValue().contains("Invent 2 scenes") && sys.getValue().contains("\"style\": ONE line"), sys.getValue());
        assertTrue(sys.getValue().contains("with exactly 2 scenes") && !sys.getValue().contains("shots."),
                "no shot count: the scenes set the shape");
        assertTrue(said.contains("2 SVI scenes, 6 beats"), said);
        var rows = dreams.shots(dream);
        assertEquals(List.of(1, 1, 1, 2, 2, 2), rows.stream().map(DreamSvi::sceneOf).toList());
        assertTrue(rows.stream().allMatch(r -> r.get("motion_prompt").toString().contains("Style: 35 mm, soft teal haze.")),
                "one style line for every beat");
        assertTrue(rows.get(0).get("picture_prompt").toString().startsWith("a folding street")
                && rows.get(0).get("picture_prompt").toString().contains("Style: 35 mm, soft teal haze."));
        assertTrue(rows.get(1).get("motion_prompt").toString().startsWith("tilt up."), "each beat has its own motion");
        assertEquals("climb", rows.get(5).get("motion_prompt").toString().split("[.]")[0], "a missing beat repeats the last motion");
        assertEquals(6, ((Number) dreams.dream(dream).get("shot_count")).intValue());
    }

    @Test
    void sviOnTheFormWinsOverChainingAndSetsTheShape() {
        UUID id = studio.create(new NewDream("a pink pier", "lonely", 4, "480p", "AUTO", null, 10, "bed", false, true, true, true,
                null, null, null, null, true, null, null, 2, true, null, true, null, null, 3, 15, "dissolve"));
        var row = jdbc.sql("SELECT * FROM dream_short WHERE id = :d").param("d", id).query().singleRow();
        assertEquals(3, ((Number) row.get("svi_scenes")).intValue());
        assertEquals(15, ((Number) row.get("svi_scene_seconds")).intValue());
        assertEquals("dissolve", row.get("svi_between"));
        assertEquals(12, ((Number) row.get("shot_count")).intValue(), "3 scenes x 4 beats");
        assertEquals(false, row.get("chain_shots"));
        assertEquals(false, row.get("chain_bridge"));
        UUID off = studio.create(new NewDream("a pink pier", "lonely", 4, "480p", "AUTO", null, 10, "bed", false, true, true, true, null));
        assertEquals(0, ((Number) jdbc.sql("SELECT svi_scenes FROM dream_short WHERE id = :d").param("d", off).query().singleRow()
                .get("svi_scenes")).intValue(), "off by default");
    }
}
