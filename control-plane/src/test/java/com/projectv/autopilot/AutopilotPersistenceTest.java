package com.projectv.autopilot;

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

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

/** The autopilot's records against a real Postgres: off by default, ideas, one run a day, no double posts, ₹0 cost entry. */
@Testcontainers(disabledWithoutDocker = true)
class AutopilotPersistenceTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static final ZoneId IST = ZoneId.of("Asia/Kolkata");

    @BeforeAll
    static void migrate() {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        jdbc = JdbcClient.create(new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()));
    }

    @BeforeEach
    void clean() {
        jdbc.sql("DELETE FROM autopilot_run").update();
        jdbc.sql("DELETE FROM autopilot_idea").update();
        jdbc.sql("UPDATE autopilot_setting SET enabled = false, start_mode = 'AUTO', start_time = '07:00'").update();
    }

    /** No Temporal, no LLM: only paths that do not start a Short or write a kit. */
    AutopilotService at(String istTime) {
        Instant t = LocalDate.of(2026, 11, 20).atTime(java.time.LocalTime.parse(istTime)).atZone(IST).toInstant();
        return new AutopilotService(jdbc, JsonMapper.builder().build(), null, null, null, IST, Clock.fixed(t, IST));
    }

    long runs() {
        return jdbc.sql("SELECT count(*) FROM autopilot_run").query(Long.class).single();
    }

    @Test
    void offByDefaultAndEveryStepDefaultsToTheProducer() {
        var s = at("08:00").settings();
        assertFalse(s.enabled());
        assertEquals("PRODUCER", s.stepModes().get("CLIPS"));
        assertEquals("private", s.privacy());
        assertEquals(0, new java.math.BigDecimal("50").compareTo(s.dailyCapInr()), "owner set the daily cap to ₹50");
        at("08:00").tick();
        assertEquals(0, runs());
    }

    @Test
    void ownerIdeasComeFirstThenUnusedScoutAngles() {
        var ap = at("08:00");
        UUID dream = UUID.randomUUID();
        jdbc.sql("""
                        INSERT INTO dream_short (id, idea, status, look, scout_angle, scout_json)
                        VALUES (:id, 'x', 'COMPLETED', 'drive', 1, CAST(:j AS jsonb))
                        """).param("id", dream).param("j", """
                        {"brief": {"angles": [{"title": "a", "idea": "used angle"}, {"title": "b", "idea": "a tram under the sea"}]}}
                        """).update();
        ap.addIdea("my own idea", null);
        assertEquals("my own idea", ap.nextIdea(true).get("idea"));
        jdbc.sql("UPDATE autopilot_idea SET used_at = now()").update();
        assertNull(ap.nextIdea(false), "Scout ideas only when allowed");
        Map<String, Object> scout = ap.nextIdea(true);
        assertEquals("a tram under the sea", scout.get("idea"));
        assertEquals("drive", scout.get("look"));
        assertEquals(0, ap.harvestScoutAngles(), "each angle is added once");
    }

    @Test
    void manualStartWaitsAndOnlyOneRunPerDay() {
        jdbc.sql("UPDATE autopilot_setting SET enabled = true, start_mode = 'MANUAL'").update();
        at("06:00").tick();
        assertEquals(0, runs(), "before the start time");
        at("08:00").addIdea("glass orchard", null);
        at("08:00").tick();
        at("09:00").tick();
        assertEquals(1, runs());
        assertEquals("WAITING_START", jdbc.sql("SELECT status FROM autopilot_run").query(String.class).single());
    }

    @Test
    void noIdeasMeansASkippedDayNotAnError() {
        jdbc.sql("UPDATE autopilot_setting SET enabled = true, use_scout = false").update();
        at("08:00").tick();
        assertEquals("SKIPPED", jdbc.sql("SELECT status FROM autopilot_run").query(String.class).single());
    }

    @Test
    void aFailedRunHoldsTheNextDayUntilDismissed() {
        jdbc.sql("UPDATE autopilot_setting SET enabled = true, start_mode = 'MANUAL'").update();
        UUID failed = UUID.randomUUID();
        jdbc.sql("INSERT INTO autopilot_run (id, run_date, status) VALUES (:id, DATE '2026-11-19', 'FAILED')").param("id", failed).update();
        at("08:00").addIdea("glass orchard", null);
        at("08:00").tick();
        assertEquals(1, runs(), "held");
        assertEquals(1, ((java.util.List<?>) at("08:00").status().get("waitingOn")).size());
        at("08:00").dismiss(failed);
        at("08:00").tick();
        assertEquals(2, runs());
    }

    @Test
    void postIsRefusedUnlessReadyAndAnUploadIsRecorded() {
        UUID dream = UUID.randomUUID();
        jdbc.sql("INSERT INTO dream_short (id, idea, status) VALUES (:id, 'x', 'COMPLETED')").param("id", dream).update();
        UUID run = UUID.randomUUID();
        jdbc.sql("INSERT INTO autopilot_run (id, run_date, status, dream_short_id) VALUES (:id, DATE '2026-11-18', 'RUNNING', :d)")
                .param("id", run).param("d", dream).update();
        var ap = at("08:00");
        assertThrows(IllegalStateException.class, () -> ap.post(run, "Title", "", "#Shorts", "private"));
        assertThrows(IllegalArgumentException.class, () -> ap.post(run, "Title", "", "#Shorts", "everyone"));

        assertEquals("failed", ap.saveUpload(run, new AutopilotTypes.YoutubeUploadResult(null, null, "public", "quota")));
        assertEquals("POST_FAILED", jdbc.sql("SELECT status FROM autopilot_run WHERE id = :id").param("id", run).query(String.class).single());
        assertEquals("posted", ap.saveUpload(run, new AutopilotTypes.YoutubeUploadResult("abc", "https://youtube.com/shorts/abc", "private", null)));
        assertEquals("abc", jdbc.sql("SELECT youtube_id FROM autopilot_run WHERE id = :id").param("id", run).query(String.class).single());
        assertEquals(1, jdbc.sql("SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND provider_id = 'youtube_api' AND amount_inr = 0")
                .param("d", dream).query(Integer.class).single());
    }
}
