package com.projectv.dream;

import com.projectv.assets.AssetStore;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.Pricing;
import com.projectv.media.MediaTypes.ClipOutput;
import com.projectv.media.MediaTypes.ClipResult;
import com.projectv.media.MediaTypes.KeyframeOutput;
import com.projectv.media.MediaTypes.KeyframeResult;
import com.projectv.routing.Router;
import com.projectv.video.ClipProgress;
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
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Dream Shorts against a real Postgres: provenance, cost entries and routing decisions linked to the Short (rule 6). */
@Testcontainers(disabledWithoutDocker = true)
class DreamServicePersistenceTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static DreamService dreams;
    UUID dream;
    UUID shot1;
    UUID shot2;

    @BeforeAll
    static void setUp() throws Exception {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        jdbc = JdbcClient.create(new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()));
        Pricing pricing = new Pricing("../config");
        BudgetGuard budget = new BudgetGuard(jdbc, pricing);
        var json = JsonMapper.builder().build();
        Router router = new Router(jdbc, json, budget, pricing);
        dreams = new DreamService(jdbc, json, null, new AssetStore(jdbc), pricing, budget, router, new ClipProgress(),
                "projectv-zimage", "ZImage", "projectv-wan", "Wan", "http://127.0.0.1:8080", BigDecimal.ZERO, 3.5, "", "lyria-3.5",
                "s3://projectv/music-beds/my track.m4a", "gemini-3.6-flash", "claude:claude-sonnet-5-5", true);
        jdbc.sql("""
                        INSERT INTO provider (id, name, kind, capabilities_json, config_json, enabled)
                        VALUES ('local_laptop', 'Laptop', 'local', CAST('["ffmpeg"]' AS jsonb), CAST('{}' AS jsonb), true)
                        ON CONFLICT (id) DO NOTHING
                        """).update();
        provider("modal_zimage", "[\"image.dream\"]");
        provider("modal_wan", "[\"video.i2v.dream\"]");
        jdbc.sql("""
                        INSERT INTO provider (id, name, kind, capabilities_json, config_json, enabled)
                        VALUES ('gemini_lyria', 'Lyria', 'api', CAST('["music.dream"]' AS jsonb),
                                CAST('{"quality":7,"budget":"gemini"}' AS jsonb), true)
                        ON CONFLICT (id) DO NOTHING
                        """).update();
    }

    static void provider(String id, String caps) {
        jdbc.sql("""
                        INSERT INTO provider (id, name, kind, capabilities_json, config_json, enabled)
                        VALUES (:id, :id, 'gpu_serverless', CAST(:caps AS jsonb), CAST('{"quality":6,"budget":"gpu"}' AS jsonb), true)
                        ON CONFLICT (id) DO UPDATE SET capabilities_json = EXCLUDED.capabilities_json, config_json = EXCLUDED.config_json
                        """).param("id", id).param("caps", caps).update();
    }

    @BeforeEach
    void newShort() {
        dream = UUID.randomUUID();
        shot1 = UUID.randomUUID();
        shot2 = UUID.randomUUID();
        jdbc.sql("INSERT INTO dream_short (id, idea, status) VALUES (:id, 'a pink pier', 'RUNNING')").param("id", dream).update();
        for (UUID s : List.of(shot1, shot2)) {
            jdbc.sql("""
                            INSERT INTO dream_shot (id, dream_short_id, ord, scene, picture_prompt, motion_prompt)
                            VALUES (:id, :d, :o, 'scene', 'picture prompt', 'walk forward')
                            """).param("id", s).param("d", dream).param("o", s.equals(shot1) ? 0 : 1).update();
        }
    }

    int count(String sql) {
        return jdbc.sql(sql).param("d", dream).query(Integer.class).single();
    }

    @Test
    void picturesThenClipsThenFinishAreRecordedAndLinked() {
        var pics = dreams.pictureRequest(dream, null, null);
        assertEquals(2, pics.jobs().size());
        assertEquals(dream.toString(), pics.videoId());
        assertEquals(1, count("SELECT count(*) FROM routing_decision WHERE dream_short_id = :d AND capability = 'image.dream' AND chosen_provider_id = 'modal_zimage'"));

        dreams.savePictures(dream, pics, new KeyframeResult(List.of(
                new KeyframeOutput(shot1.toString(), "s3://b/p1.png", "modal_zimage", "z", 1L, 5.0, null, null, List.of()),
                new KeyframeOutput(shot2.toString(), "s3://b/p2.png", "modal_zimage", "z", 2L, 5.0, null, null, List.of())), 10));
        assertEquals(2, count("SELECT count(*) FROM asset a JOIN provenance p ON p.id = a.provenance_id WHERE a.dream_short_id = :d AND p.licence_id IS NOT NULL"));
        assertEquals(1, count("SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND provider_id = 'modal_l40s'"));
        assertTrue(dreams.pictureRequest(dream, null, null).jobs().isEmpty(), "pictures are not made twice");

        var clips = dreams.clipRequest(dream, null);
        assertEquals(2, clips.jobs().size());
        assertEquals(57, clips.jobs().getFirst().numFrames()); // 3.5 s at 16 fps
        assertEquals(480, clips.jobs().getFirst().width());
        assertEquals(1, clips.containers());
        assertEquals(0, clips.holdSeconds(), "the owner reviews: no warm hold");
        jdbc.sql("UPDATE dream_short SET producer_decides = true WHERE id = :d").param("d", dream).update();
        assertEquals(90, dreams.holdSeconds(dream), "the AI Producer runs it: the GPU stays warm for its redo");
        jdbc.sql("UPDATE dream_short SET producer_decides = false WHERE id = :d").param("d", dream).update();
        dreams.saveClips(dream, clips, new ClipResult(List.of(
                new ClipOutput(shot1.toString(), 1, "dream", "s3://b/c1.mp4", 20, 57, 16, "fast", null, 0),
                new ClipOutput(shot2.toString(), 1, "dream", "s3://b/c2.mp4", 20, 57, 16, "fast", null, 0)), 1, false));
        assertEquals(3, count("SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND provider_id = 'modal_h100'"));

        var fin = dreams.finishRequest(dream);
        assertEquals(List.of("s3://b/c1.mp4", "s3://b/c2.mp4"), fin.clips().stream().map(c -> c.uri()).toList());
        assertEquals(16.0, fin.clips().getFirst().fps());
        dreams.saveFinish(dream, new DreamTypes.DreamFinishResult("s3://b/final.mp4", 6.5, 1080, 1920, 24, "rife-laptop", null, null,
                java.util.Map.of("motion", 1.8, "colour", 26.0)));
        assertNotNull(jdbc.sql("SELECT final_asset_id FROM dream_short WHERE id = :d").param("d", dream).query(UUID.class).single());
        assertEquals(1, count("SELECT count(*) FROM dream_metric WHERE dream_short_id = :d AND (metrics->>'colour')::float = 26"));
        var chk = dreams.checkRequest(dream);
        assertTrue(chk.brief().contains("Shot 1: scene") && chk.videoUri().equals("s3://b/final.mp4"), chk.brief());
        String said = dreams.saveCheck(dream, new DreamTypes.DreamCheckResult(java.util.Map.of("noPeople", 3), 6,
                List.of(new DreamTypes.CheckIssue(3, 7.5, "a woman walks into frame", "redo clip 3")), "one person appears",
                "gemini-3.6-flash", new com.projectv.media.MediaTypes.Usage(5000, 200, 0, 5200), null), "FINISH");
        assertEquals("Quality check: 6/10, 1 issue - a woman walks into frame.", said);
        assertEquals(1, count("SELECT count(*) FROM dream_short WHERE id = :d AND (check_json->>'overall')::int = 6"));
        assertEquals("Quality check unavailable this time.", dreams.saveCheck(dream,
                new DreamTypes.DreamCheckResult(java.util.Map.of(), 0, List.of(), "", null, null, "no network"), "FINISH"));
        assertEquals(2, dreams.pictureCheckRequest(dream).pictureUris().size(), "both pictures go to the picture check");
        assertTrue(dreams.saveCheck(dream, new DreamTypes.DreamCheckResult(java.util.Map.of("noPeople", 10), 9, List.of(), "fine",
                "gemini-3.6-flash", null, null), "PICTURES").startsWith("Picture check: 9/10"));
        assertEquals(1, count("SELECT count(*) FROM dream_short WHERE id = :d AND (picture_check_json->>'overall')::int = 9"));
        var on = dreams.finishRequest(dream);
        assertTrue(on.musicEdit() && on.ambience() && on.pacing(), "the editing rules are on by default");
        assertEquals("scene", on.clips().getFirst().scene());
        jdbc.sql("UPDATE dream_short SET edit_ambience = false WHERE id = :d").param("d", dream).update();
        assertTrue(!dreams.finishRequest(dream).ambience() && dreams.finishRequest(dream).pacing());
        assertEquals(4, count("SELECT count(*) FROM routing_decision WHERE dream_short_id = :d")); // + the quality check
    }

    @Test
    void aNewPictureMakesTheOldClipAndFinalStale() {
        var pics = dreams.pictureRequest(dream, null, null);
        dreams.savePictures(dream, pics, new KeyframeResult(List.of(
                new KeyframeOutput(shot1.toString(), "s3://b/p1.png", "modal_zimage", "z", 1L, 5.0, null, null, List.of()),
                new KeyframeOutput(shot2.toString(), "s3://b/p2.png", "modal_zimage", "z", 2L, 5.0, null, null, List.of())), 10));
        var clips = dreams.clipRequest(dream, null);
        dreams.saveClips(dream, clips, new ClipResult(List.of(
                new ClipOutput(shot1.toString(), 1, "dream", "s3://b/c1.mp4", 20, 57, 16, "fast", null, 0)), 1, false));

        var redo = dreams.pictureRequest(dream, List.of(shot1.toString()), "more pink");
        assertEquals(1, redo.jobs().size());
        assertTrue(redo.jobs().getFirst().prompt().endsWith("Owner's notes: more pink"));
        assertEquals(2, redo.jobs().getFirst().attempt());
        dreams.savePictures(dream, redo, new KeyframeResult(List.of(
                new KeyframeOutput(shot1.toString(), "s3://b/p1b.png", "modal_zimage", "z", 3L, 5.0, null, null, List.of())), 5));
        assertNull(jdbc.sql("SELECT clip_asset_id FROM dream_shot WHERE id = :s").param("s", shot1).query(UUID.class).optional().orElse(null));
        assertThrows(IllegalStateException.class, () -> dreams.finishRequest(dream), "a shot without a clip can't be finished");
    }

    @Test
    void musicIsRoutedCostedAgainstGeminiAndUsedByTheFinish() {
        var req = dreams.musicRequest(dream, "more strings");
        assertEquals(1, req.maxSegments());
        assertTrue(req.prompt().contains("no vocals") && req.prompt().endsWith("Owner's notes: more strings"));
        assertEquals(7.625, req.targetSec(), 0.01); // two 3.5625 s clips - one 0.5 s dissolve + 1 s tail
        assertEquals(1, count("SELECT count(*) FROM routing_decision WHERE dream_short_id = :d AND capability = 'music.dream' AND chosen_provider_id = 'gemini_lyria'"));
        dreams.saveMusic(dream, new com.projectv.media.MediaTypes.MusicResult("s3://b/score.wav", 7, 80, List.of(), List.of(), 1,
                "lyria-3.5", List.of()));
        assertEquals(1, count("SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND provider_id = 'gemini_api' AND unit_type = 'song'"));
        jdbc.sql("UPDATE dream_shot SET clip_asset_id = (SELECT music_asset_id FROM dream_short WHERE id = :d) WHERE dream_short_id = :d")
                .param("d", dream).update(); // any asset will do: the finish only needs every shot to have a clip
        assertEquals("s3://b/score.wav", dreams.finishRequest(dream).musicUri());
    }

    @Test
    void theOwnersTrackIsTheFreeDefaultWithProvenanceAndFreshPieces() {
        assertEquals("bed", dreams.musicSource(dream));
        var req = dreams.musicBedRequest(dream);
        assertEquals("s3://projectv/music-beds/my track.m4a", req.bedUri());
        assertEquals(7.625, req.targetSec(), 0.01);
        assertEquals(dream + "#0", req.seed());
        assertEquals("gemini-3.6-flash", req.model());
        assertEquals(6, req.candidates());
        assertTrue(req.brief().contains("a pink pier") && req.brief().contains("Shots, in order: scene | scene"), req.brief());
        String said = dreams.saveMusicBed(dream, new DreamTypes.DreamBedResult("s3://b/piece.wav", 8.1, 754.25, 3600, "my track.m4a",
                "gemini-3.6-flash", new com.projectv.media.MediaTypes.Usage(3000, 60, 0, 3060), "soft and floating", 6));
        assertTrue(said.contains("12:34") && said.contains("best of 6 pieces by ear: soft and floating"), said);
        assertEquals(1, count("SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND provider_id = 'local_laptop' AND amount_inr = 0"));
        assertEquals(1, count("SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND provider_id = 'gemini_api' AND amount_inr > 0 AND unit_type = 'token'"));
        assertEquals(1, count("SELECT count(*) FROM routing_decision WHERE dream_short_id = :d AND stage = 'DREAM_MUSIC_PICK'"));
        assertEquals(1, count("SELECT count(*) FROM routing_decision WHERE dream_short_id = :d AND capability = 'music.dream' AND chosen_provider_id = 'local_laptop'"));
        assertEquals(1, count("""
                SELECT count(*) FROM dream_short d JOIN asset a ON a.id = d.music_asset_id JOIN provenance p ON p.id = a.provenance_id
                WHERE d.id = :d AND p.provider = 'owner_music_bed' AND p.model = 'my track.m4a' AND p.seed = 754250
                  AND NOT p.ai_generated AND p.licence_id = '00000000-0000-0000-0000-00000000a012'
                  AND p.prompt LIKE 'piece from 12:34 to 12:42 of s3://projectv/music-beds/my track.m4a'
                """));
        var next = dreams.musicBedRequest(dream);
        assertEquals(dream + "#1", next.seed(), "New music asks for another piece");
        assertTrue(next.usedStartsSec().contains(754.25), "pieces other Shorts used are avoided");
        jdbc.sql("UPDATE dream_shot SET clip_asset_id = (SELECT music_asset_id FROM dream_short WHERE id = :d) WHERE dream_short_id = :d")
                .param("d", dream).update();
        assertEquals("your track (my track.m4a)", dreams.finishRequest(dream).musicName());
        jdbc.sql("UPDATE dream_short SET music_source = 'lyria' WHERE id = :d").param("d", dream).update();
        assertEquals("lyria", dreams.musicSource(dream));
    }

    @Test
    void theNewLooksHaveTheirOwnShotLengthAndJoins() {
        assertEquals("lonely", DreamService.LOOK_ORDER.getFirst());
        var lonely = DreamService.look("lonely");
        var walk = DreamService.look("walkthrough");
        assertEquals(0, lonely.dissolveSec(), "hard cuts");
        assertEquals(5 * 57 / 16.0 / 0.7 + 1, dreams.shortSeconds(5, lonely), 0.01); // slowed to 70% on the laptop
        assertEquals(4 * 81 / 16.0 / 0.75 - 3 * 0.35 + 1, dreams.shortSeconds(4, walk), 0.01);
        assertTrue(lonely.motionStyle().contains("no drone") && lonely.pictureStyle().contains("no small text"));
        assertTrue(lonely.motionStyle().contains("nobody is ever visible") && !lonely.motionStyle().contains("a person "), "no person in the camera words");
        assertEquals("stand", lonely.handheld());
        assertTrue(lonely.structure().formatted(5).contains("DIFFERENT place"));
        assertEquals(lonely, DreamService.look("nope"), "unknown looks fall back to the default");
        var drive = DreamService.look("drive");
        assertEquals(0, drive.dissolveSec());
        assertTrue(drive.pictureStyle().contains("not a 3D render") && lonely.pictureStyle().contains("not a 3D render"));
    }

    @Test
    void realGpuTimingsReplaceTheConfigGuessAndCostsAreCheckedAgainstTheEstimate() {
        var ev = dreams.evidence();
        String kind = "wan.fast.720p";
        jdbc.sql("DELETE FROM gpu_timing WHERE kind = :k").param("k", kind).update();
        jdbc.sql("DELETE FROM calibration WHERE key LIKE 'wan.fast.720p%'").update();
        ev.gpuTiming(dream, kind, 57, 1, true); // too few warm clips yet: nothing changes
        assertTrue(ev.recalibrate(kind).isEmpty());
        jdbc.sql("DELETE FROM gpu_timing WHERE kind = :k").param("k", kind).update();
        ev.gpuTiming(dream, kind, 57, 70, false);
        ev.gpuTiming(dream, kind, 57, 84, false);
        ev.gpuTiming(dream, kind, 57, 140, true);
        assertEquals(2, ev.recalibrate(kind).size());
        double per81 = (70 + 84) / 2.0 * 81 / 57;
        assertEquals(per81, ev.calibrated(kind + ".per81").orElseThrow(), 0.01);
        assertEquals(140 - 77, ev.calibrated(kind + ".first_extra").orElseThrow(), 0.01); // the first clip took 63 s longer
        jdbc.sql("UPDATE dream_short SET quality = '720p' WHERE id = :d").param("d", dream).update();
        var pics = dreams.pictureRequest(dream, null, null);
        dreams.savePictures(dream, pics, new KeyframeResult(List.of(
                new KeyframeOutput(shot1.toString(), "s3://b/p1.png", "modal_zimage", "z", 1L, 5.0, null, null, List.of())), 5));
        double minutes = dreams.clipEstimate(dream, null).minutes();
        assertEquals((per81 * 57 / 81 + ev.calibrated(kind + ".first_extra").orElseThrow() + 140) / 60, minutes, 0.01);
        ev.costCheck(dream, "CLIPS", 25, 47);
        dreams.status(dream, "CLIPS", "FAILED_WAITING", "GPU cap reached");
        String kinds = ev.events(dream).stream().map(e -> (String) e.get("kind")).toList().toString();
        assertTrue(kinds.contains("cost") && kinds.contains("failure"), kinds);
        assertTrue(ev.events(dream).stream().anyMatch(e -> String.valueOf(e.get("data")).contains("\"ratio\": 1.88")));
        jdbc.sql("DELETE FROM gpu_timing").update();
        jdbc.sql("DELETE FROM calibration").update();
    }

    @Test
    void theWriterFollowsThePickedAngleAndAvoidsWhatExists() {
        jdbc.sql("""
                        UPDATE dream_short SET scout_angle = 2, scout_json = CAST(:j AS jsonb) WHERE id = :d
                        """).param("d", dream).param("j", """
                {"source": "youtube", "videos": [{"url": "u", "shows": "a neon kiosk in a field at dusk"}],
                 "brief": {"cliches": ["neon kiosk at dusk"], "gaps": ["rain at dawn"],
                           "angles": [{"title": "One", "idea": "first", "why": "a"}, {"title": "Dawn rain", "idea": "a phone box at dawn in rain", "why": "b"}]}}
                """).update();
        String forWriter = dreams.scouted(dreams.dream(dream)).forWriter();
        assertTrue(forWriter.contains("build EVERY shot around it") && forWriter.contains("Dawn rain - a phone box at dawn in rain"), forWriter);
        assertTrue(forWriter.contains("Avoid these clichés: neon kiosk at dusk") && forWriter.contains("Do not copy"), forWriter);
        assertTrue(dreams.scoutWanted(dream), "the Scout is on by default");
        assertEquals("Dawn rain", dreams.scouted(dreams.dream(dream)).chosen().title(), "the picked angle replaces the look's structure");
    }

    @Test
    void aReviewNoteBecomesALessonThatGoesIntoThePromptsOnlyOnceApproved() {
        DreamLessons lessons = new DreamLessons(jdbc);
        dreams.setLessons(lessons);
        lessons.review(dream, shot1, 2, "a man appeared filming with a phone");
        lessons.review(dream, null, 4, "  ");
        assertEquals(2, count("SELECT count(*) FROM dream_review WHERE dream_short_id = :d"));
        var mine = lessons.lessons().stream().filter(l -> dream.equals(l.get("dream_short_id"))).toList();
        assertEquals(1, mine.size(), "only a note becomes a lesson");
        assertEquals("WRITER", mine.getFirst().get("target"));
        UUID lesson = (UUID) mine.getFirst().get("id");
        assertTrue(!lessons.forPrompt("WRITER").contains("a man appeared"), "proposed lessons stay out of the prompts");
        lessons.update(lesson, "never describe anyone filming", null, "ACTIVE");
        assertTrue(lessons.forPrompt("WRITER").contains("never describe anyone filming"));
        assertTrue(!lessons.forPrompt("PICTURE").contains("never describe anyone filming"), "writer rules stay with the writer");
        lessons.update(lesson, null, null, "RETIRED");
        assertTrue(!lessons.forPrompt("WRITER").contains("never describe anyone filming"));
        assertThrows(IllegalArgumentException.class, () -> lessons.review(dream, null, 6, "x"));
        dreams.setLessons(null);
    }

    @Test
    void anIdeaWithNoSimilarShortsCarriesOnWithoutAnglesInsteadOfStopping() {
        var req = new DreamTypes.DreamScoutRequest(dream.toString(), "idea", "gemini", 6, "lonely");
        String said = dreams.saveScout(dream, req,
                new DreamTypes.DreamScoutResult(List.of(), List.of(), "youtube", 2, "no Shorts found for this idea"));
        assertTrue(said.contains("no similar Shorts") && said.contains("no angle"), said);
        assertEquals(0, count("SELECT count(*) FROM dream_short WHERE id = :d AND scout_json IS NOT NULL"));
    }

    @Test
    void aBatchWithNoClipsFailsSoTheStepWaitsForTheOwner() {
        jdbc.sql("UPDATE dream_shot SET picture_asset_id = NULL").update();
        assertThrows(IllegalStateException.class, () -> dreams.clipRequest(dream, null));
    }

    @Test
    void estimateAsksWhenOverTheAutoApproveAmount() {
        jdbc.sql("DELETE FROM gpu_timing").update(); // the config's numbers, not another test's measured ones
        jdbc.sql("DELETE FROM calibration").update();
        var pics = dreams.pictureRequest(dream, null, null);
        dreams.savePictures(dream, pics, new KeyframeResult(List.of(
                new KeyframeOutput(shot1.toString(), "s3://b/p1.png", "modal_zimage", "z", 1L, 5.0, null, null, List.of())), 5));
        var est = dreams.clipEstimate(dream, null);
        assertTrue(est.needsApproval());
        assertTrue(est.inr().compareTo(BigDecimal.ZERO) > 0);
        assertTrue(est.summary().startsWith("1 clips on Modal Wan"));
        jdbc.sql("UPDATE dream_short SET quality = '720p' WHERE id = :d").param("d", dream).update();
        // measured 19 Nov: ~133 s per 81 frames at 720p, +64 s warm-up for the first clip, +140 s container start
        assertEquals((133.0 * 57 / 81 + 64 + 140) / 60, dreams.clipEstimate(dream, null).minutes(), 0.01);
    }

    @Test
    void producerReviewsAreKeptWithTheShortAndAutopilotShortsAreHeldToTheDailyCap() throws Exception {
        var pricing = new Pricing("../config");
        var producer = new DreamProducer(jdbc, JsonMapper.builder().build(), dreams, null, pricing, "gemini-3.6-flash");
        jdbc.sql("UPDATE dream_short SET producer_decides = true WHERE id = :d").param("d", dream).update();
        assertTrue(producer.decides(dream.toString()));

        var advice = producer.saveReview(dream.toString(), "PICTURES", 1, new com.projectv.media.MediaTypes.ProducerReviewResult(
                "redo", 0.8, "shot 2 melts", "keep the pier straight", List.of(
                new com.projectv.media.MediaTypes.ProducerItemVerdict("shot-1", 8, true, "", ""),
                new com.projectv.media.MediaTypes.ProducerItemVerdict("shot-2", 4, false, "melted rail", "straight rail")),
                "gemini-3.6-flash", new com.projectv.media.MediaTypes.Usage(1000, 200, 0, 1200)));
        assertEquals(List.of("shot-2"), advice.weakItems());
        assertEquals(List.of(shot2.toString()), producer.shotIds(dream.toString(), advice.weakItems()));
        assertEquals(1, count("SELECT count(*) FROM producer_decision WHERE dream_short_id = :d AND stage = 'DREAM_PICTURES' AND video_id IS NULL"));
        assertEquals(1, count("SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND note LIKE 'PRODUCER review%'"));
        producer.recordOwnerDecision(dream, "PICTURES", "reject", "yes, fix the rail");
        assertEquals(1, count("SELECT count(*) FROM producer_decision WHERE dream_short_id = :d AND agreed"));

        // not an autopilot Short: the Producer's ₹100 per Short; an autopilot Short: also the daily cap (₹30 here)
        assertEquals(0, new BigDecimal("100").compareTo(producer.limit(dream)));
        jdbc.sql("UPDATE autopilot_setting SET daily_cap_inr = 30").update();
        jdbc.sql("INSERT INTO autopilot_run (id, run_date, status, dream_short_id) VALUES (:id, DATE '2026-01-01', 'RUNNING', :d)")
                .param("id", UUID.randomUUID()).param("d", dream).update();
        assertEquals(0, new BigDecimal("30").compareTo(producer.limit(dream)));
        assertTrue(producer.spendDecision(dream.toString(), new BigDecimal("25")).approve());
        assertTrue(!producer.spendDecision(dream.toString(), new BigDecimal("35")).approve(), "over the daily cap: asks");
        assertEquals(1, count("SELECT count(*) FROM producer_decision WHERE dream_short_id = :d AND acted = 'approved_spend'"));
    }
    @Test
    void theSameClipIssueTwiceGetsOnePromptFixOnTheClipsThenTheOwnerIsAsked() throws Exception {
        var producer = new DreamProducer(jdbc, JsonMapper.builder().build(), dreams, null, new Pricing("../config"), "gemini-3.6-flash");
        dreams.savePictures(dream, dreams.pictureRequest(dream, null, null), new KeyframeResult(List.of(
                new KeyframeOutput(shot1.toString(), "s3://b/p1.png", "modal_zimage", "z", 1L, 5.0, null, null, List.of()),
                new KeyframeOutput(shot2.toString(), "s3://b/p2.png", "modal_zimage", "z", 2L, 5.0, null, null, List.of())), 10));
        java.util.function.Function<String, com.projectv.media.MediaTypes.ProducerReviewResult> people = problem ->
                new com.projectv.media.MediaTypes.ProducerReviewResult("redo", 0.8, "shot 1 has people", "remove them", List.of(
                        new com.projectv.media.MediaTypes.ProducerItemVerdict("shot-1", 4, false, problem, "empty pier")),
                        "gemini-3.6-flash", null);

        assertEquals("redo", producer.saveReview(dream.toString(), "CLIPS", 1, people.apply("visible human figures")).verdict());
        assertNull(dreams.clipJobs(dream, null).getFirst().negative(), "first redo: an ordinary re-roll");

        var fix = producer.saveReview(dream.toString(), "CLIPS", 2, people.apply("a man stands on the pier"));
        assertEquals("redo", fix.verdict());
        assertTrue(fix.redoNotes().startsWith("RECURRING ISSUE (visible people)"), fix.redoNotes());
        var job = dreams.clipJobs(dream, null).getFirst();
        assertTrue(job.negative().contains("human figure"), "the redo clip carries the firm negative prompt");
        assertTrue(job.prompt().startsWith("walk forward ") && job.prompt().contains("empty of people"), job.prompt());

        var ask = producer.saveReview(dream.toString(), "CLIPS", 3, people.apply("silhouette in the fog"));
        assertEquals("ask", ask.verdict(), "the fix did not hold: ask the owner instead of re-rolling");
        assertEquals(1, count("SELECT count(*) FROM producer_decision WHERE dream_short_id = :d AND stop_rule = 'prompt_fix' AND stop_cause = 'people'"));
        assertEquals(1, count("SELECT count(*) FROM producer_decision WHERE dream_short_id = :d AND stop_rule = 'ask' AND verdict = 'redo'"));
    }

    @Test
    void aWatchedShortIsRememberedAndOfferedToTheNextScout() {
        var seen = new DreamTypes.ScoutVideo("https://www.youtube.com/shorts/AbCdEfGhIjK", "t", "c", 10L, 20.0, "a pier",
                "soft", "handheld", 4, 2.0, "piano", "fog", "AI", "calm", null);
        var failed = new DreamTypes.ScoutVideo("https://youtu.be/ZZZZZZZZZZZ", "t", "c", 1L, 20.0, null, null, null, null,
                null, null, null, null, null, "TimeoutError: slow");
        dreams.rememberWatched(List.of(seen, failed), "gemini-3.6-flash");
        var known = dreams.scoutRequest(dream, null).known();
        assertEquals("a pier", known.get("AbCdEfGhIjK").shows());
        assertTrue(!known.containsKey("ZZZZZZZZZZZ"), "a failed watch is tried again next time");
    }
}
