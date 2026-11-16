package com.projectv.agents;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class OpenAiCompatChatTest {

    @Test
    void reasoningAndFencesAreStrippedAroundTheJson() {
        assertEquals("{\"a\":1}", OpenAiCompatChat.clean("<think>plan {x}</think>\n```json\n{\"a\":1}\n```"));
    }

    @Test
    void onlyPrefixedModelsAreRoutedToNvidia() {
        assertTrue(OpenAiCompatChat.handles("nvidia:nvidia/nemotron-3-ultra-550b-a55b"));
        assertTrue(OpenAiCompatChat.handles("groq:openai/gpt-oss-120b"));
        assertFalse(OpenAiCompatChat.handles("gemini-3.5-flash"));
    }

    @Test
    void missingKeyIsAModelProblemSoTheListMovesOn() {
        var chat = new OpenAiCompatChat("", "http://127.0.0.1:1", "", "http://127.0.0.1:1");
        try {
            chat.call("nvidia:x", "s", "u", String.class);
        } catch (IllegalStateException e) {
            assertTrue(ModelFallback.isModelProblem(e));
            return;
        }
        throw new AssertionError("expected an error");
    }
}
