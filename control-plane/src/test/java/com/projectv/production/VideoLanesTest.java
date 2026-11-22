package com.projectv.production;

import com.projectv.assets.AssetStore;
import com.projectv.dream.DreamGpu;
import com.projectv.gpu.broker.BrokerRecords;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import com.projectv.media.MediaTypes.ClipOutput;
import com.projectv.media.MediaTypes.ClipResult;
import com.projectv.planner.StylePresets;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;
import org.testcontainers.postgresql.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;
import tools.jackson.databind.json.JsonMapper;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.anyDouble;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** Video lanes (both default off): the diagram lane in the clip batch, and clips on the GPU broker's pod. Real Postgres, nothing rented. */
@Testcontainers(disabledWithoutDocker = true)
class VideoLanesTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static JsonMapper json;
    static ProductionDeps deps;
    static Pricing pricing;
    UUID video;
    UUID scene;

    @BeforeAll
    static void setUp() throws Exception {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        jdbc = JdbcClient.create(new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()));
        json = JsonMapper.builder().build();
        pricing = new Pricing("src/test/resources/config");
        deps = new ProductionDeps(jdbc, json, null, null, new AssetStore(jdbc), new CostLedger(jdbc), pricing,
                new BudgetGuard(jdbc, pricing), new StylePresets("src/test/resources/config"), "gemini-3.6-flash", "tts", "img",
                "music", "Charon", "app", "cls");
    }

    @BeforeEach
    void seed() {
        video = UUID.randomUUID();
        scene = UUID.randomUUID();
        Map<String, Object> plan = Map.ofEntries(Map.entry("title", "TV"), Map.entry("summary", "s"), Map.entry("format", "long"),
                Map.entry("durationSec", 180), Map.entry("aspectRatio", "16:9"), Map.entry("outputResolution", "1080p"),
                Map.entry("stylePreset", "shorts_punchy"), Map.entry("tone", "calm"), Map.entry("language", "en"),
                Map.entry("needsResearch", false), Map.entry("narration", "full"), Map.entry("voice", ""), Map.entry("music", "support"),
                Map.entry("soundDesign", true), Map.entry("visualTypes", List.of()), Map.entry("shortsCount", 0),
                Map.entry("mustInclude", List.of()), Map.entry("rationale", ""));
        jdbc.sql("INSERT INTO video (id, prompt, status, plan_json) VALUES (:id, 'p', 'COMPLETED', CAST(:plan AS jsonb))")
                .param("id", video).param("plan", json.writeValueAsString(Map.of("plan", plan))).update();
        jdbc.sql("INSERT INTO scene (id, video_id, ord, title) VALUES (:s, :v, 0, 'S')").param("s", scene).param("v", video).update();
    }

    private UUID shot(int ord, double seconds, String promptJson, boolean keyframe) {
        UUID shot = UUID.randomUUID();
        UUID image = null;
        if (keyframe) {
            image = UUID.randomUUID();
            jdbc.sql("INSERT INTO asset (id, video_id, kind, uri_hot) VALUES (:a, :v, 'image', 's3://b/k.png')")
                    .param("a", image).param("v", video).update();
        }
        jdbc.sql("""
                        INSERT INTO shot (id, scene_id, ord, duration_target_sec, description, prompt_json, keyframe_asset_id)
                        VALUES (:id, :s, :o, :d, 'desc', CAST(:p AS jsonb), :k)
                        """).param("id", shot).param("s", scene).param("o", ord).param("d", seconds).param("p", promptJson)
                .param("k", image, java.sql.Types.OTHER).update();
        return shot;
    }

    private static final String DIAGRAM = "{\"lane\":\"diagram\",\"diagramSpec\":{\"title\":\"CRT\",\"duration\":8.0,\"elements\":[],\"steps\":[]}}";
    private static final String AI = "{\"motionPrompt\":\"slow push in\"}";

    private VideoStage stage(VideoGpu gpu) {
        var s = new VideoStage(deps, "projectv-wan", "Wan", "http://127.0.0.1:8080", BigDecimal.ZERO, 2, "wan", "wan", "omni-model", null);
        s.setVideoGpu(gpu);
        return s;
    }

    @Test
    void diagramShotsAreDrawnNotGeneratedAndCountedInTheEstimate() {
        shot(0, 4.0, AI, true);
        UUID d = shot(1, 8.0, DIAGRAM, false);
        VideoStage s = stage(null);
        assertEquals(2, s.estimate(video, VideoStage.DRAFT, null).clips(), "1 AI clip + 1 diagram: the stage is not skipped");
        var req = s.clipRequest(video, VideoStage.DRAFT, null);
        var drawn = req.jobs().stream().filter(j -> "diagram".equals(j.mode())).toList();
        assertEquals(1, drawn.size());
        assertEquals(d.toString(), drawn.getFirst().shotId());
        assertEquals(192, drawn.getFirst().numFrames(), "8 s at 24 fps");
        assertEquals(1920, drawn.getFirst().width());
        assertTrue(drawn.getFirst().prompt().contains("\"title\":\"CRT\""), "the spec travels as the job prompt");
        assertEquals(1, req.jobs().stream().filter(j -> !"diagram".equals(j.mode())).count());
        assertEquals(0, s.estimate(video, VideoStage.FINAL, null).clips(), "a drawn clip is final quality: no final render");
    }

    @Test
    void aDiagramOnlyBatchNeedsNoGpuAndNoApprovalMoney() {
        shot(0, 8.0, DIAGRAM, false);
        var req = stage(null).clipRequest(video, VideoStage.DRAFT, null);
        assertEquals(1, req.jobs().size());
        assertEquals(0, req.estimateInr().signum());
        assertEquals(0, req.containers());
    }

    @Test
    void savedDiagramClipIsAnOwnRenderWithProvenanceAndNoModalCost() {
        UUID d = shot(0, 6.0, DIAGRAM, false);
        VideoStage s = stage(null);
        var req = s.clipRequest(video, VideoStage.DRAFT, null);
        var out = new ClipOutput(d.toString(), 1, "draft", "s3://b/diagram.mp4", 0, 144, 24, "diagram", null, 0);
        s.saveClips(video, req, new ClipResult(List.of(out), 0, false, 0));
        assertEquals("diagram_manim", jdbc.sql("SELECT p.provider FROM asset a JOIN provenance p ON p.id = a.provenance_id WHERE a.shot_id = :s")
                .param("s", d).query(String.class).single());
        assertEquals(0, jdbc.sql("SELECT count(*) FROM cost_entry WHERE video_id = :v").param("v", video).query(Integer.class).single());
        assertFalse(jdbc.sql("SELECT draft_clip_asset_id FROM shot WHERE id = :s").param("s", d).query(UUID.class).optional().isEmpty());
        assertEquals(0, s.diagramJobs(video, VideoStage.DRAFT, null).size(), "already drawn: not redrawn unless picked");
    }

    @Test
    void brokerDraftsRunOnThePodAndDefaultStaysUnchanged() {
        shot(0, 4.0, AI, true);
        VideoGpu gpu = Mockito.mock(VideoGpu.class);
        when(gpu.clips(VideoStage.DRAFT)).thenReturn(true);
        when(gpu.clipSeconds(eq(video), any())).thenReturn(1200.0);
        when(gpu.inr(anyDouble())).thenReturn(new BigDecimal("58.50"));
        when(gpu.session(eq(video), anyDouble())).thenReturn(new DreamGpu.Session("http://worker:tok@pod:8000", 1800));
        VideoStage s = stage(gpu);

        var est = s.estimate(video, VideoStage.DRAFT, null);
        assertEquals(1, est.clips());
        assertTrue(est.summary().contains("rented GPU pod"));
        verify(gpu, never()).session(any(), anyDouble()); // an estimate never rents

        var req = s.clipRequest(video, VideoStage.DRAFT, null);
        assertEquals("pod", req.provider());
        assertEquals("http://worker:tok@pod:8000", req.gpuUrl());
        assertEquals(1, req.containers());
        assertEquals(0, new BigDecimal("58.50").compareTo(req.estimateInr()));

        // finals are not on the broker here: the unchanged Omni/Wan route (no pod)
        when(gpu.clips(VideoStage.FINAL)).thenReturn(false);
        assertEquals(0, s.estimate(video, VideoStage.FINAL, null).clips());
        // default (no VideoGpu): the Modal Wan request, no pod
        assertNull(stage(null).clipRequest(video, VideoStage.DRAFT, null).gpuUrl());
    }

    @Test
    void podClipsAreSavedWithoutPerClipModalCostAndGpuIsReleased() {
        UUID a = shot(0, 4.0, AI, true);
        VideoGpu gpu = Mockito.mock(VideoGpu.class);
        when(gpu.clips(VideoStage.DRAFT)).thenReturn(true);
        when(gpu.clipSeconds(eq(video), any())).thenReturn(600.0);
        when(gpu.inr(anyDouble())).thenReturn(new BigDecimal("30.00"));
        when(gpu.session(eq(video), anyDouble())).thenReturn(new DreamGpu.Session("http://pod", 600));
        VideoStage s = stage(gpu);
        var req = s.clipRequest(video, VideoStage.DRAFT, null);
        var out = new ClipOutput(a.toString(), 1, "draft", "s3://b/c.mp4", 80, 65, 16, "fast", null, 0);
        s.saveClips(video, req, new ClipResult(List.of(out), 1, false, 1));
        assertEquals("gpu_pod", jdbc.sql("SELECT p.provider FROM asset a JOIN provenance p ON p.id = a.provenance_id WHERE a.shot_id = :s")
                .param("s", a).query(String.class).single());
        assertEquals(0, jdbc.sql("SELECT count(*) FROM cost_entry WHERE video_id = :v AND provider_id LIKE 'modal%'").param("v", video)
                .query(Integer.class).single(), "the rental is booked when the broker ends it, not per clip or per start-up");
        s.releaseGpu(video, "clip batch saved");
        verify(gpu).release(video, "clip batch saved");
    }

    @Test
    void aVideosRentalIsBookedOnTheVideosLedger() {
        var records = new BrokerRecords.Jdbc(jdbc, json, new CostLedger(jdbc), pricing);
        records.cost(video, "runpod", 0.5, 0.34, "GPU rental pod-1 ended: clip batch saved");
        records.decision(video, "video.i2v.dream", List.of(), "runpod", "cheapest"); // a Video: no dream routing row, no FK failure
        var row = jdbc.sql("SELECT video_id, dream_short_id, unit_type FROM cost_entry WHERE video_id = :v").param("v", video)
                .query().singleRow();
        assertEquals(video, row.get("video_id"));
        assertNull(row.get("dream_short_id"));
        assertEquals("gpu_hour", row.get("unit_type"));
    }
}
