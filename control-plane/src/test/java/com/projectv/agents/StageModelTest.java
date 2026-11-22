package com.projectv.agents;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

/** projectv.cost.stage-models: empty = nothing changes; a purpose can be given its own first-choice model. */
class StageModelTest {

    private static final String STORY = "claude:claude-sonnet-5-5";

    @Test
    void emptyKeepsTheModelEveryPurposeAlwaysUsed() {
        assertEquals(STORY, Llm.stageModel("", "CONCEPT", STORY));
        assertEquals(STORY, Llm.stageModel(null, "CONCEPT", STORY));
        assertNull(Llm.stageModel("", "DIAGRAM_SPEC", null));
    }

    @Test
    void aListedPurposeGetsItsOwnModelOthersAreUntouched() {
        String o = "PLAN=text; concept = claude:claude-haiku-5-5";
        assertNull(Llm.stageModel(o, "PLAN", STORY), "text = the Gemini list with no Claude first");
        assertEquals("claude:claude-haiku-5-5", Llm.stageModel(o, "CONCEPT", STORY), "case-insensitive, spaces ignored");
        assertEquals(STORY, Llm.stageModel(o, "SCRIPT", STORY));
        assertEquals(STORY, Llm.stageModel(o, "SHOT_PLAN", STORY));
    }

    @Test
    void anEmptyValueIsIgnored() {
        assertEquals(STORY, Llm.stageModel("SCRIPT=", "SCRIPT", STORY));
    }
}
