package com.projectv.production;

import com.projectv.media.MediaTypes.ProducerItemVerdict;
import com.projectv.production.RedoStopRule.Action;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** The Producer stops re-rolling when the same issue comes back (optimisation review item 4). */
class RedoStopRuleTest {

    static List<ProducerItemVerdict> weak(String... problems) {
        return java.util.stream.IntStream.range(0, problems.length)
                .mapToObj(i -> new ProducerItemVerdict("shot-" + (i + 1), 4, false, problems[i], "fix")).toList();
    }

    @Test
    void theFirstRedoAndANewIssueAreLeftAlone() {
        assertEquals(Action.NONE, RedoStopRule.decide("redo", weak("visible human figure"), "", "n", List.of(), null, false, false).action());
        var other = RedoStopRule.decide("redo", weak("melted rail"), "", "n", weak("visible human figure"), "", true, false);
        assertEquals(Action.NONE, other.action());
        assertEquals("redo", other.verdict());
        assertEquals(Action.NONE, RedoStopRule.decide("approve", List.of(), "", null, weak("x"), "", true, false).action());
    }

    @Test
    void peopleTwiceInARowGetOneFirmPromptFixThenTheOwnerIsAsked() {
        // the autopilot Short (19 Nov): "two shots contain visible human figures", round after round
        var first = RedoStopRule.decide("redo", weak("a woman walks across the frame"), "", "remove the woman",
                weak("visible human figures"), "two shots contain visible human figures", true, false);
        assertEquals(Action.PROMPT_FIX, first.action());
        assertEquals("people", first.cause().key());
        assertEquals("redo", first.verdict());
        assertTrue(first.redoNotes().startsWith("RECURRING ISSUE (visible people)") && first.redoNotes().endsWith("remove the woman"));
        assertTrue(first.cause().negative().contains("silhouette"));

        var again = RedoStopRule.decide("redo", weak("silhouette of a man"), "", "remove him",
                weak("a woman walks across the frame"), "", true, true);
        assertEquals(Action.ASK, again.action());
        assertEquals("ask", again.verdict());
        assertTrue(again.note().contains("even after a prompt fix"), again.note());
    }

    @Test
    void anUnknownRepeatedIssueGoesStraightToTheOwner() {
        var r = RedoStopRule.decide("redo", weak("the bridge colour shifts mid clip"), "", "n",
                weak("bridge colour shifts in the clip"), "", true, false);
        assertEquals(Action.ASK, r.action());
        assertNull(r.cause());
        assertEquals("ask", r.verdict());
    }

    @Test
    void theSummaryNamesTheCauseWhenItemsHaveNoProblemText() {
        assertEquals(java.util.Set.of("people"), RedoStopRule.issues(List.of(), "Two shots contain visible human figures."));
        assertEquals(java.util.Set.of("text"), RedoStopRule.issues(weak("letters on the sign"), ""));
    }
}
