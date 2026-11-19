package com.projectv.dream;

import com.projectv.video.ClipProgress;
import io.temporal.api.common.v1.WorkflowExecution;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowNotFoundException;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.simple.JdbcClient;
import tools.jackson.databind.json.JsonMapper;

import java.util.Map;
import java.util.UUID;

import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.RETURNS_DEEP_STUBS;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** POST /cancel on a Short whose workflow already ended used to fail with HTTP 500 (Temporal NOT_FOUND). */
class DreamStudioCancelTest {

    final UUID id = UUID.randomUUID();
    final JdbcClient jdbc = mock(JdbcClient.class, RETURNS_DEEP_STUBS);
    final WorkflowClient temporal = mock(WorkflowClient.class);
    final DreamService dreams = mock(DreamService.class);
    final DreamEvidence evidence = mock(DreamEvidence.class);
    final DreamShortWorkflow wf = mock(DreamShortWorkflow.class);
    final DreamStudio studio = new DreamStudio(jdbc, temporal, JsonMapper.builder().build(), dreams, new ClipProgress(),
            mock(DreamProducer.class));

    DreamStudioCancelTest() {
        when(dreams.evidence()).thenReturn(evidence);
        when(temporal.newWorkflowStub(DreamShortWorkflow.class, "dream-" + id)).thenReturn(wf);
    }

    @Test
    void cancelOfAnEndedWorkflowIsNotAnError() {
        when(dreams.dream(id)).thenReturn(Map.of("step", "CLIPS", "status", "RUNNING"));
        doThrow(new WorkflowNotFoundException(WorkflowExecution.newBuilder().setWorkflowId("dream-" + id).build(),
                "DreamShortWorkflow", null)).when(wf).cancel();
        studio.cancel(id); // no exception
        verify(evidence).event(eq(id), eq("CLIPS"), eq("cancel"), isNull(), isNull(), eq(Map.of("alreadyEnded", true)));
    }

    @Test
    void cancelDuringClipsSaysItAppliesAfterTheCurrentClip() {
        when(dreams.dream(id)).thenReturn(Map.of("step", "CLIPS", "status", "RUNNING"));
        studio.cancel(id);
        verify(wf).cancel();
        verify(evidence).event(eq(id), eq("CLIPS"), eq("cancel"), isNull(), isNull(), eq(Map.of("afterClip", true)));
    }

    @Test
    void cancelWhileWaitingIsPlain() {
        when(dreams.dream(id)).thenReturn(Map.of("step", "WRITE", "status", "WAITING"));
        studio.cancel(id);
        verify(evidence).event(eq(id), eq("WRITE"), eq("cancel"), isNull(), isNull(), (Map<String, ?>) isNull());
    }
}
