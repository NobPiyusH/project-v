package com.projectv.video;

import com.projectv.workflow.StageMode;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;
import tools.jackson.databind.json.JsonMapper;

import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** A second video with the same prompt while the first is still in production is refused (22 Nov 2026: a duplicate TV video cost Rs 57). */
@Testcontainers(disabledWithoutDocker = true)
class DuplicateVideoGuardTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static VideoService service;

    @BeforeAll
    static void setUp() {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        jdbc = JdbcClient.create(new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()));
        service = new VideoService(jdbc, null, JsonMapper.builder().build()); // no Temporal: the guard refuses before it is needed
    }

    private UUID video(String prompt, String status, String ageMinutes) {
        UUID id = UUID.randomUUID();
        jdbc.sql("INSERT INTO video (id, prompt, status, created_at) VALUES (:id, :p, :s, now() - make_interval(mins => :m))")
                .param("id", id).param("p", prompt).param("s", status).param("m", Integer.parseInt(ageMinutes)).update();
        return id;
    }

    @Test
    void sameJobWhileAnotherIsPausedOrRunningIsRefused() {
        UUID first = video("3-minute television explainer A", "PAUSED", "8");
        var ex = assertThrows(IllegalStateException.class, () -> service.create(
                new VideoService.CreateVideo("  3-minute television explainer A ", StageMode.AUTO, null, null)));
        assertTrue(ex.getMessage().contains(first.toString()) && ex.getMessage().contains("allowDuplicate"));
    }

    @Test
    void finishedOrOldOrDifferentVideosAreNotDuplicates() {
        video("prompt done", "COMPLETED", "5");
        video("prompt old", "RUNNING", "600");
        video("prompt other", "RUNNING", "5");
        assertEquals(true, service.duplicateOf("prompt done").isEmpty());
        assertEquals(true, service.duplicateOf("prompt old").isEmpty());
        assertEquals(true, service.duplicateOf("prompt new").isEmpty());
        assertEquals(true, service.duplicateOf("prompt other").isPresent());
    }
}
