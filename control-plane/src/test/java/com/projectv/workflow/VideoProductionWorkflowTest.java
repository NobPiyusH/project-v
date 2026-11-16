package com.projectv.workflow;

import com.projectv.workflow.WorkflowTypes.StageResult;
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
        public void recordStage(String videoId, Stage stage, int attempt, StageMode mode, String status, String summary,
                                String artifactJson, String ownerNotes) {
            events.add(stage + ":" + attempt + ":" + status);
        }

        @Override
        public void setVideoStatus(String videoId, String status) {
            events.add("VIDEO:" + status);
        }
    }

    static class FakeMedia implements MediaActivities {
        @Override
        public String toolVersions() {
            return "ffmpeg: test";
        }
    }

    private TestWorkflowEnvironment env;
    private FakeActivities activities;
    private WorkflowClient client;

    @BeforeEach
    void setUp() {
        env = TestWorkflowEnvironment.newInstance();
        Worker control = env.newWorker(TaskQueues.CONTROL);
        control.registerWorkflowImplementationTypes(VideoProductionWorkflowImpl.class);
        activities = new FakeActivities();
        control.registerActivitiesImplementations(activities);
        env.newWorker(TaskQueues.MEDIA).registerActivitiesImplementations(new FakeMedia());
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
