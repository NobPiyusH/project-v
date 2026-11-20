package com.projectv.planner;

import com.projectv.agents.Llm;
import org.junit.jupiter.api.Test;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.beans.factory.ObjectProvider;

import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class PlannerTest {

    private static final UUID VIDEO = UUID.randomUUID();
    private static final String STORY = "claude:claude-sonnet-5-5";

    private final Llm llm = mock(Llm.class);

    @SuppressWarnings("unchecked")
    private Planner planner(String apiKey) throws Exception {
        ObjectProvider<ChatModel> cm = mock(ObjectProvider.class);
        when(cm.getIfAvailable()).thenReturn(mock(ChatModel.class));
        return new Planner(cm, new StylePresets("src/test/resources/config"), llm, apiKey, "gemini-x", STORY);
    }

    private static VideoPlan raw() {
        return new VideoPlan("T", "s", "short", 30, null, null, "shorts_punchy", null, "en", false, "full", null, "support", true,
                List.of("ai_video"), 0, List.of(), "r");
    }

    @Test
    void planComesFromClaudeFirstAndSourceNamesTheModel() throws Exception {
        when(llm.callWithModel(eq(VIDEO), eq("PLAN"), any(), any(), eq(VideoPlan.class), eq(STORY)))
                .thenReturn(new Llm.Answer<>(raw(), STORY));
        Planner.Outcome o = planner("k").plan(VIDEO, "a short about owls", null);
        assertEquals(STORY, o.source());
        assertEquals("T", o.plan().title());
        assertNull(o.fallbackReason());
    }

    @Test
    void geminiAnswerIsLabelledGemini() throws Exception {
        when(llm.callWithModel(any(), any(), any(), any(), eq(VideoPlan.class), any()))
                .thenReturn(new Llm.Answer<>(raw(), "gemini-3.6-flash"));
        assertEquals("gemini:gemini-3.6-flash", planner("k").plan(VIDEO, "owls", null).source());
    }

    @Test
    void anLlmFailureFallsBackToRules() throws Exception {
        when(llm.callWithModel(any(), any(), any(), any(), eq(VideoPlan.class), any()))
                .thenThrow(new IllegalStateException("PLAN: no model answered"));
        Planner.Outcome o = planner("k").plan(VIDEO, "a short about owls", null);
        assertEquals("rules", o.source());
        assertTrue(o.fallbackReason().contains("IllegalStateException"));
    }

    @Test
    @SuppressWarnings("unchecked")
    void noGeminiKeyMeansRulesWithoutCallingTheLlm() throws Exception {
        assertEquals("rules", planner("").plan(VIDEO, "owls", null).source());
        verify(llm, never()).callWithModel(any(), any(), any(), any(), any(), any());
    }
}
