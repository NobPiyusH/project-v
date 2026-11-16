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
            return new AnimaticRequest(videoId, "t", List.of(), List.of(), null, 1);
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
    }

    static class FakeMedia implements MediaActivities {
        /** When true, keyframe generation throws, to test the failed-stage path. */
        volatile boolean failKeyframes;
        volatile boolean failVoice;

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
