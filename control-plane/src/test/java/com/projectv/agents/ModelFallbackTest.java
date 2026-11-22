package com.projectv.agents;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeoutException;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class ModelFallbackTest {

    @Test
    void aCutOffAnswerDoesNotHopToTheNextModel() {
        List<String> tried = new CopyOnWriteArrayList<>();
        assertThrows(ExecutionException.class, () -> ModelFallback.run(ModelFallback.models("claude:claude-sonnet-5-5", "gemini-3.6-flash"), 5, "SHOT_PLAN", m -> {
            tried.add(m);
            throw new IllegalStateException("unavailable: claude:claude-sonnet-5-5 answer was cut off (token limit); tokens 3489/16000");
        }));
        assertEquals(List.of("claude:claude-sonnet-5-5"), tried, "no weaker model is paid to redo it");
    }

    @Test
    void retiredModelFallsBackToTheNext() throws Exception {
        List<String> tried = new CopyOnWriteArrayList<>();
        var a = ModelFallback.run(ModelFallback.models("gemini-2.5-flash", "gemini-3.6-flash"), 5, "test", m -> {
            tried.add(m);
            if (m.equals("gemini-2.5-flash")) {
                throw new IllegalStateException("404 models/gemini-2.5-flash is no longer available to new users");
            }
            return "ok";
        });
        assertEquals("gemini-3.6-flash", a.model());
        assertEquals(List.of("gemini-2.5-flash", "gemini-3.6-flash"), tried);
    }

    @Test
    void aHangingModelFallsBackAfterTheTimeLimit() throws Exception {
        var a = ModelFallback.run(List.of("hangs", "fast"), 1, "test", m -> {
            if (m.equals("hangs")) {
                try {
                    Thread.sleep(5_000);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            }
            return m;
        });
        assertEquals("fast", a.model());
    }

    @Test
    void billingErrorsDoNotHopModels() {
        List<String> tried = new CopyOnWriteArrayList<>();
        assertThrows(ExecutionException.class, () -> ModelFallback.run(List.of("a", "b"), 5, "test", m -> {
            tried.add(m);
            throw new IllegalStateException("403 PERMISSION_DENIED: billing account disabled");
        }));
        assertEquals(List.of("a"), tried);
    }

    @Test
    void lastModelTimeoutIsReported() {
        assertThrows(TimeoutException.class, () -> ModelFallback.run(List.of("only"), 1, "test", m -> {
            try {
                Thread.sleep(3_000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            return m;
        }));
    }

    @Test
    void listsAreParsedAndDeduplicated() {
        assertEquals(List.of("a", "b", "c"), ModelFallback.models("a, b", "b,c,,"));
    }
}
