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

        @Override
        public String writeShots(String dreamId, String notes) {
            events.add("write" + (notes == null ? "" : ":" + notes));
            return "write v" + writes.incrementAndGet();
        }

        @Override
        public KeyframeRequest pictureRequest(String dreamId, List<String> shotIds, String notes) {
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
        public DreamFinishRequest finishRequest(String dreamId) {
            return new DreamFinishRequest(dreamId, "dreamcore", List.of(), 1080, 1920, 24, 0.5, null, "u");
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
                    case "toString" -> "fakeMedia";
                    default -> throw new UnsupportedOperationException(method.getName());
                });
    }

    static class FakeFinish implements DreamMediaActivities {
        @Override
        public DreamFinishResult dreamFinish(DreamFinishRequest request) {
            return new DreamFinishResult("s3://final", 15, 1080, 1920, 24, "ffmpeg", null, null);
        }
    }

    TestWorkflowEnvironment env;
    FakeDream dream;
    WorkflowClient client;

    @BeforeEach
    void setUp() {
        env = TestWorkflowEnvironment.newInstance();
        dream = new FakeDream();
        Worker control = env.newWorker(TaskQueues.CONTROL);
        control.registerWorkflowImplementationTypes(DreamShortWorkflowImpl.class);
        control.registerActivitiesImplementations(dream);
        Worker media = env.newWorker(TaskQueues.MEDIA);
        media.registerActivitiesImplementations(fakeMedia(), new FakeFinish());
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
    void autoRunsAllFourStepsInOrder() {
        start(StageMode.AUTO, Map.of());
        assertEquals("completed", result());
        List<String> work = dream.events.stream().filter(e -> !e.startsWith("status")).toList();
        assertEquals(List.of("write", "pictures", "clips", "finish"), work);
        assertTrue(dream.events.contains("status:FINISH:COMPLETED"));
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
    void cancelEndsTheShortWhileWaiting() {
        DreamShortWorkflow wf = start(StageMode.MANUAL, Map.of());
        waitFor(wf, "WRITE", "WAITING");
        wf.cancel();
        assertEquals("cancelled", result());
    }
}
