package com.projectv.dream;

import com.projectv.assets.AssetStore;
import com.projectv.gpu.GpuPodLimits;
import com.projectv.gpu.PodLeases;
import com.projectv.gpu.broker.BrokerConfig;
import com.projectv.gpu.broker.BrokerRecords;
import com.projectv.gpu.broker.GpuBroker;
import com.projectv.gpu.broker.GpuProvider;
import com.projectv.gpu.broker.GpuWallet;
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
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Dream Shorts on the GPU broker (flag = broker) against a real Postgres and a fake provider: nothing is rented. */
@Testcontainers(disabledWithoutDocker = true)
class DreamBrokerRouteTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static DreamService dreams;
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
            assertTrue(req.name().startsWith("projectv-"));
            assertEquals("bash", req.bootArgv().get(0), "the boot command is wrapped by the self-stop guard");
            return new Instance("runpod", "pod-" + created.size(), req.name(), 0.34, "NVIDIA GeForce RTX 4090", "https://pod-1-8000.proxy.runpod.net");
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
                new GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, true), java.time.Clock.systemUTC());
        Path worker = Files.createTempFile("worker", ".py");
        Files.writeString(worker, "print('worker')\n");
        DreamGpu gpu = new DreamGpu(broker, jdbc, new com.projectv.gpu.broker.PreferenceStore.Jdbc(jdbc, json), pricing, "broker", worker.toString(), "img", "", 30, 8000, 10, 76, 900, 2.0);
        dreams = new DreamService(jdbc, json, null, new AssetStore(jdbc), pricing, budget, router, new ClipProgress(),
                new DreamSettings("projectv-zimage", "ZImage", "projectv-wan", "Wan", "http://127.0.0.1:8080", BigDecimal.ZERO, 3.5, "", "lyria-3.5",
                "", "gemini-3.6-flash", "claude:claude-sonnet-5-5", true), gpu);
        jdbc.sql("""
                        INSERT INTO provider (id, name, kind, capabilities_json, config_json, enabled)
                        VALUES ('gpu_pod', 'pod', 'gpu_pod', CAST('["image.dream","video.i2v.dream"]' AS jsonb),
                                CAST('{"quality":6,"budget":"none"}' AS jsonb), true)
                        """).update();
    }

    @Test
    void picturesAndClipsUseOnePodThatIsBookedAndTerminatedOnRelease() {
        UUID dream = UUID.randomUUID();
        UUID shot = UUID.randomUUID();
        jdbc.sql("INSERT INTO dream_short (id, idea, status) VALUES (:id, 'a pink pier', 'RUNNING')").param("id", dream).update();
        jdbc.sql("""
                        INSERT INTO dream_shot (id, dream_short_id, ord, scene, picture_prompt, motion_prompt)
                        VALUES (:id, :d, 0, 'scene', 'picture prompt', 'walk forward')
                        """).param("id", shot).param("d", dream).update();

        var pics = dreams.pictureRequest(dream, null, null);
        assertEquals("https://pod-1-8000.proxy.runpod.net", pics.gpuUrl());
        assertEquals(1800, pics.readyTimeoutSeconds());
        assertEquals("gpu_pod", pics.jobs().getFirst().provider());
        assertEquals(1, created.size());
        dreams.savePictures(dream, pics, new KeyframeResult(List.of(
                new KeyframeOutput(shot.toString(), "s3://b/p1.png", "gpu_pod", "z", 1L, 5.0, null, null, List.of())), 0, 10));
        assertEquals(0, jdbc.sql("SELECT count(*) FROM cost_entry WHERE dream_short_id = :d").param("d", dream).query(Integer.class).single(),
                "no per-job entries: the rental is booked once on release");

        var est = dreams.clipEstimate(dream, null);
        assertNotNull(est.summary());
        assertTrue(est.summary().contains("rented GPU pod"));
        var clips = dreams.clipRequest(dream, null);
        assertEquals("pod", clips.provider());
        assertEquals("https://pod-1-8000.proxy.runpod.net", clips.gpuUrl());
        assertEquals(1, created.size(), "the same pod serves the clips");
        dreams.saveClips(dream, clips, new ClipResult(List.of(
                new ClipOutput(shot.toString(), 1, "dream", "s3://b/c1.mp4", 76, 57, 16, "fast", null, 0)), 1, false));
        assertEquals(2, jdbc.sql("SELECT count(*) FROM routing_decision WHERE dream_short_id = :d AND chosen_provider_id = 'gpu_pod'")
                .param("d", dream).query(Integer.class).single(), "one router decision per pictures / clips batch");
        assertEquals(1, jdbc.sql("SELECT count(*) FROM routing_decision WHERE dream_short_id = :d AND chosen_provider_id = 'runpod'")
                .param("d", dream).query(Integer.class).single(), "one broker decision per rental");

        dreams.status(dream, "MUSIC", "RUNNING", null); // the GPU steps are over: the pod goes
        assertEquals(List.of("pod-1"), terminated);
        assertEquals(1, jdbc.sql("SELECT count(*) FROM cost_entry WHERE dream_short_id = :d AND provider_id = 'runpod'")
                .param("d", dream).query(Integer.class).single());
        assertFalse(created.isEmpty());
    }

    @Test
    void batchOrderCountsOnlyWorkingShortsAhead() {
        UUID me = UUID.randomUUID();
        List<UUID> others = new ArrayList<>();
        for (String[] row : new String[][]{{"CLIPS", "RUNNING"}, {"PICTURES", "RUNNING"}, {"WRITE", "RUNNING"},
                {"WRITE", "WAITING"}, {"MUSIC", "RUNNING"}}) {
            UUID id = others.isEmpty() ? me : UUID.randomUUID(); // the first row is this Short, at its clips
            others.add(id);
            jdbc.sql("INSERT INTO dream_short (id, idea, step, status) VALUES (:id, 'x', :s, :st)")
                    .param("id", id).param("s", row[0]).param("st", row[1]).update();
        }
        try {
            assertEquals(2, dreams.gpuBatchAhead(me, "CLIPS"), "pictures + writing Shorts go first; waiting/music ones never hold it up");
            assertEquals(1, dreams.gpuBatchAhead(me, "PICTURES"), "no pod on yet: wait for the Short still writing");
            assertEquals(0, dreams.gpuBatchAhead(me, "MUSIC"));
        } finally {
            others.forEach(id -> jdbc.sql("DELETE FROM dream_short WHERE id = :id").param("id", id).update());
        }
    }

    @Test
    void theSharedPodEndsAsSoonAsNoShortWaitsForGpuWork() {
        UUID a = UUID.randomUUID();
        UUID b = UUID.randomUUID();
        for (UUID id : List.of(a, b)) {
            jdbc.sql("INSERT INTO dream_short (id, idea, status) VALUES (:id, 'x', 'RUNNING')").param("id", id).update();
            jdbc.sql("""
                            INSERT INTO dream_shot (id, dream_short_id, ord, scene, picture_prompt, motion_prompt)
                            VALUES (:id, :d, 0, 'scene', 'picture prompt', 'walk forward')
                            """).param("id", UUID.randomUUID()).param("d", id).update();
        }
        try {
            dreams.status(a, "PICTURES", "RUNNING", null);
            dreams.status(b, "PICTURES", "RUNNING", null);
            dreams.pictureRequest(a, null, null);
            dreams.pictureRequest(b, null, null);
            int ended = terminated.size();

            dreams.status(a, "CLIPS", "WAITING_REVIEW", null); // a's clips are done; b is still at work
            assertEquals(ended, terminated.size());
            dreams.status(b, "PICTURES", "WAITING_REVIEW", null); // goes on to clips by itself: keep the pod
            assertEquals(ended, terminated.size());
            dreams.status(b, "CLIPS", "WAITING_COST", null); // the owner's cost OK: the pod waits up to 3 min
            assertEquals(ended, terminated.size());
            jdbc.sql("UPDATE dream_short SET updated_at = now() - interval '4 minutes' WHERE id = :id").param("id", b).update();
            dreams.releaseGpuIfNobodyWaits(); // the 30 s check after the grace
            assertEquals(ended + 1, terminated.size());
            assertEquals(0, jdbc.sql("SELECT count(*) FROM gpu_pod_lease WHERE released_at IS NULL")
                    .query(Integer.class).single(), "the lease is released with the pod");
        } finally {
            for (UUID id : List.of(a, b)) {
                jdbc.sql("UPDATE dream_short SET status = 'CANCELLED' WHERE id = :id").param("id", id).update();
            }
        }
    }
}
