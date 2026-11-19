package com.projectv.dream;

import com.projectv.dream.DreamTypes.DreamEstimate;
import com.projectv.dream.DreamTypes.DreamFinishRequest;
import com.projectv.dream.DreamTypes.DreamFinishResult;
import com.projectv.dream.DreamTypes.DreamRequest;
import com.projectv.dream.DreamTypes.DreamStep;
import com.projectv.media.MediaTypes.ClipJob;
import com.projectv.media.MediaTypes.ClipOutput;
import com.projectv.media.MediaTypes.ClipRequest;
import com.projectv.media.MediaTypes.ClipResult;
import com.projectv.media.MediaTypes.KeyframeJob;
import com.projectv.media.MediaTypes.KeyframeOutput;
import com.projectv.media.MediaTypes.KeyframeRequest;
import com.projectv.media.MediaTypes.KeyframeResult;
import com.projectv.workflow.MediaActivities;
import com.projectv.workflow.StageMode;
import com.projectv.workflow.TaskQueues;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import io.temporal.testing.TestWorkflowEnvironment;
import io.temporal.worker.Worker;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Proxy;
import java.math.BigDecimal;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Step order, Auto/Review/Manual gates, the clips' cost gate and failure → wait for Retry, with fake activities. */
class DreamShortWorkflowTest {

    static class FakeDream implements DreamActivities {
        final List<String> events = new CopyOnWriteArrayList<>();
        final AtomicInteger writes = new AtomicInteger();
        volatile boolean askForCost;
        volatile int failPicturesTimes;
        volatile List<String> lastClipShots;
        volatile String musicSource = "bed";
        volatile boolean scout = true;
        final List<String> pictureNotes = new CopyOnWriteArrayList<>();

        @Override
        public String writeShots(String dreamId, String notes) {
            events.add("write" + (notes == null ? "" : ":" + notes));
            return "write v" + writes.incrementAndGet();
        }

        @Override
        public KeyframeRequest pictureRequest(String dreamId, List<String> shotIds, String notes) {
            pictureNotes.add(shotIds + ":" + notes);
            if (failPicturesTimes > 0) {
                failPicturesTimes--;
                throw new IllegalStateException("GPU cap reached");
            }
            return new KeyframeRequest(dreamId, null, "app", "cls",
                    List.of(new KeyframeJob("s1", 1, "modal_zimage", "p", 768, 1344, "9:16", "1K", null, 1)));
        }

        @Override
        public String savePictures(String dreamId, KeyframeRequest request, KeyframeResult result) {
            events.add("pictures");
            return "pictures";
        }

        @Override
        public DreamEstimate clipEstimate(String dreamId, List<String> shotIds) {
            return new DreamEstimate(DreamStep.CLIPS, BigDecimal.TEN, 3, askForCost, "about ₹10");
        }

        @Override
        public ClipRequest clipRequest(String dreamId, List<String> shotIds) {
            lastClipShots = shotIds;
            return new ClipRequest(dreamId, "app", "cls", "u",
                    List.of(new ClipJob("s1", 1, 1, "dream", "fast", "s3://x", "p", 480, 832, 57, 1)), 1, 100,
                    BigDecimal.TEN, "wan", null, null);
        }

        @Override
        public String saveClips(String dreamId, ClipRequest request, ClipResult result) {
            events.add("clips" + (request == null ? "" : ""));
            return "clips";
        }

        @Override
        public com.projectv.media.MediaTypes.MusicRequest musicRequest(String dreamId, String notes) {
            return new com.projectv.media.MediaTypes.MusicRequest(dreamId, "lyria", "p", 16, 1);
        }

        @Override
        public String saveMusic(String dreamId, com.projectv.media.MediaTypes.MusicResult result) {
            events.add("music");
            events.add("music:lyria");
            return "music";
        }

        @Override
        public boolean scoutWanted(String dreamId) {
            return scout;
        }

        @Override
        public DreamTypes.DreamScoutRequest scoutRequest(String dreamId, String notes) {
            return new DreamTypes.DreamScoutRequest(dreamId, "idea" + (notes == null ? "" : " " + notes), "gemini", 6, "look");
        }

        @Override
        public String saveScout(String dreamId, DreamTypes.DreamScoutRequest request, DreamTypes.DreamScoutResult result) {
            events.add("scout:" + request.query() + ":" + result.videos().size());
            return "scouted";
        }

        @Override
        public String musicSource(String dreamId) {
            return musicSource;
        }

        @Override
        public DreamTypes.DreamBedRequest musicBedRequest(String dreamId) {
            return new DreamTypes.DreamBedRequest(dreamId, "s3://b/bed.m4a", 16, dreamId + "#0", List.of(), "gemini", "brief",
                    List.of(), 6);
        }

        @Override
        public String saveMusicBed(String dreamId, DreamTypes.DreamBedResult result) {
            events.add("music");
            events.add("music:bed:" + result.startSec());
            return "music";
        }

        @Override
        public DreamFinishRequest finishRequest(String dreamId) {
            return new DreamFinishRequest(dreamId, "dreamcore", List.of(), 1080, 1920, 24, 0.5, null, "u", null, null, 1.0, "none", true, true, true);
        }

        @Override
        public String saveFinish(String dreamId, DreamFinishResult result) {
            events.add("finish");
            return "finished";
        }

        @Override
        public void status(String dreamId, String step, String status, String message) {
            events.add("status:" + step + ":" + status);
        }

        @Override
        public void startClipBatch(String dreamId) {
        }
    }

    /** Media worker stand-in: only the two methods Dream Shorts call. */
    static MediaActivities fakeMedia() {
        return (MediaActivities) Proxy.newProxyInstance(MediaActivities.class.getClassLoader(), new Class<?>[]{MediaActivities.class},
                (proxy, method, args) -> switch (method.getName()) {
                    case "generateKeyframes" -> new KeyframeResult(List.of(new KeyframeOutput("s1", "s3://img", "modal_zimage",
                            "z", 1L, 1.0, null, null, List.of())), 1.0);
                    case "generateClips" -> new ClipResult(List.of(new ClipOutput("s1", 1, "dream", "s3://clip", 20, 57, 16,
                            "fast", null, 0)), 1, false);
                    case "generateMusic" -> new com.projectv.media.MediaTypes.MusicResult("s3://music", 16, 80, List.of(),
                            List.of(), 1, "lyria", List.of());
                    case "toString" -> "fakeMedia";
                    default -> throw new UnsupportedOperationException(method.getName());
                });
    }

    static class FakeFinish implements DreamMediaActivities {
        @Override
        public DreamFinishResult dreamFinish(DreamFinishRequest request) {
            return new DreamFinishResult("s3://final", 15, 1080, 1920, 24, "ffmpeg", null, null, java.util.Map.of());
        }

        @Override
        public DreamTypes.DreamScoutResult dreamScout(DreamTypes.DreamScoutRequest request) {
            return new DreamTypes.DreamScoutResult(List.of(new DreamTypes.ScoutVideo("u", "t", "c", 1000L, 20.0, "a kiosk",
                    null, null, 4, 4.0, null, null, null, null, null)), List.of(), "youtube", 1, null);
        }

        @Override
        public DreamTypes.DreamBedResult dreamMusicBed(DreamTypes.DreamBedRequest request) {
            return new DreamTypes.DreamBedResult("s3://bed-piece", 16.5, 754.2, 3600, "bed.m4a", null, null, null, 1);
        }
    }

    /** The AI Producer stand-in: verdicts in order (then approve), and whether it may act alone. */
    static class FakeProducer implements DreamProducerActivities {
        final java.util.Queue<String> verdicts = new java.util.concurrent.ConcurrentLinkedQueue<>();
        final List<String> acted = new CopyOnWriteArrayList<>();
        final List<String> reviewed = new CopyOnWriteArrayList<>();
        volatile boolean decides = true;
        volatile boolean spendApprove = true;
        volatile int maxRounds = 3;

        @Override
        public com.projectv.media.MediaTypes.ProducerReviewRequest reviewRequest(String dreamId, String step) {
            if (step.equals("SCOUT")) {
                return null;
            }
            return new com.projectv.media.MediaTypes.ProducerReviewRequest(dreamId, "DREAM_" + step, "m", "r", "c", List.of(),
                    List.of(new com.projectv.media.MediaTypes.ProducerItem("shot-1", "Shot 1", "image", "s3://x", null, "e")));
        }

        @Override
        public com.projectv.media.MediaTypes.ProducerAdvice saveReview(String dreamId, String step, int attempt,
                                                                         com.projectv.media.MediaTypes.ProducerReviewResult result) {
            reviewed.add(step);
            String v = step.equals("PICTURES") && !verdicts.isEmpty() ? verdicts.poll() : "approve";
            return new com.projectv.media.MediaTypes.ProducerAdvice(v, 0.9, "advice " + v, "sharper edges", "d-" + step + attempt,
                    v.equals("redo") ? List.of("shot-1") : List.of());
        }

        @Override
        public boolean decides(String dreamId) {
            return decides;
        }

        @Override
        public boolean mayRedo(String dreamId, int round) {
            return round <= maxRounds;
        }

        @Override
        public List<String> shotIds(String dreamId, List<String> itemIds) {
            return List.of("s1");
        }

        @Override
        public com.projectv.media.MediaTypes.SpendDecision spendDecision(String dreamId, BigDecimal estimateInr) {
            return new com.projectv.media.MediaTypes.SpendDecision(spendApprove, spendApprove ? "approved" : "Producer asks: over ₹50");
        }

        @Override
        public void markActed(String decisionId, String acted) {
            this.acted.add(decisionId + ":" + acted);
        }
    }

    static class FakeProducerMedia implements com.projectv.workflow.ProducerMediaActivities {
        @Override
        public com.projectv.media.MediaTypes.ProducerReviewResult review(com.projectv.media.MediaTypes.ProducerReviewRequest request) {
            return new com.projectv.media.MediaTypes.ProducerReviewResult("x", 0.9, "s", "n", List.of(), "m", null);
        }
    }

    TestWorkflowEnvironment env;
    FakeProducer producer;
    FakeDream dream;
    WorkflowClient client;

    @BeforeEach
    void setUp() {
        env = TestWorkflowEnvironment.newInstance();
        dream = new FakeDream();
        producer = new FakeProducer();
        Worker control = env.newWorker(TaskQueues.CONTROL);
        control.registerWorkflowImplementationTypes(DreamShortWorkflowImpl.class);
        control.registerActivitiesImplementations(dream, producer);
        Worker media = env.newWorker(TaskQueues.MEDIA);
        media.registerActivitiesImplementations(fakeMedia(), new FakeFinish(), new FakeProducerMedia());
        env.start();
        client = env.getWorkflowClient();
    }

    @AfterEach
    void tearDown() {
        env.close();
    }

    DreamShortWorkflow start(StageMode mode, Map<DreamStep, StageMode> modes) {
        DreamShortWorkflow wf = client.newWorkflowStub(DreamShortWorkflow.class,
                WorkflowOptions.newBuilder().setTaskQueue(TaskQueues.CONTROL).setWorkflowId("dream-test").build());
        WorkflowClient.start(wf::run, new DreamRequest("00000000-0000-0000-0000-000000000001", mode, modes, 10));
        return wf;
    }

    String result() {
        return client.newUntypedWorkflowStub("dream-test").getResult(String.class);
    }

    void waitFor(DreamShortWorkflow wf, String step, String status) {
        for (int i = 0; i < 200; i++) {
            var v = wf.view();
            if (step.equals(v.step()) && status.equals(v.status())) {
                return;
            }
            env.sleep(Duration.ofMillis(50));
        }
        throw new AssertionError("never reached " + step + " " + status + "; at " + wf.view());
    }

    @Test
    void decidingProducerRedoesWeakShotsWithItsNotesThenApproves() {
        producer.verdicts.add("redo");
        start(StageMode.PRODUCER, Map.of(DreamStep.SCOUT, StageMode.AUTO));
        assertEquals("completed", result());
        assertEquals(List.of("null:null", "[s1]:sharper edges"), dream.pictureNotes);
        assertTrue(producer.acted.contains("d-PICTURES1:redo"));
        assertTrue(producer.acted.contains("d-PICTURES2:approved"));
        assertEquals(List.of("WRITE", "PICTURES", "PICTURES", "CLIPS", "MUSIC", "FINISH"), producer.reviewed);
    }

    @Test
    void producerStopsRedoingAfterItsRoundsAndAsksTheOwner() {
        producer.maxRounds = 1;
        producer.verdicts.addAll(List.of("redo", "redo"));
        DreamShortWorkflow wf = start(StageMode.PRODUCER, Map.of(DreamStep.SCOUT, StageMode.AUTO));
        waitFor(wf, "PICTURES", "WAITING");
        assertTrue(wf.view().message().contains("advice redo"));
        assertTrue(producer.acted.contains("d-PICTURES2:asked"));
        wf.approve(DreamStep.PICTURES);
        assertEquals("completed", result());
    }

    @Test
    void advisingProducerLeavesTheDecisionToTheOwner() {
        producer.decides = false;
        DreamShortWorkflow wf = start(StageMode.AUTO, Map.of(DreamStep.WRITE, StageMode.PRODUCER));
        waitFor(wf, "WRITE", "WAITING");
        assertTrue(wf.view().message().contains("advice approve"));
        assertTrue(producer.acted.isEmpty());
        wf.approve(DreamStep.WRITE);
        assertEquals("completed", result());
    }

    @Test
    void decidingProducerApprovesClipCostInsideTheLimits() {
        dream.askForCost = true;
        start(StageMode.AUTO, Map.of(DreamStep.CLIPS, StageMode.PRODUCER));
        assertEquals("completed", result());
        assertTrue(dream.events.stream().noneMatch(e -> e.equals("status:CLIPS:WAITING_COST")));
    }

    @Test
    void clipCostAboveTheLimitsWaitsForTheOwnerWithTheProducersReason() {
        dream.askForCost = true;
        producer.spendApprove = false;
        DreamShortWorkflow wf = start(StageMode.AUTO, Map.of(DreamStep.CLIPS, StageMode.PRODUCER));
        waitFor(wf, "CLIPS", "WAITING_COST");
        assertTrue(wf.view().message().contains("over ₹50"));
        wf.approve(DreamStep.CLIPS);
        assertEquals("completed", result());
    }

    @Test
    void autoRunsAllFourStepsInOrder() {
        start(StageMode.AUTO, Map.of());
        assertEquals("completed", result());
        List<String> work = dream.events.stream().filter(e -> !e.startsWith("status") && !e.startsWith("music:")).toList();
        assertEquals(List.of("scout:idea:1", "write", "pictures", "clips", "music", "finish"), work);
        assertTrue(dream.events.contains("status:FINISH:COMPLETED"));
        assertTrue(dream.events.contains("music:bed:754.2"), "the owner's track is the default music");
    }

    @Test
    void lyriaIsUsedOnlyWhenChosenForTheShort() {
        dream.musicSource = "lyria";
        start(StageMode.AUTO, Map.of());
        assertEquals("completed", result());
        assertTrue(dream.events.contains("music:lyria"));
        assertTrue(dream.events.stream().noneMatch(e -> e.startsWith("music:bed")));
    }

    @Test
    void manualStepWaitsForTheOwnerAndRedoUsesTheNotes() {
        DreamShortWorkflow wf = start(StageMode.AUTO, Map.of(DreamStep.WRITE, StageMode.MANUAL));
        waitFor(wf, "WRITE", "WAITING");
        wf.redo(DreamStep.WRITE, null, "more mushrooms");
        waitFor(wf, "WRITE", "WAITING");
        assertTrue(dream.events.contains("write:more mushrooms"));
        wf.approve(DreamStep.WRITE);
        assertEquals("completed", result());
        assertEquals(2, dream.writes.get());
    }

    @Test
    void reviewGoesOnByItselfAfterTheWindow() {
        DreamShortWorkflow wf = start(StageMode.AUTO, Map.of(DreamStep.PICTURES, StageMode.REVIEW));
        waitFor(wf, "PICTURES", "WAITING_REVIEW");
        env.sleep(Duration.ofMinutes(11));
        assertEquals("completed", result());
    }

    @Test
    void clipsWaitForCostApprovalAndRedoTargetsOnlyChosenShots() {
        dream.askForCost = true;
        DreamShortWorkflow wf = start(StageMode.AUTO, Map.of(DreamStep.CLIPS, StageMode.MANUAL));
        waitFor(wf, "CLIPS", "WAITING_COST");
        assertTrue(dream.events.stream().noneMatch(e -> e.startsWith("clips")));
        wf.approve(DreamStep.CLIPS);
        waitFor(wf, "CLIPS", "WAITING");
        wf.redo(DreamStep.CLIPS, List.of("s1"), null);
        waitFor(wf, "CLIPS", "WAITING_COST");
        wf.approve(DreamStep.CLIPS);
        waitFor(wf, "CLIPS", "WAITING");
        assertEquals(List.of("s1"), dream.lastClipShots);
        wf.approve(DreamStep.CLIPS);
        assertEquals("completed", result());
    }

    @Test
    void aFailedStepWaitsForRetryInsteadOfEndingTheShort() {
        dream.failPicturesTimes = 3; // every automatic attempt fails
        DreamShortWorkflow wf = start(StageMode.AUTO, Map.of());
        waitFor(wf, "PICTURES", "FAILED_WAITING");
        assertTrue(wf.view().message().contains("GPU cap reached"));
        wf.retry();
        assertEquals("completed", result());
    }

    @Test
    void redoFromMusicKeepsTheEarlierSteps() {
        DreamShortWorkflow wf = client.newWorkflowStub(DreamShortWorkflow.class,
                WorkflowOptions.newBuilder().setTaskQueue(TaskQueues.CONTROL).setWorkflowId("dream-test").build());
        WorkflowClient.start(wf::run, new DreamRequest("00000000-0000-0000-0000-000000000001", StageMode.AUTO, Map.of(), 10,
                DreamStep.MUSIC));
        assertEquals("completed", result());
        assertEquals(List.of("music", "finish"), dream.events.stream().filter(e -> !e.startsWith("status") && !e.startsWith("music:")).toList());
    }

    @Test
    void cancelEndsTheShortWhileWaiting() {
        DreamShortWorkflow wf = start(StageMode.MANUAL, Map.of());
        waitFor(wf, "SCOUT", "WAITING"); // in Manual the Scout is the first step that waits
        wf.cancel();
        assertEquals("cancelled", result());
    }

    @Test
    void theScoutCanBeSwitchedOffForAShort() {
        dream.scout = false;
        start(StageMode.AUTO, Map.of());
        assertEquals("completed", result());
        assertTrue(dream.events.stream().noneMatch(e -> e.startsWith("scout")));
    }

    @Test
    void aScoutRedoSearchesAgainWithTheOwnersNotes() {
        DreamShortWorkflow wf = start(StageMode.AUTO, Map.of(DreamStep.SCOUT, StageMode.MANUAL));
        waitFor(wf, "SCOUT", "WAITING");
        wf.redo(DreamStep.SCOUT, null, "at night");
        waitFor(wf, "SCOUT", "WAITING");
        assertTrue(dream.events.contains("scout:idea at night:1"));
        wf.approve(DreamStep.SCOUT);
        assertEquals("completed", result());
    }
}
