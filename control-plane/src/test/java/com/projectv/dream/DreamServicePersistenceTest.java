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
                "projectv-zimage", "ZImage", "projectv-wan", "Wan", "http://127.0.0.1:8080", BigDecimal.ZERO, 3.5, "", "lyria-3.5");
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
        dreams.saveClips(dream, clips, new ClipResult(List.of(
                new ClipOutput(shot1.toString(), 1, "dream", "s3://b/c1.mp4", 20, 57, 16, "fast", null, 0),
                new ClipOutput(shot2.toString(), 1, "dream", "s3://b/c2.mp4", 20, 57, 16, "fast", null, 0)), 1, false));
        assertEquals(3, count("SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND provider_id = 'modal_h100'"));

        var fin = dreams.finishRequest(dream);
        assertEquals(List.of("s3://b/c1.mp4", "s3://b/c2.mp4"), fin.clips().stream().map(c -> c.uri()).toList());
        assertEquals(16.0, fin.clips().getFirst().fps());
        dreams.saveFinish(dream, new DreamTypes.DreamFinishResult("s3://b/final.mp4", 6.5, 1080, 1920, 24, "rife-laptop", null, null));
        assertNotNull(jdbc.sql("SELECT final_asset_id FROM dream_short WHERE id = :d").param("d", dream).query(UUID.class).single());
        assertEquals(3, count("SELECT count(*) FROM routing_decision WHERE dream_short_id = :d"));
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
    void aBatchWithNoClipsFailsSoTheStepWaitsForTheOwner() {
        jdbc.sql("UPDATE dream_shot SET picture_asset_id = NULL").update();
        assertThrows(IllegalStateException.class, () -> dreams.clipRequest(dream, null));
    }

    @Test
    void estimateAsksWhenOverTheAutoApproveAmount() {
        var pics = dreams.pictureRequest(dream, null, null);
        dreams.savePictures(dream, pics, new KeyframeResult(List.of(
                new KeyframeOutput(shot1.toString(), "s3://b/p1.png", "modal_zimage", "z", 1L, 5.0, null, null, List.of())), 5));
        var est = dreams.clipEstimate(dream, null);
        assertTrue(est.needsApproval());
        assertTrue(est.inr().compareTo(BigDecimal.ZERO) > 0);
        assertTrue(est.summary().startsWith("1 clips on Modal Wan"));
    }
}
