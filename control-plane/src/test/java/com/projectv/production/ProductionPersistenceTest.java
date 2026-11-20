package com.projectv.production;

import com.projectv.assets.AssetStore;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.CostLedger;
import com.projectv.ledger.Pricing;
import com.projectv.media.MediaTypes.NarrationClip;
import com.projectv.media.MediaTypes.NarrationResult;
import com.projectv.media.MediaTypes.Usage;
import com.projectv.planner.StylePresets;
import com.projectv.production.ProductionService.StoredLine;
import com.projectv.production.ProductionService.StoredScript;
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
import tools.jackson.databind.json.JsonMapper;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Real SQL against a real (throwaway) Postgres with every migration applied: the parts the workflow tests fake out.
 * Skipped automatically when Docker isn't running.
 */
@Testcontainers(disabledWithoutDocker = true)
class ProductionPersistenceTest {

    @Container
    static final PostgreSQLContainer PG = new PostgreSQLContainer(
            DockerImageName.parse("pgvector/pgvector:0.8.7-pg17-trixie").asCompatibleSubstituteFor("postgres"));

    static JdbcClient jdbc;
    static JsonMapper json;
    static VoiceStage voice;
    static ShotStage shots;
    static Inspector inspector;
    static CreativeDirector creative;
    static VideoStage clips;
    static FootageStage footage;
    static AssemblyStage assembly;
    static FinishStage finish;
    static SharpenStage sharpen;
    static BudgetGuard budget;
    static ProducerService producer;

    UUID video;

    @BeforeAll
    static void setUp() throws Exception {
        Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
        var ds = new DriverManagerDataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword());
        jdbc = JdbcClient.create(ds);
        json = JsonMapper.builder().build();
        Pricing pricing = new Pricing("../config");
        budget = new BudgetGuard(jdbc, pricing);
        var deps = new ProductionDeps(jdbc, json, null, null, new AssetStore(jdbc), new CostLedger(jdbc), pricing, budget,
                new StylePresets("../config"), "gemini-3.6-flash", "gemini-3.8-flash-tts", "img", "music", "Charon", "app", "cls");
        voice = new VoiceStage(deps);
        producer = new ProducerService(deps, "gemini-3.6-flash");
        shots = new ShotStage(deps);
        inspector = new Inspector(deps);
        creative = new CreativeDirector(deps, null, "gemini-3.6-flash");
        clips = new VideoStage(deps, "projectv-wan", "Wan", "http://127.0.0.1:8080", BigDecimal.ZERO, 2, "wan", "wan", "gemini-omni-flash-preview", null);
        footage = new FootageStage(deps);
        assembly = new AssemblyStage(deps);
        finish = new FinishStage(deps, "http://127.0.0.1:8080", BigDecimal.ZERO);
        sharpen = new SharpenStage(deps, finish, "http://127.0.0.1:8080", BigDecimal.ZERO);
    }

    @BeforeEach
    void seedVideo() {
        video = UUID.randomUUID();
        Map<String, Object> plan = Map.ofEntries(Map.entry("title", "Test"), Map.entry("summary", "s"),
                Map.entry("format", "short"), Map.entry("durationSec", 60), Map.entry("aspectRatio", "9:16"),
                Map.entry("outputResolution", "1080p"), Map.entry("stylePreset", "shorts_punchy"), Map.entry("tone", "calm"),
                Map.entry("language", "en"), Map.entry("needsResearch", false), Map.entry("narration", "full"),
                Map.entry("voice", ""), Map.entry("music", "support"), Map.entry("soundDesign", true),
                Map.entry("visualTypes", List.of()), Map.entry("shortsCount", 0), Map.entry("mustInclude", List.of()),
                Map.entry("rationale", ""));
        jdbc.sql("INSERT INTO video (id, prompt, status, plan_json) VALUES (:id, 'p', 'COMPLETED', CAST(:plan AS jsonb))")
                .param("id", video).param("plan", json.writeValueAsString(Map.of("plan", plan))).update();
        StoredScript script = new StoredScript(1, List.of(new StoredLine(0, "A star is born.", "Act 1", "hook", true),
                new StoredLine(1, "Silence.", "Act 1", "beat", false)), "Charon", "calm", "strings", null, null);
        jdbc.sql("INSERT INTO script (id, video_id, version, beat_sheet_json) VALUES (:id, :v, 1, CAST(:b AS jsonb))")
                .param("id", UUID.randomUUID()).param("v", video).param("b", json.writeValueAsString(script)).update();
    }

    private StoredLine line(int index) {
        String s = jdbc.sql("SELECT beat_sheet_json::text FROM script WHERE video_id = :v").param("v", video).query(String.class).single();
        return json.readValue(s, StoredScript.class).lines().stream().filter(l -> l.index() == index).findFirst().orElseThrow();
    }

    private static NarrationResult take(String uri, double seconds) {
        return new NarrationResult(List.of(new NarrationClip(0, uri, seconds, new Usage(10, 1000, 0, 1010), 1, true, "coverage 100%")),
                "gemini-3.8-flash-tts", "Charon");
    }

    @Test
    void inspectorFindsTheMissingVoiceAndStoresOneReportPerAttempt() {
        // The seeded script's spoken line has no recording yet.
        var report = inspector.evaluate(video, com.projectv.workflow.Stage.NARRATION, 1,
                com.projectv.media.MediaTypes.MediaInspectResult.NONE);
        assertEquals("FAIL", report.verdict());
        assertTrue(report.notes().contains("line 1: This line has no voice recording."), report.notes());
        // Re-inspecting the same attempt replaces the row; a new attempt adds one.
        inspector.evaluate(video, com.projectv.workflow.Stage.NARRATION, 1, null);
        voice.saveNarration(video, take("s3://projectv/l0.wav", 2.0));
        var second = inspector.evaluate(video, com.projectv.workflow.Stage.NARRATION, 2,
                new com.projectv.media.MediaTypes.MediaInspectResult(List.of(new com.projectv.media.MediaTypes.Finding(
                        "WARN", "CLIPPING", "Peak -0.1 dBTP", "line 1")), List.of("line 1")));
        assertEquals("WARN", second.verdict());
        assertEquals(List.of("FAIL", "WARN"), jdbc.sql("SELECT verdict FROM inspection WHERE video_id = :v ORDER BY attempt")
                .param("v", video).query(String.class).list());
    }

    @Test
    void allMigrationsApplyAndLicencesAreSeeded() {
        assertEquals(12, jdbc.sql("SELECT count(*) FROM licence").query(Integer.class).single()); // main 11 + owner music (V22)
        assertEquals(32, jdbc.sql("SELECT count(*) FROM flyway_schema_history WHERE success").query(Integer.class).single()); // main V1-V19 + Dream Shorts V20-V25 + autopilot V26 + Dream check V27 + Dream lessons V28 + stop rule V29 + Scout cache V30 + GPU watchdog V31 + Dream GPU pick V32
    }

    @Test
    void lineRedosKeepTheVoiceTheVideoHasAndElevenLabsCreditsAreTracked() {
        assertEquals("auto", voice.currentProvider(video), "no narration yet");
        voice.saveNarration(video, take("s3://projectv/videos/x/narration/line-000.wav", 2.0));
        assertEquals("gemini", voice.currentProvider(video));
        voice.saveNarration(video, new NarrationResult(List.of(new NarrationClip(0, "s3://projectv/videos/x/narration/line-000.wav",
                2.1, null, 1, true, "coverage 100%")), "elevenlabs:eleven_v4_turbo", "George", 42));
        assertEquals("elevenlabs", voice.currentProvider(video));
        assertEquals("elevenlabs", voice.lineNarrationRequest(video, List.of(0), null).voiceProvider());
        assertEquals(1, jdbc.sql("SELECT count(*) FROM cost_entry WHERE video_id = :v AND provider_id = 'elevenlabs' AND units = 42")
                .param("v", video).query(Integer.class).single());
        assertEquals(1, jdbc.sql("""
                        SELECT count(*) FROM asset a JOIN provenance p ON p.id = a.provenance_id
                        WHERE a.video_id = :v AND p.licence_id = '00000000-0000-0000-0000-00000000a008'
                        """).param("v", video).query(Integer.class).single(), "free-plan licence recorded");
    }

    @Test
    void editVoiceAndSwitchTakesRoundTripThroughTheDatabase() {
        StoredLine edited = voice.editLine(video, 0, "A star ignites.", null);
        assertTrue(edited.edited());
        assertEquals(1, jdbc.sql("SELECT count(*) FROM preference_event WHERE video_id = :v AND kind = 'edit_diff'")
                .param("v", video).query(Integer.class).single(), "the edit is kept as taste-learning data");

        voice.saveLineNarration(video, take("s3://b/t1.wav", 4.0), "in a calm voice");
        StoredLine first = line(0);
        assertNotNull(first.audioAssetId());
        assertFalse(first.needsVoice());
        assertEquals("READY", first.voiceStatus());

        voice.saveLineNarration(video, take("s3://b/t2.wav", 5.0), null);
        StoredLine second = line(0);
        assertEquals(2, second.audioVersions().size());
        voice.selectLineVoice(video, 0, first.audioAssetId());
        assertEquals(first.audioAssetId(), line(0).audioAssetId(), "switched back to the first take");

        // Every take: an asset + provenance with a licence, and a priced cost entry.
        assertEquals(2, jdbc.sql("""
                        SELECT count(*) FROM asset a JOIN provenance p ON p.id = a.provenance_id
                        WHERE a.video_id = :v AND a.kind = 'audio' AND p.licence_id IS NOT NULL AND p.asset_id = a.id
                        """).param("v", video).query(Integer.class).single());
        BigDecimal spent = jdbc.sql("SELECT sum(amount_inr) FROM cost_entry WHERE video_id = :v").param("v", video)
                .query(BigDecimal.class).single();
        assertTrue(spent.signum() > 0);
        assertThrows(IllegalStateException.class, () -> voice.selectLineVoice(video, 0, UUID.randomUUID()));
    }

    @Test
    void creativeDirectorKeepsItsFixAsASuggestionUntilTheShotIsRedone() {
        UUID scene = UUID.randomUUID();
        UUID shot = UUID.randomUUID();
        jdbc.sql("INSERT INTO scene (id, video_id, ord, title) VALUES (:s, :v, 0, 'S')").param("s", scene).param("v", video).update();
        jdbc.sql("""
                        INSERT INTO shot (id, scene_id, ord, prompt_json)
                        VALUES (:id, :s, 0, CAST('{"keyframePrompt":"a flashlight beam","lineIndex":0}' AS jsonb))
                        """).param("id", shot).param("s", scene).update();
        var review = new com.projectv.agents.AgentTypes.CreativeReview(
                new com.projectv.agents.AgentTypes.VideoCritique(4, 4, 4, 4, "", ""), List.of(),
                List.of(new com.projectv.agents.AgentTypes.ShotCritique(1, 1, 4, 4, true, false, "a torch, not starlight",
                        "ancient light leaving a distant galaxy")));
        var findings = creative.saveImageReview(video, 1, new com.projectv.media.MediaTypes.ImageReviewResult(review,
                "gemini-3.6-flash", new Usage(5000, 800, 0, 5800), 1));
        assertEquals(1, findings.size());
        assertEquals(shot.toString(), findings.getFirst().targetId());
        String prompt = jdbc.sql("SELECT prompt_json::text FROM shot WHERE id = :s").param("s", shot).query(String.class).single();
        assertTrue(prompt.contains("\"keyframePrompt\": \"a flashlight beam\""), "not changed before the redo: " + prompt);
        assertTrue(prompt.contains("suggestedPrompt"));
        assertEquals(1, jdbc.sql("SELECT count(*) FROM creative_review WHERE video_id = :v").param("v", video)
                .query(Integer.class).single());
        assertTrue(jdbc.sql("SELECT count(*) FROM cost_entry WHERE video_id = :v AND note LIKE 'CREATIVE_REVIEW%'")
                .param("v", video).query(Integer.class).single() == 1);

        creative.applySuggestedPrompts(video, List.of(shot));
        prompt = jdbc.sql("SELECT prompt_json::text FROM shot WHERE id = :s").param("s", shot).query(String.class).single();
        assertTrue(prompt.contains("\"keyframePrompt\": \"ancient light leaving a distant galaxy\""), prompt);
        assertTrue(prompt.contains("\"originalPrompt\": \"a flashlight beam\""), prompt);
        assertFalse(prompt.contains("suggestedPrompt"), prompt);
    }

    private UUID shotWithKeyframe(int ord, double seconds) {
        UUID scene = jdbc.sql("SELECT id FROM scene WHERE video_id = :v").param("v", video).query(UUID.class).optional()
                .orElseGet(() -> {
                    UUID id = UUID.randomUUID();
                    jdbc.sql("INSERT INTO scene (id, video_id, ord, title) VALUES (:s, :v, 0, 'S')").param("s", id).param("v", video).update();
                    return id;
                });
        UUID shot = UUID.randomUUID();
        UUID image = UUID.randomUUID();
        jdbc.sql("INSERT INTO asset (id, video_id, kind, uri_hot, shot_id) VALUES (:a, :v, 'image', 's3://b/k.png', NULL)")
                .param("a", image).param("v", video).update();
        jdbc.sql("""
                        INSERT INTO shot (id, scene_id, ord, duration_target_sec, description, prompt_json, keyframe_asset_id)
                        VALUES (:id, :s, :o, :d, 'stars wheel overhead', CAST('{"motionPrompt":"slow push in"}' AS jsonb), :k)
                        """).param("id", shot).param("s", scene).param("o", ord).param("d", seconds).param("k", image).update();
        return shot;
    }

    @Test
    void clipsAreEstimatedRenderedAndCostedPerShot() {
        assertEquals(33, VideoStage.frames(1.2), "at least ~2 s");
        assertEquals(49, VideoStage.frames(3.0));
        assertEquals(81, VideoStage.frames(9.0), "Wan's ~5 s limit");
        UUID a = shotWithKeyframe(0, 3.0);
        UUID b = shotWithKeyframe(1, 6.0);

        var estimate = clips.estimate(video, VideoStage.DRAFT, null);
        assertEquals(2, estimate.clips());
        assertTrue(estimate.needsApproval(), "auto-approve is 0: the owner always says yes first");
        assertEquals(0, clips.estimate(video, VideoStage.FINAL, null).clips(), "no finals before drafts");

        var request = clips.clipRequest(video, VideoStage.DRAFT, null);
        var job = request.jobs().getFirst();
        assertEquals(480, job.width(), "vertical Short: 480x832 drafts");
        assertEquals(832, job.height());
        assertEquals("fast", job.mode());
        assertEquals(1, request.containers(), "drafts share one GPU: one start-up");
        assertEquals(1, VideoStage.containers(VideoStage.DRAFT, 24));
        assertEquals(4, VideoStage.containers(VideoStage.FINAL, 24));
        assertTrue(job.prompt().startsWith("slow push in. stars wheel overhead. Visual style:"), job.prompt());

        var result = new com.projectv.media.MediaTypes.ClipResult(List.of(
                new com.projectv.media.MediaTypes.ClipOutput(a.toString(), 1, "draft", "s3://b/a.mp4", 50, 49, 16, "fast", null, 0),
                new com.projectv.media.MediaTypes.ClipOutput(b.toString(), 1, "draft", null, 0, 0, 0, "fast", "stopped", 0)), 2, true);
        var saved = clips.saveClips(video, request, result);
        assertTrue(saved.summary().startsWith("1/2 draft clips"), saved.summary());
        assertEquals("READY", jdbc.sql("SELECT clip_status FROM shot WHERE id = :s").param("s", a).query(String.class).single());
        assertEquals("STOPPED", jdbc.sql("SELECT clip_status FROM shot WHERE id = :s").param("s", b).query(String.class).single());
        assertEquals(1, jdbc.sql("""
                        SELECT count(*) FROM asset x JOIN provenance p ON p.id = x.provenance_id
                        WHERE x.shot_id = :s AND x.kind = 'video' AND x.clip_kind = 'draft' AND p.licence_id = :l
                        """).param("s", a).param("l", com.projectv.assets.AssetStore.LICENCE_WAN).query(Integer.class).single());
        assertEquals(2, jdbc.sql("SELECT count(*) FROM cost_entry WHERE video_id = :v AND provider_id = 'modal_h100'")
                .param("v", video).query(Integer.class).single(), "the clip + the container start-up");

        assertEquals(1, clips.estimate(video, VideoStage.FINAL, null).clips(), "a final for the shot with a draft");
        clips.setFinalWanted(a, false);
        assertEquals(0, clips.estimate(video, VideoStage.FINAL, null).clips(), "kept draft: no final");
        assertEquals(1, clips.clipVersions(a).size());
    }

    @Test
    void pickedNasaFootageReplacesTheGeneratedClipForThatShot() {
        UUID sun = shotWithKeyframe(0, 3.0);
        UUID other = shotWithKeyframe(1, 3.0);
        jdbc.sql("UPDATE shot SET prompt_json = CAST('{\"footageQuery\":\"solar flare SDO\"}' AS jsonb) WHERE id = :s")
                .param("s", sun).update();
        var search = footage.searchRequest(video, null, null);
        assertEquals(1, search.queries().size(), "only the shot the Director marked");
        var candidate = new com.projectv.media.MediaTypes.FootageCandidate("GSFC_SDO_1", "Solar flare", "d",
                "GSFC, Joy Ng", "2016-06-24", "t", "p");
        var saved = footage.saveCandidates(video, new com.projectv.media.MediaTypes.FootageSearchResponse(List.of(
                new com.projectv.media.MediaTypes.FootageSearchResult(sun.toString(), "solar flare SDO", List.of(candidate), null))));
        assertTrue(saved.summary().startsWith("Found 1 free real clip (NASA, Wikimedia Commons)"), saved.summary());

        var req = footage.fetchRequest(video, sun, "GSFC_SDO_1", null);
        assertEquals("9:16", req.aspectRatio());
        var result = footage.saveFootage(video, req, new com.projectv.media.MediaTypes.FootageFetchResult(
                "s3://b/sun.mp4", 1080, 1920, 3.0, 1920, 1080, 1.78, 35.0, "https://images-assets.nasa.gov/x~orig.mp4"));
        assertTrue(result.summary().contains("credit: NASA / GSFC, Joy Ng"), result.summary());
        assertTrue(result.summary().contains("may look soft"), "a 1.78x enlargement is flagged");
        assertEquals(1, jdbc.sql("""
                        SELECT count(*) FROM asset a JOIN provenance p ON p.id = a.provenance_id
                        WHERE a.shot_id = :s AND a.clip_kind = 'footage' AND p.licence_id = :l
                        """).param("s", sun).param("l", com.projectv.assets.AssetStore.LICENCE_NASA).query(Integer.class).single());

        var jobs = clips.clipRequest(video, VideoStage.DRAFT, null).jobs();
        assertEquals(List.of(other.toString()), jobs.stream().map(j -> j.shotId()).toList(), "no GPU clip for the footage shot");
        footage.clearFootage(sun);
        assertEquals(2, clips.clipRequest(video, VideoStage.DRAFT, null).jobs().size(), "back to a generated clip");
    }

    @Test
    void heroShotsGetTwoDraftTakesAndTheFirstGoodOneIsUsed() {
        UUID hero = shotWithKeyframe(0, 3.0);
        UUID plain = shotWithKeyframe(1, 3.0);
        jdbc.sql("UPDATE shot SET priority = 'hero' WHERE id = :s").param("s", hero).update();
        var request = clips.clipRequest(video, VideoStage.DRAFT, null);
        assertEquals(List.of(hero + ":1", hero + ":2", plain + ":1"),
                request.jobs().stream().map(j -> j.shotId() + ":" + j.take()).toList());
        assertTrue(request.jobs().get(0).seed() != request.jobs().get(1).seed(), "different seeds per take");

        var result = new com.projectv.media.MediaTypes.ClipResult(List.of(
                new com.projectv.media.MediaTypes.ClipOutput(hero.toString(), 1, "draft", null, 10, 0, 0, "fast", "CUDA error", 0),
                new com.projectv.media.MediaTypes.ClipOutput(hero.toString(), 2, "draft", "s3://b/h2.mp4", 12, 49, 16, "fast", null, 0),
                new com.projectv.media.MediaTypes.ClipOutput(plain.toString(), 1, "draft", "s3://b/p1.mp4", 12, 49, 16, "fast", null, 0)), 1, false);
        var saved = clips.saveClips(video, request, result);
        assertTrue(saved.summary().contains("1 hero shot has takes to compare"), saved.summary());
        UUID inUse = jdbc.sql("SELECT draft_clip_asset_id FROM shot WHERE id = :s").param("s", hero).query(UUID.class).single();
        assertEquals("s3://b/h2.mp4", jdbc.sql("SELECT uri_hot FROM asset WHERE id = :a").param("a", inUse)
                .query(String.class).single(), "take 1 failed, so take 2 is the one in use");
        assertEquals(1, clips.estimate(video, VideoStage.FINAL, null).clips() - 1, "finals: one per shot, not per take");
    }

    private UUID videoAsset(UUID shot, String kind, String uri) {
        UUID a = UUID.randomUUID();
        jdbc.sql("INSERT INTO asset (id, video_id, kind, uri_hot, shot_id, clip_kind) VALUES (:a, :v, 'video', :u, :s, :k)")
                .param("a", a).param("v", video).param("u", uri).param("s", shot).param("k", kind).update();
        return a;
    }

    @Test
    void theMasterUsesFootageThenFinalThenDraftAndRefusesShotsWithoutVideo() {
        UUID s1 = shotWithKeyframe(0, 3.0);
        UUID s2 = shotWithKeyframe(1, 3.0);
        jdbc.sql("UPDATE shot SET prompt_json = CAST('{\"startSec\": 3.0, \"lineIndex\": 0}' AS jsonb) WHERE id = :s")
                .param("s", s2).update();
        UUID draft1 = videoAsset(s1, "draft", "s3://b/d1.mp4");
        jdbc.sql("UPDATE shot SET draft_clip_asset_id = :a WHERE id = :s").param("a", draft1).param("s", s1).update();
        var refused = assertThrows(IllegalStateException.class, () -> assembly.assemblyRequest(video));
        assertTrue(refused.getMessage().startsWith("1 shot has no moving video yet (shot 2)"), refused.getMessage());

        UUID draft2 = videoAsset(s2, "draft", "s3://b/d2.mp4");
        UUID final2 = videoAsset(s2, "final", "s3://b/f2.mp4");
        UUID nasa1 = videoAsset(s1, "footage", "s3://b/nasa1.mp4");
        jdbc.sql("UPDATE shot SET draft_clip_asset_id = :d, final_clip_asset_id = :f WHERE id = :s")
                .param("d", draft2).param("f", final2).param("s", s2).update();
        jdbc.sql("UPDATE shot SET footage_asset_id = :a WHERE id = :s").param("a", nasa1).param("s", s1).update();
        var req = assembly.assemblyRequest(video);
        assertEquals(List.of("footage:s3://b/nasa1.mp4", "final:s3://b/f2.mp4"),
                req.shots().stream().map(x -> x.kind() + ":" + x.uri()).toList());
        assertEquals("9:16", req.aspectRatio());

        var saved = assembly.saveMaster(video, new com.projectv.media.MediaTypes.AssemblyResult("s3://b/master.mp4",
                "s3://b/pkg.zip", "s3://b/c.srt", 6.0, 1080, 1920, 30,
                List.of(Map.of("shot", 1, "fit", "trimmed"), Map.of("shot", 2, "fit", "slowed 1.80x (looks slow-motion)")), 4, 1));
        assertTrue(saved.summary().contains("1 shot slowed to fill its slot (1 noticeably)"), saved.summary());
        assertEquals(1, jdbc.sql("""
                        SELECT count(*) FROM video v JOIN asset m ON m.id = v.master_asset_id
                        JOIN asset p ON p.id = v.master_package_asset_id JOIN asset c ON c.id = v.master_captions_asset_id
                        WHERE v.id = :v AND p.kind = 'timeline' AND c.kind = 'subtitle'
                        """).param("v", video).query(Integer.class).single());
    }

    @Test
    void smoothingFollowsTheClipInUseAndTheEditUsesItOnlyWhileFresh() {
        UUID s1 = shotWithKeyframe(0, 3.0);
        UUID d1 = videoAsset(s1, "draft", "s3://b/d1.mp4");
        jdbc.sql("UPDATE shot SET draft_clip_asset_id = :a, prompt_json = CAST('{\"startSec\": 0}' AS jsonb) WHERE id = :s")
                .param("a", d1).param("s", s1).update();
        var est = finish.estimate(video);
        assertEquals(1, est.clips());
        assertTrue(est.needsApproval());
        var req = finish.request(video);
        finish.save(video, req, new com.projectv.media.MediaTypes.SmoothResult(List.of(new com.projectv.media.MediaTypes.SmoothOutput(
                s1.toString(), d1.toString(), "s3://b/d1-smooth.mp4", 4.0, 72, 24, null)), 1, false));
        assertEquals(0, finish.estimate(video).clips(), "already smooth from this clip");
        var shots = assembly.assemblyRequest(video).shots();
        assertEquals("draft+smooth:s3://b/d1-smooth.mp4", shots.getFirst().kind() + ":" + shots.getFirst().uri());

        UUID d2 = videoAsset(s1, "draft", "s3://b/d2.mp4");   // the owner picks another take
        jdbc.sql("UPDATE shot SET draft_clip_asset_id = :a WHERE id = :s").param("a", d2).param("s", s1).update();
        shots = assembly.assemblyRequest(video).shots();
        assertEquals("draft:s3://b/d2.mp4", shots.getFirst().kind() + ":" + shots.getFirst().uri(), "stale smooth clip not used");
        assertEquals(1, finish.estimate(video).clips(), "the new take needs smoothing");
    }

    @Test
    void sharpeningWorksOnTheSmoothedClipAndTheEditUsesItOnlyWhileFresh() {
        UUID s1 = shotWithKeyframe(0, 3.0);
        UUID d1 = videoAsset(s1, "draft", "s3://b/d1.mp4");
        jdbc.sql("UPDATE shot SET draft_clip_asset_id = :a, prompt_json = CAST('{\"startSec\": 0}' AS jsonb) WHERE id = :s")
                .param("a", d1).param("s", s1).update();
        assertEquals(1, sharpen.estimate(video).clips(), "a shot about to be smoothed is counted once");
        var smoothReq = finish.request(video);
        finish.save(video, smoothReq, new com.projectv.media.MediaTypes.SmoothResult(List.of(new com.projectv.media.MediaTypes.SmoothOutput(
                s1.toString(), d1.toString(), "s3://b/d1-smooth.mp4", 4.0, 72, 24, null)), 1, false));
        var req = sharpen.request(video);
        assertEquals(1, req.jobs().size());
        assertEquals("s3://b/d1-smooth.mp4", req.jobs().getFirst().uri(), "sharpening starts from the smoothed clip");
        String smoothId = req.jobs().getFirst().sourceAssetId();
        var saved = sharpen.save(video, req, new com.projectv.media.MediaTypes.SmoothResult(List.of(new com.projectv.media.MediaTypes.SmoothOutput(
                s1.toString(), smoothId, "s3://b/d1-sharp.mp4", 95.0, 72, 24, null)), 1, false));
        assertTrue(saved.summary().startsWith("1/1 clips sharpened"), saved.summary());
        assertEquals(0, sharpen.estimate(video).clips(), "already sharp from the clip in use");
        assertEquals(1, jdbc.sql("SELECT count(*) FROM cost_entry WHERE video_id = :v AND provider_id = 'modal_h100'")
                .param("v", video).query(Integer.class).single());
        var shots = assembly.assemblyRequest(video).shots();
        assertEquals("draft+smooth+sharp:s3://b/d1-sharp.mp4", shots.getFirst().kind() + ":" + shots.getFirst().uri());

        UUID d2 = videoAsset(s1, "draft", "s3://b/d2.mp4");   // the owner picks another take
        jdbc.sql("UPDATE shot SET draft_clip_asset_id = :a WHERE id = :s").param("a", d2).param("s", s1).update();
        shots = assembly.assemblyRequest(video).shots();
        assertEquals("draft:s3://b/d2.mp4", shots.getFirst().kind() + ":" + shots.getFirst().uri(), "stale sharp clip not used");
        assertEquals(1, sharpen.estimate(video).clips(), "the new take needs sharpening");
    }

    @Test
    void theMonthlyGpuCapStopsClips() {
        shotWithKeyframe(0, 4.0);
        jdbc.sql("""
                        INSERT INTO cost_entry (id, video_id, provider_id, amount_inr, units, unit_type, estimated, note)
                        VALUES (:id, :v, 'modal_h100', 100000, 1, 'gpu_seconds', true, 'test: cap already used')
                        """).param("id", UUID.randomUUID()).param("v", video).update();
        assertThrows(BudgetGuard.BudgetExceededException.class, () -> clips.clipRequest(video, VideoStage.DRAFT, null));
        jdbc.sql("DELETE FROM cost_entry WHERE note = 'test: cap already used'").update();
    }

    @Test
    void aShotOnlyAcceptsItsOwnKeyframeVersions() {
        UUID scene = UUID.randomUUID();
        UUID shot = UUID.randomUUID();
        jdbc.sql("INSERT INTO scene (id, video_id, ord, title) VALUES (:s, :v, 0, 'S')").param("s", scene).param("v", video).update();
        jdbc.sql("INSERT INTO shot (id, scene_id, ord, prompt_json) VALUES (:id, :s, 0, CAST('{}' AS jsonb))")
                .param("id", shot).param("s", scene).update();
        UUID mine = UUID.randomUUID();
        UUID other = UUID.randomUUID();
        for (UUID a : List.of(mine, other)) {
            jdbc.sql("INSERT INTO asset (id, video_id, kind, uri_hot, shot_id) VALUES (:a, :v, 'image', 's3://b/x.png', :s)")
                    .param("a", a).param("v", video).param("s", a.equals(mine) ? shot : null).update();
        }
        shots.selectKeyframe(shot, mine);
        assertEquals(mine, jdbc.sql("SELECT keyframe_asset_id FROM shot WHERE id = :s").param("s", shot).query(UUID.class).single());
        assertThrows(IllegalStateException.class, () -> shots.selectKeyframe(shot, other));
        assertEquals(1, shots.shotVersions(shot).size());
    }

    @Test
    void theMonthlyGeminiCapStopsSpending() throws Exception {
        BigDecimal cap = new Pricing("../config").geminiMonthlyCapInr();
        BigDecimal before = budget.geminiSpentThisMonth();
        jdbc.sql("INSERT INTO cost_entry (id, video_id, provider_id, amount_inr) VALUES (:id, :v, 'gemini_api', :amt)")
                .param("id", UUID.randomUUID()).param("v", video).param("amt", cap.subtract(before).subtract(BigDecimal.valueOf(5)))
                .update();
        assertTrue(budget.canSpend(BigDecimal.valueOf(4)));
        assertFalse(budget.canSpend(BigDecimal.valueOf(6)));
        assertThrows(BudgetGuard.BudgetExceededException.class, () -> budget.require(BigDecimal.TEN, "test"));
        jdbc.sql("DELETE FROM cost_entry WHERE video_id = :v AND provider_id = 'gemini_api' AND amount_inr > 5").param("v", video).update();
    }

    // ---- AI Producer (P1)

    @Test
    void producerListensToEveryVoicedLineWithItsDeliveryAndTheOwnersFeedback() {
        StoredScript voiced = new StoredScript(2, List.of(
                new StoredLine(0, "A star is born.", "Act 1", "hook", true, null, "s3://b/l0.wav", 2.0, "READY", false,
                        false, null, List.of(), null, "hushed, awe-struck"),
                new StoredLine(1, "Silence.", "Act 1", "beat", false)), "Aoede", "warm", "strings", null, null);
        jdbc.sql("INSERT INTO script (id, video_id, version, beat_sheet_json) VALUES (:id, :v, 2, CAST(:b AS jsonb))")
                .param("id", UUID.randomUUID()).param("v", video).param("b", json.writeValueAsString(voiced)).update();
        jdbc.sql("INSERT INTO approval (id, video_id, stage, decision, notes, actor) VALUES (:id, :v, 'NARRATION', 'reject', 'voice not good', 'owner')")
                .param("id", UUID.randomUUID()).param("v", video).update();
        var req = producer.reviewRequest(video, "NARRATION");
        assertEquals(1, req.items().size(), "only spoken lines with audio");
        var item = req.items().getFirst();
        assertEquals("audio", item.kind());
        assertTrue(item.expect().contains("A star is born.") && item.expect().contains("hushed, awe-struck"), item.expect());
        assertTrue(req.rules().stream().anyMatch(r -> r.contains("voice not good")), "the owner's feedback goes into the review");
        assertNull(producer.reviewRequest(video, "MUSIC"), "stages it doesn't review: null");
    }

    @Test
    void producerAdviceIsLoggedCostedAndComparedWithTheOwnersDecision() {
        var result = new com.projectv.media.MediaTypes.ProducerReviewResult("redo", 0.8, "Line 1 sounds rushed.",
                "Line 1: slower, hushed.", List.of(new com.projectv.media.MediaTypes.ProducerItemVerdict("line-0", 4, false,
                "rushed", "slower, hushed")), "gemini-3.6-flash", new Usage(5000, 300, 0, 5300));
        var advice = producer.saveReview(video, "NARRATION", 1, result);
        assertTrue(advice.summary().contains("REDO of 1 item") && advice.summary().contains("80% sure"), advice.summary());
        assertEquals("Line 1: slower, hushed.", advice.redoNotes());
        assertTrue(jdbc.sql("SELECT count(*) FROM cost_entry WHERE video_id = :v AND note LIKE 'PRODUCER review%'")
                .param("v", video).query(Integer.class).single() >= 1, "the review is a cost entry (rule 6)");
        ProducerService.recordOwnerDecision(jdbc, video, "NARRATION", "reject", "yes, too fast");
        var d = producer.decisions(video).getFirst();
        assertEquals("reject", d.get("owner_decision"));
        assertEquals(Boolean.TRUE, d.get("agreed"), "it recommended a redo and the owner rejected: agreement");
        assertEquals(1L, ((Number) producer.agreement().get("agreed")).longValue());
    }

    @Test
    void producerSpendAdviceFollowsTheOwnersLimits() {
        assertTrue(producer.spendAdvice(video, new BigDecimal("30")).contains("would APPROVE"));
        assertTrue(producer.spendAdvice(video, new BigDecimal("60")).contains("would ASK YOU"), "above the Rs 50 line");
    }

    // ---- AI Producer (P2): acting within the owner's trust and limits

    @Test
    void producerTrustSwitchDecidesWhenItMayAct() {
        producer.setTrust("advise");
        assertFalse(producer.deciding());
        producer.setTrust("decide");
        assertTrue(producer.deciding());
        producer.setTrust("auto");
        assertFalse(producer.deciding(), "auto: not before it has agreed with the owner on enough decisions");
        assertThrows(IllegalArgumentException.class, () -> producer.setTrust("yolo"));
    }

    @Test
    void producerRedoLimitsAndSpendDecisionsFollowTheOwnersLimits() {
        producer.setTrust("decide");
        assertTrue(producer.mayRedo(video, 1));
        assertFalse(producer.mayRedo(video, 4), "at most 3 redo rounds per stage");
        assertTrue(producer.spendDecision(video, "DRAFT_GEN", new BigDecimal("30")).approve(), "inside the limits: it approves");
        assertFalse(producer.spendDecision(video, "UPSCALE", new BigDecimal("60")).approve(), "above Rs 50: it asks");
        var sum = producer.videoSummary(video);
        assertEquals(1L, ((Number) sum.get("spend_approved")).longValue());
        assertEquals(1L, ((Number) sum.get("asked")).longValue());
        producer.setTrust("auto");
    }

    @Test
    void producerRulesCanBeAddedEditedSwitchedOffAndDeleted() {
        producer.addRule("NARRATION", "no theatrical whispering");
        var rule = producer.rulesList().stream().filter(r -> "no theatrical whispering".equals(r.get("rule_text"))).findFirst().orElseThrow();
        UUID id = (UUID) rule.get("id");
        assertTrue(producer.rules("NARRATION").contains("no theatrical whispering"), "rules go into every review");
        producer.updateRule(id, null, false);
        assertFalse(producer.rules("NARRATION").contains("no theatrical whispering"), "switched off");
        producer.updateRule(id, "calm, warm documentary narration", true);
        assertTrue(producer.rules("NARRATION").contains("calm, warm documentary narration"));
        producer.deleteRule(id);
        assertTrue(producer.rulesList().stream().noneMatch(r -> id.equals(r.get("id"))));
    }

    @Test
    void producerVoiceRedoUsesItsNotesAsTheReadingStyle() {
        var base = new com.projectv.media.MediaTypes.NarrationRequest("v", "tts", "check", "Aoede", "in a warm voice", "en",
                List.of(new com.projectv.media.MediaTypes.NarrationLine(0, "Hi.", "hushed")));
        var redo = ProducerService.withStyle(base, "natural documentary voice, no whispering; say 'This bug' exactly");
        assertEquals("natural documentary voice, no whispering; say 'This bug' exactly", redo.style());
        assertEquals("Aoede", redo.voiceName(), "same voice, only the direction changes");
        assertEquals("hushed", redo.lines().getFirst().delivery());
        assertEquals(base, ProducerService.withStyle(base, " "));
    }
}
