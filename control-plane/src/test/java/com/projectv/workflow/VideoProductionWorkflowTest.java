package com.projectv.workflow;

import com.projectv.media.MediaTypes.AnimaticRequest;
import com.projectv.media.MediaTypes.AnimaticResult;
import com.projectv.media.MediaTypes.KeyframeRequest;
import com.projectv.media.MediaTypes.KeyframeResult;
import com.projectv.media.MediaTypes.MusicRequest;
import com.projectv.media.MediaTypes.MusicResult;
import com.projectv.media.MediaTypes.NarrationRequest;
import com.projectv.media.MediaTypes.NarrationResult;
import com.projectv.media.MediaTypes.ResearchRequest;
import com.projectv.media.MediaTypes.ResearchResult;
import com.projectv.media.MediaTypes.Usage;
import com.projectv.workflow.WorkflowTypes.StageResult;
import io.temporal.failure.ApplicationFailure;
import com.projectv.workflow.WorkflowTypes.VideoRequest;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import io.temporal.client.WorkflowStub;
import io.temporal.testing.TestWorkflowEnvironment;
import io.temporal.worker.Worker;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Auto/Manual behaviour of the workflow, with in-memory fake activities (no DB, no LLM, no Python worker). */
class VideoProductionWorkflowTest {

    /** Records every recordStage call as "STAGE:attempt:STATUS". */
    static class FakeActivities implements VideoActivities {
        final List<String> events = new CopyOnWriteArrayList<>();
        final List<String> notesSeen = new CopyOnWriteArrayList<>();

        @Override
        public StageResult plan(String videoId, String prompt, String ownerNotes, int attempt) {
            if (ownerNotes != null) {
                notesSeen.add(ownerNotes);
            }
            // A plan that skips RESEARCH and MUSIC.
            List<Stage> stages = java.util.Arrays.stream(Stage.values())
                    .filter(s -> s != Stage.RESEARCH && s != Stage.MUSIC).toList();
            return new StageResult("plan v" + attempt, "{}", stages);
        }

        @Override
        public StageResult runStage(String videoId, Stage stage, String ownerNotes, int attempt) {
            return StageResult.of(stage + " done", "{}");
        }

        @Override
        public ResearchRequest researchRequest(String videoId, String ownerNotes) {
            return new ResearchRequest("t", "s", List.of(), "en", 5, "m", ownerNotes);
        }

        @Override
        public StageResult saveResearch(String videoId, ResearchResult result) {
            return StageResult.of("research saved", "{}");
        }

        @Override
        public StageResult concept(String videoId, String ownerNotes) {
            return StageResult.of("concept", "{}");
        }

        @Override
        public StageResult script(String videoId, String ownerNotes) {
            return StageResult.of("script", "{}");
        }

        @Override
        public MusicRequest musicRequest(String videoId, String ownerNotes) {
            return new MusicRequest(videoId, "m", "p", 60, 1);
        }

        @Override
        public StageResult saveMusic(String videoId, MusicResult result) {
            return StageResult.of("music", "{}");
        }

        @Override
        public NarrationRequest narrationRequest(String videoId) {
            return new NarrationRequest(videoId, "m", "c", "v", "s", "en", List.of());
        }

        @Override
        public StageResult saveNarration(String videoId, NarrationResult result) {
            return StageResult.of("voice", "{}");
        }

        @Override
        public int lastAttempt(String videoId, Stage stage) {
            return 2;
        }

        @Override
        public StageResult shotPlan(String videoId, String ownerNotes) {
            return StageResult.of("shots", "{}");
        }

        @Override
        public KeyframeRequest keyframeRequest(String videoId, String ownerNotes) {
            return new KeyframeRequest(videoId, "m", "a", "c", List.of());
        }

        @Override
        public StageResult saveKeyframes(String videoId, KeyframeRequest request, KeyframeResult result) {
            return StageResult.of("keyframes", "{}");
        }

        volatile boolean budgetRefusal;
        final java.util.concurrent.atomic.AtomicInteger shotRequests = new java.util.concurrent.atomic.AtomicInteger();

        @Override
        public KeyframeRequest shotKeyframeRequest(String videoId, List<String> shotIds, String ownerNotes, String policy) {
            shotRequests.incrementAndGet();
            if (budgetRefusal) {
                throw new com.projectv.ledger.BudgetGuard.BudgetExceededException("cap reached");
            }
            events.add("SHOT_REQUEST:" + shotIds + ":" + policy + ":" + ownerNotes);
            return new KeyframeRequest(videoId, "m", "a", "c", List.of());
        }

        @Override
        public void markShots(String videoId, List<String> shotIds, String status, String error) {
            events.add("SHOTS:" + shotIds + ":" + status);
        }

        @Override
        public NarrationRequest lineNarrationRequest(String videoId, List<Integer> lineIndexes, String styleNotes) {
            events.add("LINE_REQUEST:" + lineIndexes + ":" + styleNotes);
            return new NarrationRequest(videoId, "m", "c", "v", "in a voice that is " + styleNotes, "en", List.of());
        }

        @Override
        public StageResult saveLineNarration(String videoId, NarrationResult result, String style) {
            events.add("LINE_SAVED:" + style);
            return StageResult.of("lines saved", null);
        }

        @Override
        public void markLines(String videoId, List<Integer> lineIndexes, String status, String why) {
            events.add("LINES:" + lineIndexes + ":" + status);
        }

        @Override
        public AnimaticRequest animaticRequest(String videoId) {
            return new AnimaticRequest(videoId, "t", List.of(), List.of(), null, 1, "16:9");
        }

        @Override
        public StageResult saveAnimatic(String videoId, AnimaticResult result) {
            return StageResult.of("animatic", "{}");
        }

        @Override
        public void recordStage(String videoId, Stage stage, int attempt, StageMode mode, String status, String summary,
                                String artifactJson, String ownerNotes) {
            events.add(stage + ":" + attempt + ":" + status);
        }

        @Override
        public void setReviewDeadline(String videoId, Stage stage, int attempt, String deadlineIso) {
            events.add("DEADLINE:" + stage + ":" + (deadlineIso == null ? "cleared" : "set"));
        }

        @Override
        public void setVideoStatus(String videoId, String status) {
            events.add("VIDEO:" + status);
        }

        // ---- M2b clips
        volatile int clipsToMake = 2;
        volatile boolean clipsNeedApproval;
        volatile int brokenClipInspections;
        final List<String> clipRequests = new CopyOnWriteArrayList<>();

        @Override
        public com.projectv.media.MediaTypes.ClipEstimate clipEstimate(String videoId, String kind, List<String> shotIds) {
            int n = shotIds == null ? clipsToMake : shotIds.size();
            return new com.projectv.media.MediaTypes.ClipEstimate(kind, n, 100, java.math.BigDecimal.TEN, 0.1, 3,
                    clipsNeedApproval, "Ready to make " + n + " " + kind + " clips: about ₹10");
        }

        @Override
        public com.projectv.media.MediaTypes.ClipRequest clipRequest(String videoId, String kind, List<String> shotIds) {
            clipRequests.add(kind + ":" + shotIds);
            return new com.projectv.media.MediaTypes.ClipRequest(videoId, "app", "Wan", "http://x", List.of(), 1, 100,
                    java.math.BigDecimal.TEN);
        }

        @Override
        public StageResult saveClips(String videoId, com.projectv.media.MediaTypes.ClipRequest request,
                                     com.projectv.media.MediaTypes.ClipResult result) {
            return StageResult.of("clips saved", "{}");
        }

        @Override
        public void markClips(String videoId, List<String> shotIds, String status, String error) {
            events.add("CLIPS:" + shotIds + ":" + status);
        }

        // ---- M2c part 2a smooth motion
        @Override
        public com.projectv.media.MediaTypes.ClipEstimate smoothEstimate(String videoId) {
            return new com.projectv.media.MediaTypes.ClipEstimate("smooth", 0, 0, java.math.BigDecimal.ZERO, 0, 0, false,
                    "Every clip is already smooth.");
        }

        @Override
        public com.projectv.media.MediaTypes.SmoothRequest smoothRequest(String videoId) {
            return new com.projectv.media.MediaTypes.SmoothRequest(videoId, "u", 24, List.of(), java.math.BigDecimal.ZERO);
        }

        @Override
        public StageResult saveSmooth(String videoId, com.projectv.media.MediaTypes.SmoothRequest request,
                                      com.projectv.media.MediaTypes.SmoothResult result) {
            return StageResult.of("smoothed", "{}");
        }

        @Override
        public com.projectv.media.MediaTypes.ClipEstimate sharpEstimate(String videoId) {
            return new com.projectv.media.MediaTypes.ClipEstimate("sharp", 0, 0, java.math.BigDecimal.ZERO, 0, 0, false,
                    "Every clip is already sharp.");
        }

        @Override
        public com.projectv.media.MediaTypes.SmoothRequest sharpRequest(String videoId) {
            return new com.projectv.media.MediaTypes.SmoothRequest(videoId, "u", 24, List.of(), java.math.BigDecimal.ZERO);
        }

        @Override
        public StageResult saveSharp(String videoId, com.projectv.media.MediaTypes.SmoothRequest request,
                                     com.projectv.media.MediaTypes.SmoothResult result) {
            return StageResult.of("sharpened", "{}");
        }

        // ---- M2c assembly
        @Override
        public com.projectv.media.MediaTypes.AssemblyRequest assemblyRequest(String videoId) {
            return new com.projectv.media.MediaTypes.AssemblyRequest(videoId, "t", "9:16", "en", true, List.of(),
                    List.of(), null, 1);
        }

        @Override
        public StageResult saveMaster(String videoId, com.projectv.media.MediaTypes.AssemblyResult result) {
            events.add("MASTER:" + result.masterUri());
            return StageResult.of("master", "{}");
        }

        // ---- NASA footage
        volatile int footageQueries;

        @Override
        public com.projectv.media.MediaTypes.FootageSearchRequest footageSearchRequest(String videoId, List<String> shotIds,
                                                                                     String query) {
            List<com.projectv.media.MediaTypes.FootageQuery> q = new java.util.ArrayList<>();
            for (int i = 0; i < footageQueries; i++) {
                q.add(new com.projectv.media.MediaTypes.FootageQuery("shot-" + i, "solar flare"));
            }
            return new com.projectv.media.MediaTypes.FootageSearchRequest(q);
        }

        @Override
        public StageResult saveFootageCandidates(String videoId, com.projectv.media.MediaTypes.FootageSearchResponse result) {
            events.add("FOOTAGE:" + result.results().size());
            return StageResult.of("footage suggested", "{}");
        }

        @Override
        public com.projectv.media.MediaTypes.FootageFetchRequest footageFetchRequest(String videoId, String shotId, String nasaId,
                                                                                   Double startSec) {
            return new com.projectv.media.MediaTypes.FootageFetchRequest(videoId, shotId, 1, nasaId, startSec, 3, "9:16");
        }

        @Override
        public StageResult saveFootage(String videoId, com.projectv.media.MediaTypes.FootageFetchRequest request,
                                       com.projectv.media.MediaTypes.FootageFetchResult result) {
            return StageResult.of("footage used", "{}");
        }

        /** How many more image reviews report shot-3 as weak. */
        volatile int weakImageReviews;
        volatile boolean failCreative;

        @Override
        public List<com.projectv.media.MediaTypes.Finding> creativeShotPlanReview(String videoId, int attempt) {
            events.add("CD:SHOT_PLAN");
            if (failCreative) {
                throw ApplicationFailure.newNonRetryableFailure("no model answered", "Test");
            }
            return List.of(new com.projectv.media.MediaTypes.Finding("INFO", "CD_PROMPT_IMPROVED", "rewritten", "shot 2"));
        }

        @Override
        public com.projectv.media.MediaTypes.ImageReviewRequest imageReviewRequest(String videoId) {
            return new com.projectv.media.MediaTypes.ImageReviewRequest(videoId, "m", "r", "t", "9:16", "s", null, null,
                    null, null, List.of(), List.of());
        }

        @Override
        public List<com.projectv.media.MediaTypes.Finding> saveImageReview(String videoId, int attempt,
                                                                           com.projectv.media.MediaTypes.ImageReviewResult result) {
            events.add("CD:KEYFRAMES:" + attempt);
            if (weakImageReviews > 0) {
                weakImageReviews--;
                return List.of(new com.projectv.media.MediaTypes.Finding("FAIL", "CD_SHOT", "shows a flashlight",
                        "shot 3", "shot-3"));
            }
            return List.of();
        }

        @Override
        public KeyframeRequest fixKeyframeRequest(String videoId, List<String> shotIds, String ownerNotes) {
            events.add("FIX:" + shotIds + ":" + ownerNotes);
            return new KeyframeRequest(videoId, "m", "a", "c", List.of());
        }

        /** How many more times the Inspector reports FAIL for KEYFRAMES. */
        volatile int keyframeInspectionFails;
        final List<String> inspected = new CopyOnWriteArrayList<>();

        @Override
        public com.projectv.media.MediaTypes.MediaInspectRequest inspectionRequest(String videoId, Stage stage) {
            return stage == Stage.KEYFRAMES ? new com.projectv.media.MediaTypes.MediaInspectRequest(videoId,
                    stage.name(), "16:9", false, 60, List.of(), null, List.of(), null, null) : null;
        }

        @Override
        public WorkflowTypes.InspectionReport saveInspection(String videoId, Stage stage, int attempt,
                                                             com.projectv.media.MediaTypes.MediaInspectResult media) {
            inspected.add(stage + ":" + attempt);
            var fails = media == null ? List.<com.projectv.media.MediaTypes.Finding>of()
                    : media.findings().stream().filter(f -> "FAIL".equals(f.severity())).toList();
            if (!fails.isEmpty()) {
                return new WorkflowTypes.InspectionReport("FAIL", "Creative Director: weak image", "fix it",
                        fails.stream().map(com.projectv.media.MediaTypes.Finding::targetId).toList());
            }
            if (stage == Stage.DRAFT_GEN && brokenClipInspections > 0) {
                brokenClipInspections--;
                return new WorkflowTypes.InspectionReport("FAIL", "Inspector: frozen clip", "fix", List.of("shot-7"));
            }
            if (stage == Stage.KEYFRAMES && keyframeInspectionFails > 0) {
                keyframeInspectionFails--;
                return new WorkflowTypes.InspectionReport("FAIL", "Inspector: FAIL", "shot 3: blank image");
            }
            return new WorkflowTypes.InspectionReport("PASS", "Inspector: all checks passed.", null);
        }
    }

    static class FakeMedia implements MediaActivities {
        /** When true, keyframe generation throws, to test the failed-stage path. */
        volatile boolean failKeyframes;
        volatile boolean failVoice;

        volatile boolean failInspector;

        @Override
        public com.projectv.media.MediaTypes.SmoothResult smoothClips(com.projectv.media.MediaTypes.SmoothRequest request) {
            return new com.projectv.media.MediaTypes.SmoothResult(List.of(), 1, false);
        }

        @Override
        public com.projectv.media.MediaTypes.SmoothResult upscaleClips(com.projectv.media.MediaTypes.SmoothRequest request) {
            return new com.projectv.media.MediaTypes.SmoothResult(List.of(), 1, false);
        }

        @Override
        public com.projectv.media.MediaTypes.AssemblyResult assembleVideo(com.projectv.media.MediaTypes.AssemblyRequest request) {
            return new com.projectv.media.MediaTypes.AssemblyResult("s3://b/master.mp4", "s3://b/p.zip", null, 1, 1080, 1920,
                    30, List.of(), 0, 0);
        }

        @Override
        public com.projectv.media.MediaTypes.FootageSearchResponse searchFootage(
                com.projectv.media.MediaTypes.FootageSearchRequest request) {
            return new com.projectv.media.MediaTypes.FootageSearchResponse(request.queries().stream()
                    .map(q -> new com.projectv.media.MediaTypes.FootageSearchResult(q.shotId(), q.query(), List.of(), null))
                    .toList());
        }

        @Override
        public com.projectv.media.MediaTypes.FootageFetchResult fetchFootage(com.projectv.media.MediaTypes.FootageFetchRequest request) {
            return new com.projectv.media.MediaTypes.FootageFetchResult("s3://b/f.mp4", 1080, 1920, 3, 1920, 1080, 1.78, 2, "u");
        }

        @Override
        public com.projectv.media.MediaTypes.ClipResult generateClips(com.projectv.media.MediaTypes.ClipRequest request) {
            return new com.projectv.media.MediaTypes.ClipResult(List.of(), 1, false);
        }

        @Override
        public com.projectv.media.MediaTypes.ImageReviewResult reviewImages(
                com.projectv.media.MediaTypes.ImageReviewRequest request) {
            return new com.projectv.media.MediaTypes.ImageReviewResult(null, "m", Usage.NONE, 0);
        }

        @Override
        public com.projectv.media.MediaTypes.MediaInspectResult inspectMedia(
                com.projectv.media.MediaTypes.MediaInspectRequest request) {
            if (failInspector) {
                throw ApplicationFailure.newNonRetryableFailure("ffmpeg missing", "Test");
            }
            return com.projectv.media.MediaTypes.MediaInspectResult.NONE;
        }

        @Override
        public String toolVersions() {
            return "ffmpeg: test";
        }

        @Override
        public ResearchResult researchTopic(ResearchRequest request) {
            return new ResearchResult(List.of(), List.of(), List.of(), "m", Usage.NONE);
        }

        @Override
        public NarrationResult synthesizeNarration(NarrationRequest request) {
            if (failVoice) {
                throw ApplicationFailure.newNonRetryableFailure("TTS quota", "Test");
            }
            return new NarrationResult(List.of(), "m", "v");
        }

        @Override
        public MusicResult generateMusic(MusicRequest request) {
            return new MusicResult("s3://b/m.wav", 60, 90, List.of(), List.of(), 1, "m", List.of());
        }

        @Override
        public KeyframeResult generateKeyframes(KeyframeRequest request) {
            if (failKeyframes) {
                throw ApplicationFailure.newNonRetryableFailure("GPU unavailable", "Test");
            }
            return new KeyframeResult(List.of(), 0);
        }

        @Override
        public AnimaticResult renderAnimatic(AnimaticRequest request) {
            return new AnimaticResult("s3://b/a.mp4", 1);
        }
    }

    private TestWorkflowEnvironment env;
    private FakeActivities activities;
    private FakeMedia media;
    private WorkflowClient client;

    @BeforeEach
    void setUp() {
        env = TestWorkflowEnvironment.newInstance();
        Worker control = env.newWorker(TaskQueues.CONTROL);
        control.registerWorkflowImplementationTypes(VideoProductionWorkflowImpl.class, ShotRedoWorkflowImpl.class,
                VoiceRedoWorkflowImpl.class);
        activities = new FakeActivities();
        control.registerActivitiesImplementations(activities);
        media = new FakeMedia();
        env.newWorker(TaskQueues.MEDIA).registerActivitiesImplementations(media);
        env.start();
        client = env.getWorkflowClient();
    }

    @AfterEach
    void tearDown() {
        env.close();
    }

    private VideoProductionWorkflow start(String id, StageMode defaultMode, Map<Stage, StageMode> modes) {
        VideoProductionWorkflow wf = client.newWorkflowStub(VideoProductionWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId(id).setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new VideoRequest(id, "test prompt", defaultMode, modes));
        return wf;
    }

    private void awaitWaiting(VideoProductionWorkflow wf, Stage stage) {
        for (int i = 0; i < 200; i++) {
            var s = wf.status();
            if ("WAITING".equals(s.phase()) && s.currentStage() == stage) {
                return;
            }
            env.sleep(Duration.ofMillis(50));
        }
        throw new AssertionError("Stage " + stage + " never started waiting");
    }

    @Test
    void autoRunsEveryPlannedStageAndSkipsTheRest() {
        VideoProductionWorkflow wf = start("auto", StageMode.AUTO, Map.of());
        WorkflowStub.fromTyped(wf).getResult(Void.class);

        assertTrue(activities.events.contains("RESEARCH:0:SKIPPED"));
        assertTrue(activities.events.contains("MUSIC:0:SKIPPED"));
        assertTrue(activities.events.contains("PUBLISH:1:AUTO_PASSED"));
        assertTrue(activities.events.contains("MASTER:s3://b/master.mp4"), "assembly renders the master");
        assertEquals("VIDEO:COMPLETED", activities.events.getLast());
    }

    @Test
    void manualStageWaitsAndRejectRerunsWithNotes() {
        VideoProductionWorkflow wf = start("manual", StageMode.AUTO, Map.of(Stage.PLAN, StageMode.MANUAL));
        awaitWaiting(wf, Stage.PLAN);
        wf.reject(Stage.PLAN, "make it shorter");
        awaitWaiting(wf, Stage.PLAN);
        wf.approve(Stage.PLAN, "ok");
        WorkflowStub.fromTyped(wf).getResult(Void.class);

        assertEquals(List.of("make it shorter"), activities.notesSeen);
        assertTrue(activities.events.contains("PLAN:1:REJECTED"));
        assertTrue(activities.events.contains("PLAN:2:APPROVED"));
        assertEquals("VIDEO:COMPLETED", activities.events.getLast());
    }

    @Test
    void switchingAWaitingStageToAutoLetsItContinue() {
        VideoProductionWorkflow wf = start("switch", StageMode.MANUAL, Map.of());
        awaitWaiting(wf, Stage.PLAN);
        wf.setAllModes(StageMode.AUTO);
        WorkflowStub.fromTyped(wf).getResult(Void.class);

        assertTrue(activities.events.contains("PLAN:1:AUTO_PASSED"));
        assertEquals("VIDEO:COMPLETED", activities.events.getLast());
    }

    @Test
    void failedStageWaitsForOwnerThenRetriesOrSkips() {
        media.failKeyframes = true;
        VideoProductionWorkflow wf = start("fail", StageMode.AUTO, Map.of());
        awaitWaiting(wf, Stage.KEYFRAMES);                       // failed, even though the stage is AUTO
        assertTrue(activities.events.contains("KEYFRAMES:1:FAILED"));
        wf.reject(Stage.KEYFRAMES, "try again");                  // reject = retry
        awaitWaiting(wf, Stage.KEYFRAMES);
        assertTrue(activities.events.contains("KEYFRAMES:2:FAILED"));
        media.failKeyframes = false;
        wf.approve(Stage.KEYFRAMES, null);                        // approve = continue without it
        WorkflowStub.fromTyped(wf).getResult(Void.class);
        assertTrue(activities.events.contains("KEYFRAMES:2:SKIPPED"));
        assertEquals("VIDEO:COMPLETED", activities.events.getLast());
    }

    @Test
    void inspectorFailIsRedoneOnceAutomaticallyWithTheFindingsAsNotes() {
        activities.keyframeInspectionFails = 1;
        VideoProductionWorkflow wf = start("inspect-once", StageMode.AUTO, Map.of());
        WorkflowStub.fromTyped(wf).getResult(Void.class);
        assertTrue(activities.events.contains("KEYFRAMES:1:INSPECTION_FAILED"));
        assertTrue(activities.events.contains("KEYFRAMES:2:AUTO_PASSED"));
        assertTrue(activities.inspected.contains("SHOT_PLAN:1"), "every inspected stage is checked");
        assertEquals("VIDEO:COMPLETED", activities.events.getLast());
    }

    @Test
    void inspectorFailingTwiceWaitsForTheOwnerEvenInAuto() {
        activities.keyframeInspectionFails = 2;
        VideoProductionWorkflow wf = start("inspect-twice", StageMode.AUTO, Map.of());
        awaitWaiting(wf, Stage.KEYFRAMES);
        assertTrue(activities.events.contains("KEYFRAMES:2:WAITING_APPROVAL"));
        wf.approve(Stage.KEYFRAMES, null);                       // accept it anyway
        WorkflowStub.fromTyped(wf).getResult(Void.class);
        assertTrue(activities.events.contains("KEYFRAMES:2:APPROVED"));
        assertEquals("VIDEO:COMPLETED", activities.events.getLast());
    }

    @Test
    void aWeakImageIsRedoneOnItsOwnWithTheBetterPromptThenThePlanContinues() {
        activities.weakImageReviews = 1;
        VideoProductionWorkflow wf = start("cd-image", StageMode.AUTO, Map.of());
        WorkflowStub.fromTyped(wf).getResult(Void.class);
        assertTrue(activities.events.contains("CD:SHOT_PLAN"), "the shot plan gets a story review");
        assertTrue(activities.events.contains("KEYFRAMES:1:INSPECTION_FAILED"));
        assertTrue(activities.events.contains("FIX:[shot-3]:null"), "only the weak shot is regenerated");
        assertTrue(activities.events.contains("CD:KEYFRAMES:2"), "the images are reviewed again");
        assertTrue(activities.events.contains("KEYFRAMES:2:AUTO_PASSED"));
        assertEquals("VIDEO:COMPLETED", activities.events.getLast());
    }

    @Test
    void aStillWeakImageWaitsForTheOwnerAndRejectRedoesThatShotWithTheirNotes() {
        activities.weakImageReviews = 2;
        VideoProductionWorkflow wf = start("cd-image-owner", StageMode.AUTO, Map.of());
        awaitWaiting(wf, Stage.KEYFRAMES);
        wf.reject(Stage.KEYFRAMES, "show the Moon's far side in shadow");
        WorkflowStub.fromTyped(wf).getResult(Void.class);
        assertTrue(activities.events.contains("FIX:[shot-3]:show the Moon's far side in shadow"));
        assertTrue(activities.events.contains("KEYFRAMES:3:AUTO_PASSED"));
    }

    @Test
    void anUnavailableCreativeDirectorOnlyWarns() {
        activities.failCreative = true;
        VideoProductionWorkflow wf = start("cd-down", StageMode.AUTO, Map.of());
        WorkflowStub.fromTyped(wf).getResult(Void.class);
        assertTrue(activities.events.contains("SHOT_PLAN:1:AUTO_PASSED"));
        assertEquals("VIDEO:COMPLETED", activities.events.getLast());
    }

    @Test
    void clipsAboveTheAutoApproveAmountWaitForTheOwnersGoAheadEvenInAuto() {
        activities.clipsNeedApproval = true;
        VideoProductionWorkflow wf = start("clips-gate", StageMode.AUTO, Map.of());
        awaitWaiting(wf, Stage.DRAFT_GEN);
        assertTrue(activities.clipRequests.isEmpty(), "nothing is sent to the GPU before the owner says yes");
        wf.approve(Stage.DRAFT_GEN, null);
        awaitWaiting(wf, Stage.FINAL_GEN);
        wf.reject(Stage.FINAL_GEN, "too expensive");               // declining the spend skips the stage
        WorkflowStub.fromTyped(wf).getResult(Void.class);
        assertEquals(List.of("draft:null"), activities.clipRequests);
        assertTrue(activities.events.contains("DRAFT_GEN:1:AUTO_PASSED"));
        assertTrue(activities.events.contains("FINAL_GEN:1:SKIPPED"));
        assertEquals("VIDEO:COMPLETED", activities.events.getLast());
    }

    @Test
    void aBrokenClipIsRedoneOnItsOwn() {
        activities.brokenClipInspections = 1;
        VideoProductionWorkflow wf = start("clips-fix", StageMode.AUTO, Map.of());
        WorkflowStub.fromTyped(wf).getResult(Void.class);
        assertTrue(activities.events.contains("DRAFT_GEN:1:INSPECTION_FAILED"));
        assertTrue(activities.clipRequests.contains("draft:[shot-7]"), "only the broken shot is rendered again");
        assertTrue(activities.events.contains("DRAFT_GEN:2:AUTO_PASSED"));
    }

    @Test
    void libraryMatchSuggestsRealFootageForFree() {
        activities.footageQueries = 2;
        VideoProductionWorkflow wf = start("footage", StageMode.AUTO, Map.of());
        WorkflowStub.fromTyped(wf).getResult(Void.class);
        assertTrue(activities.events.contains("FOOTAGE:2"));
        assertTrue(activities.events.contains("LIBRARY_MATCH:1:AUTO_PASSED"));
    }

    @Test
    void nothingToRenderSkipsTheClipStage() {
        activities.clipsToMake = 0;
        VideoProductionWorkflow wf = start("clips-none", StageMode.AUTO, Map.of());
        WorkflowStub.fromTyped(wf).getResult(Void.class);
        assertTrue(activities.events.contains("DRAFT_GEN:1:SKIPPED"));
        assertTrue(activities.clipRequests.isEmpty());
    }

    @Test
    void aBrokenInspectorNeverBlocksTheVideo() {
        media.failInspector = true;
        VideoProductionWorkflow wf = start("inspect-broken", StageMode.AUTO, Map.of());
        WorkflowStub.fromTyped(wf).getResult(Void.class);
        assertTrue(activities.events.contains("KEYFRAMES:1:AUTO_PASSED"));
        assertEquals("VIDEO:COMPLETED", activities.events.getLast());
    }

    @Test
    void rerunRunsOnlyTheRequestedStagesWithContinuingAttemptNumbers() {
        VideoProductionWorkflow wf = client.newWorkflowStub(VideoProductionWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId("rerun").setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new VideoRequest("rerun", "p", StageMode.AUTO, Map.of(),
                List.of(Stage.ANIMATIC, Stage.NARRATION)));
        WorkflowStub.fromTyped(wf).getResult(Void.class);
        List<String> stageEvents = activities.events.stream().filter(e -> !e.startsWith("VIDEO")).toList();
        assertEquals(List.of("NARRATION:3:RUNNING", "NARRATION:3:AUTO_PASSED", "ANIMATIC:3:RUNNING", "ANIMATIC:3:AUTO_PASSED"),
                stageEvents);
    }

    @Test
    void reviewModeContinuesByItselfWhenTheWindowEnds() {
        VideoProductionWorkflow wf = client.newWorkflowStub(VideoProductionWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId("review").setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new VideoRequest("review", "p", StageMode.AUTO,
                Map.of(Stage.SCRIPT, StageMode.REVIEW), null, 10));
        WorkflowStub.fromTyped(wf).getResult(Void.class);   // test server skips the 10 minutes
        assertTrue(activities.events.contains("DEADLINE:SCRIPT:set"));
        assertTrue(activities.events.contains("SCRIPT:1:AUTO_APPROVED"));
        assertEquals("VIDEO:COMPLETED", activities.events.getLast());
    }

    @Test
    void reviewModeCanBeApprovedEarlyOrHeld() {
        VideoProductionWorkflow wf = client.newWorkflowStub(VideoProductionWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId("review2").setTaskQueue(TaskQueues.CONTROL).build());
        WorkflowClient.start(wf::run, new VideoRequest("review2", "p", StageMode.AUTO,
                Map.of(Stage.CONCEPT, StageMode.REVIEW, Stage.SCRIPT, StageMode.REVIEW), null, 10));
        awaitWaiting(wf, Stage.CONCEPT);
        wf.approve(Stage.CONCEPT, null);                       // approve early
        awaitWaiting(wf, Stage.SCRIPT);
        wf.setMode(Stage.SCRIPT, StageMode.MANUAL);            // hold: the timer must not auto-approve any more
        env.sleep(Duration.ofMinutes(30));
        assertEquals(Stage.SCRIPT, wf.status().currentStage());
        assertTrue(activities.events.contains("DEADLINE:SCRIPT:cleared"));
        wf.approve(Stage.SCRIPT, "ok");
        WorkflowStub.fromTyped(wf).getResult(Void.class);
        assertTrue(activities.events.contains("CONCEPT:1:APPROVED"));
        assertTrue(activities.events.contains("SCRIPT:1:APPROVED"));
    }

    private StageResult redo(String id, List<String> shots, String notes, String policy) {
        ShotRedoWorkflow wf = client.newWorkflowStub(ShotRedoWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId(id).setTaskQueue(TaskQueues.CONTROL).build());
        return wf.run(new ShotRedoWorkflow.ShotRedoRequest("v", shots, notes, policy));
    }

    @Test
    void shotRedoRegeneratesOnlyTheChosenShots() {
        StageResult r = redo("redo-ok", List.of("s14"), "less cartoonish", "PREMIUM");
        assertEquals("keyframes", r.summary());
        assertEquals(List.of("SHOTS:[s14]:REDOING", "SHOT_REQUEST:[s14]:PREMIUM:less cartoonish"), activities.events);
    }

    @Test
    void shotRedoFailureMarksTheShotsFailed() {
        media.failKeyframes = true;
        StageResult r = redo("redo-fail", List.of("s1", "s2"), null, "FREE");
        assertTrue(r.summary().startsWith("Shot redo failed"));
        assertEquals("SHOTS:[s1, s2]:FAILED", activities.events.getLast());
    }

    @Test
    void shotRedoBudgetRefusalIsNotRetried() {
        activities.budgetRefusal = true;
        StageResult r = redo("redo-budget", List.of("s1"), null, "PREMIUM");
        assertTrue(r.summary().contains("cap reached"));
        assertEquals(1, activities.shotRequests.get());
        assertEquals("SHOTS:[s1]:FAILED", activities.events.getLast());
    }

    private StageResult redoVoice(String id, List<Integer> lines, String style) {
        VoiceRedoWorkflow wf = client.newWorkflowStub(VoiceRedoWorkflow.class,
                WorkflowOptions.newBuilder().setWorkflowId(id).setTaskQueue(TaskQueues.CONTROL).build());
        return wf.run(new VoiceRedoWorkflow.VoiceRedoRequest("v", lines, style));
    }

    @Test
    void voiceRedoRevoicesOnlyTheChosenLinesWithTheNewStyle() {
        StageResult r = redoVoice("voice-ok", List.of(7, 9), "slower and more hushed");
        assertEquals("lines saved", r.summary());
        assertEquals(List.of("LINES:[7, 9]:REDOING", "LINE_REQUEST:[7, 9]:slower and more hushed",
                "LINE_SAVED:in a voice that is slower and more hushed"), activities.events);
    }

    @Test
    void voiceRedoFailureMarksTheLinesFailed() {
        media.failVoice = true;
        StageResult r = redoVoice("voice-fail", List.of(3), null);
        assertTrue(r.summary().startsWith("Voice redo failed"));
        assertEquals("LINES:[3]:FAILED", activities.events.getLast());
    }

    @Test
    void approvalForAStageThatIsNotWaitingIsIgnored() {
        VideoProductionWorkflow wf = start("ignore", StageMode.AUTO, Map.of(Stage.SCRIPT, StageMode.MANUAL));
        awaitWaiting(wf, Stage.SCRIPT);
        wf.approve(Stage.CONCEPT, "wrong stage");   // ignored
        env.sleep(Duration.ofMillis(200));
        assertEquals(Stage.SCRIPT, wf.status().currentStage());
        wf.approve(Stage.SCRIPT, null);
        WorkflowStub.fromTyped(wf).getResult(Void.class);
        assertTrue(activities.events.contains("SCRIPT:1:APPROVED"));
    }
}
