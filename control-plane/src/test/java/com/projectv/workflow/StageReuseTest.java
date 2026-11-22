package com.projectv.workflow;

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

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** The stored-result lookup behind projectv.cost.reuse-stored-stages: real Postgres, no model. */
@Testcontainers(disabledWithoutDocker = true)
class StageReuseTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    UUID video;

    @BeforeAll
    static void setUp() {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        jdbc = JdbcClient.create(new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()));
    }

    @BeforeEach
    void seed() {
        video = UUID.randomUUID();
        jdbc.sql("INSERT INTO video (id, prompt, status) VALUES (:id, 'p', 'RUNNING')").param("id", video).update();
    }

    private void run(Stage stage, int attempt, String status, String summary, String finishedAt) {
        jdbc.sql("""
                INSERT INTO stage_run (id, video_id, stage, attempt, mode, status, summary, artifact_json, finished_at)
                VALUES (:id, :v, :s, :a, 'AUTO', :st, :sum, CAST('{"k":1}' AS jsonb), CAST(:f AS timestamptz))
                """).param("id", UUID.randomUUID()).param("v", video).param("s", stage.name()).param("a", attempt)
                .param("st", status).param("sum", summary).param("f", finishedAt).update();
    }

    private void concept() {
        jdbc.sql("INSERT INTO concept (id, video_id, big_idea, clock_definition, acts_json, version) VALUES (:id, :v, 'i', CAST('{}' AS jsonb), CAST('[]' AS jsonb), 1)")
                .param("id", UUID.randomUUID()).param("v", video).update();
    }

    private StageReuse on() {
        return new StageReuse(jdbc, true);
    }

    @Test
    void offByDefaultNeverReuses() {
        concept();
        run(Stage.CONCEPT, 1, "AUTO_PASSED", "done", "2026-11-22T10:00:00Z");
        assertTrue(new StageReuse(jdbc, false).reusable(video, Stage.CONCEPT).isEmpty());
    }

    @Test
    void finishedStageWithItsOutputIsReusedWithTheOriginalArtifact() {
        concept();
        run(Stage.CONCEPT, 1, "AUTO_PASSED", "A big idea.", "2026-11-22T10:00:00Z");
        var r = on().reusable(video, Stage.CONCEPT).orElseThrow();
        assertTrue(r.summary().startsWith(StageReuse.PREFIX) && r.summary().endsWith("A big idea."));
        assertTrue(r.artifactJson().contains("\"k\""));
    }

    @Test
    void failedRejectedOrRunningLatestAttemptIsNotReused() {
        concept();
        run(Stage.CONCEPT, 1, "AUTO_PASSED", "ok", "2026-11-22T10:00:00Z");
        run(Stage.CONCEPT, 2, "REJECTED", "no", "2026-11-22T10:05:00Z");
        assertTrue(on().reusable(video, Stage.CONCEPT).isEmpty(), "the owner rejected the latest result");
    }

    @Test
    void theRunningAttemptOfTheCurrentRunIsIgnored() {
        concept();
        run(Stage.CONCEPT, 1, "AUTO_PASSED", "ok", "2026-11-22T10:00:00Z");
        jdbc.sql("INSERT INTO stage_run (id, video_id, stage, attempt, mode, status) VALUES (:id, :v, 'CONCEPT', 2, 'AUTO', 'RUNNING')")
                .param("id", UUID.randomUUID()).param("v", video).update();
        assertTrue(on().reusable(video, Stage.CONCEPT).isPresent());
    }

    @Test
    void noOutputRowMeansNothingToReuse() {
        run(Stage.CONCEPT, 1, "AUTO_PASSED", "ok", "2026-11-22T10:00:00Z");
        assertFalse(on().reusable(video, Stage.CONCEPT).isPresent(), "the stage_run row alone is not enough");
    }

    @Test
    void anUpstreamStageRemadeLaterMakesItStale() {
        concept();
        run(Stage.CONCEPT, 1, "AUTO_PASSED", "ok", "2026-11-22T10:00:00Z");
        run(Stage.RESEARCH, 1, "AUTO_PASSED", "facts", "2026-11-22T10:30:00Z");
        assertTrue(on().reusable(video, Stage.CONCEPT).isEmpty());
    }

    @Test
    void anUpstreamStageThatWasOnlyReusedDoesNotMakeItStale() {
        concept();
        run(Stage.CONCEPT, 1, "AUTO_PASSED", "ok", "2026-11-22T10:00:00Z");
        run(Stage.RESEARCH, 1, "AUTO_PASSED", "facts", "2026-11-22T09:00:00Z");
        run(Stage.RESEARCH, 2, "AUTO_PASSED", StageReuse.PREFIX + "facts", "2026-11-22T10:30:00Z");
        assertEquals(StageReuse.PREFIX + "ok", on().reusable(video, Stage.CONCEPT).orElseThrow().summary());
    }

    @Test
    void musicIsNeverRemadeWhileTheVideoStillHasItsMusic() {
        run(Stage.MUSIC, 1, "AUTO_PASSED", "Score 120s", "2026-11-22T10:00:00Z");
        assertTrue(on().reusable(video, Stage.MUSIC).isEmpty(), "no music_cue row yet");
        UUID asset = new com.projectv.assets.AssetStore(jdbc).create(new com.projectv.assets.AssetStore.NewAsset(video, "audio",
                "s3://b/m.wav", "audio/wav", null, null, 10.0, "gemini_api", "lyria", null, null,
                com.projectv.assets.AssetStore.LICENCE_GEMINI, true));
        jdbc.sql("INSERT INTO music_cue (id, video_id, asset_id, bpm, beat_times_json, sections_json) VALUES (:id, :v, :a, 100, CAST('[]' AS jsonb), CAST('{}' AS jsonb))")
                .param("id", UUID.randomUUID()).param("v", video).param("a", asset).update();
        assertTrue(on().reusable(video, Stage.MUSIC).isPresent());
    }
}
