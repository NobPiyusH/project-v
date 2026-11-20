package com.projectv.dream;

import com.projectv.gpu.GpuPodLimits;
import com.projectv.gpu.PodLeases;
import com.projectv.gpu.broker.BrokerConfig;
import com.projectv.gpu.broker.BrokerRecords;
import com.projectv.gpu.broker.GpuBroker;
import com.projectv.gpu.broker.GpuProvider;
import com.projectv.gpu.broker.GpuWallet;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.Pricing;
import com.projectv.assets.AssetStore;
import com.projectv.routing.Router;
import com.projectv.video.ClipProgress;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.utility.DockerImageName;
import tools.jackson.databind.json.JsonMapper;

import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * GPU power (owner, 20 Nov 2026): Auto turns the pod off when the GPU steps are over (as before); Manual keeps it on until the
 * owner's button, the idle time, or a cancelled Short. Real Postgres and a fake provider: nothing is rented.
 */
@Testcontainers(disabledWithoutDocker = true)
class DreamGpuPowerTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static DreamService dreams;
    static DreamGpu gpu;
    static final List<String> created = new ArrayList<>();
    static final List<String> terminated = new ArrayList<>();

    static final GpuProvider FAKE = new GpuProvider() {
        public String provider() { return "runpod"; }
        public boolean configured() { return true; }
        public boolean enabledByDefault() { return true; }
        public List<Pod> listPods() { return List.of(); }
        public void terminate(String podId) { terminated.add(podId); }
        public Quote quote(GpuSpec spec) { return new Quote("runpod", true, "HIGH", "NVIDIA GeForce RTX 4090|COMMUNITY", 0.34, ""); }
        public Instance create(CreateRequest req) {
            created.add(req.name());
            return new Instance("runpod", "pod-" + created.size(), req.name(), 0.34, "NVIDIA GeForce RTX 4090", "https://pod-" + created.size() + "-8000.proxy.runpod.net");
        }
        public InstanceStatus status(String id) { return new InstanceStatus(State.RUNNING, null, "RUNNING"); }
    };

    @BeforeAll
    static void setUp() throws Exception {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        jdbc = JdbcClient.create(new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()));
        Pricing pricing = new Pricing("src/test/resources/config");
        BudgetGuard budget = new BudgetGuard(jdbc, pricing);
        var json = JsonMapper.builder().build();
        Router router = new Router(jdbc, json, budget, pricing);
        BrokerConfig cfg = new BrokerConfig(List.of("runpod"), Map.of("runpod", 8.0), 8, List.of("NVIDIA GeForce RTX 4090"),
                24, 48, 80, 0.75, 8000);
        PodLeases.Jdbc leases = new PodLeases.Jdbc(jdbc);
        GpuBroker broker = new GpuBroker(List.of(FAKE), cfg, new GpuWallet(cfg, jdbc, pricing), new BrokerRecords.Jdbc(jdbc, json,
                new com.projectv.ledger.CostLedger(jdbc), pricing), leases, (p, pod, r, h, u) -> { },
                new GpuPodLimits("projectv-", 120, 15, 10, 4, 2.0, true), java.time.Clock.systemUTC());
        Path worker = Files.createTempFile("worker", ".py");
        Files.writeString(worker, "print('worker')\n");
        gpu = new DreamGpu(broker, jdbc, new com.projectv.gpu.broker.PreferenceStore.Jdbc(jdbc, json), pricing, "broker", worker.toString(), "img", "", 30, 8000, 10, 76, 900, 2.0);
        dreams = new DreamService(jdbc, json, null, new AssetStore(jdbc), pricing, budget, router, new ClipProgress(),
                new DreamSettings("projectv-zimage", "ZImage", "projectv-wan", "Wan", "http://127.0.0.1:8080", BigDecimal.ZERO, 3.5, "", "lyria-3.5",
                "", "gemini-3.6-flash", "claude:claude-sonnet-5-5", true), gpu);
        jdbc.sql("""
                        INSERT INTO provider (id, name, kind, capabilities_json, config_json, enabled)
                        VALUES ('gpu_pod', 'pod', 'gpu_pod', CAST('["image.dream","video.i2v.dream"]' AS jsonb),
                                CAST('{"quality":6,"budget":"none"}' AS jsonb), true)
                        """).update();
    }

    /** A Short with one shot and a rented pod (the pictures request rents it). */
    static UUID rentedShort(String hold) {
        UUID dream = UUID.randomUUID();
        jdbc.sql("INSERT INTO dream_short (id, idea, status) VALUES (:id, 'a pink pier', 'RUNNING')").param("id", dream).update();
        jdbc.sql("""
                        INSERT INTO dream_shot (id, dream_short_id, ord, scene, picture_prompt, motion_prompt)
                        VALUES (:id, :d, 0, 'scene', 'picture prompt', 'walk forward')
                        """).param("id", UUID.randomUUID()).param("d", dream).update();
        gpu.setHold(dream, hold, null);
        dreams.pictureRequest(dream, null, null);
        assertTrue(gpu.isOpen(dream));
        return dream;
    }

    static int rentalCostRows(UUID dream) {
        return jdbc.sql("SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND provider_id = 'runpod'")
                .param("d", dream).query(Integer.class).single();
    }

    @Test
    void autoStillTurnsThePodOffWhenTheGpuStepsAreOver() {
        UUID dream = rentedShort("auto");
        assertNull(gpu.offInSeconds(dream, Instant.now()), "no idle timer on Auto");
        dreams.status(dream, "MUSIC", "RUNNING", null);
        assertFalse(gpu.isOpen(dream));
        assertEquals(1, rentalCostRows(dream), "the rental is booked");
    }

    @Test
    void manualKeepsThePodAfterTheGpuStepsAndTurnsItOffWhenIdle() {
        UUID dream = rentedShort("manual");
        int before = terminated.size();
        dreams.status(dream, "MUSIC", "RUNNING", null);
        dreams.status(dream, "FINISH", "RUNNING", null);
        assertTrue(gpu.isOpen(dream), "Manual: the pod stays on after the GPU steps are over");
        assertEquals(before, terminated.size());

        Instant now = Instant.now();
        long left = gpu.offInSeconds(dream, now);
        assertTrue(left > 0 && left <= 600, "about 10 minutes left: " + left);
        gpu.releaseIdleManual(now.plusSeconds(9 * 60));
        assertTrue(gpu.isOpen(dream), "9 minutes idle is not enough");

        gpu.releaseIdleManual(now.plusSeconds(11 * 60));
        assertFalse(gpu.isOpen(dream), "10 minutes idle: turned off");
        assertEquals(before + 1, terminated.size());
        assertEquals(1, rentalCostRows(dream), "booked once, with the idle reason");
    }

    @Test
    void theOwnersButtonTurnsItOffNowAndAnAutoShortIsNeverTouchedByTheIdleSweep() {
        UUID manual = rentedShort("manual");
        UUID auto = rentedShort("auto");
        gpu.releaseIdleManual(Instant.now().plusSeconds(3600));
        assertTrue(gpu.isOpen(auto), "Auto pods are left to the broker, the pod's own guard and the watchdog");
        assertFalse(gpu.isOpen(manual));

        UUID again = rentedShort("manual");
        gpu.turnOff(again);
        assertFalse(gpu.isOpen(again));
        assertEquals(1, rentalCostRows(again));
        assertThrows(IllegalStateException.class, () -> gpu.turnOff(again), "nothing left to turn off");
        gpu.turnOff(auto); // the button works on Auto Shorts too
    }

    @Test
    void aCancelledShortAlwaysReleasesEvenOnManual() {
        UUID dream = rentedShort("manual");
        dreams.status(dream, "CLIPS", "CANCELLED", null);
        assertFalse(gpu.isOpen(dream));
    }

    @Test
    void holdAndIdleMinutesAreValidatedAndClamped() {
        UUID dream = UUID.randomUUID();
        jdbc.sql("INSERT INTO dream_short (id, idea, status) VALUES (:id, 'x', 'RUNNING')").param("id", dream).update();
        assertFalse(gpu.holdManual(dream), "default: Auto");
        assertEquals(10, gpu.idleMinutes(dream));
        assertThrows(IllegalArgumentException.class, () -> gpu.setHold(dream, "sometimes", null));
        gpu.setHold(dream, "MANUAL", 1);
        assertTrue(gpu.holdManual(dream));
        assertEquals(5, gpu.idleMinutes(dream), "at least 5 minutes");
        gpu.setHold(dream, "manual", 999);
        assertEquals(30, gpu.idleMinutes(dream), "at most 30 minutes");
        gpu.setHold(dream, "auto", null);
        assertFalse(gpu.holdManual(dream));
        assertEquals(30, gpu.idleMinutes(dream), "idle minutes kept when not given");
    }
}
