package com.projectv.production;

import com.projectv.media.MediaTypes.Finding;
import com.projectv.planner.VideoPlan;
import com.projectv.production.ProductionService.StoredLine;
import com.projectv.production.ProductionService.StoredScript;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class InspectorTest {

    private static VideoPlan plan(String format, int seconds, List<String> mustInclude) {
        return new VideoPlan("t", "s", format, seconds, "9:16", "1080p", "shorts_punchy", "calm", "en", true, "full",
                "v", "support", true, List.of("ai_video"), 0, mustInclude, "");
    }

    private static StoredLine voiced(int i, String text, double sec) {
        return new StoredLine(i, text, "a", "p", true, null, "s3://x/" + i, sec, "READY", false, false, null, List.of(),
                "coverage 100%", null);
    }

    private static List<String> codes(List<Finding> f) {
        return f.stream().map(Finding::code).toList();
    }

    @Test
    void aScriptTooLongForTheShortFails() {
        String longLine = "word ".repeat(160).strip();
        var script = new StoredScript(1, List.of(new StoredLine(0, longLine, "a", "p", true)), "v", "s", "m", null, null);
        assertTrue(codes(Inspector.script(plan("short", 60, List.of()), script)).containsAll(
                List.of("SCRIPT_TOO_LONG", "SHORT_TOO_LONG")));
    }

    @Test
    void missingMustIncludeItemsAreWarnedNotFailed() {
        var script = new StoredScript(1, List.of(new StoredLine(0, "Olbers' paradoxes puzzled everyone.", "a", "p", true)),
                "v", "s", "m", null, null);
        List<Finding> f = Inspector.script(plan("short", 60, List.of("Olbers' paradox", "Expansion of the universe")), script);
        assertEquals(List.of("MISSING_MUST_INCLUDE"), codes(f));
        assertTrue(f.getFirst().message().contains("Expansion of the universe"));
    }

    @Test
    void deadAirAfterAShortenedLineIsWarned() {
        // The night-sky case: line 2 got 2.6s shorter after re-voicing, but the next line still starts where planned.
        var script = new StoredScript(1, List.of(voiced(0, "one", 5.0), voiced(1, "two", 7.8), voiced(2, "three", 6.0)),
                "v", "s", "m", Map.of("0", 0.4, "1", 6.0, "2", 17.0), 1.0);
        List<Finding> f = Inspector.gaps(script);
        assertEquals(List.of("DEAD_AIR"), codes(f));
        assertEquals("line 2", f.getFirst().ref());
        assertTrue(f.getFirst().message().startsWith("3.2s of silence"), f.getFirst().message());
    }

    @Test
    void aShortWhoseVoiceRunsPast60sFails() {
        var script = new StoredScript(1, List.of(voiced(0, "x", 30), voiced(1, "y", 29)), "v", "s", "m",
                Map.of("0", 0.4, "1", 30.9), 1.0);
        assertEquals(List.of("SHORT_TOO_LONG"), codes(Inspector.timing(plan("short", 60, List.of()), script)));
        assertEquals(59.9, Inspector.speechEnd(script), 1e-9);
    }

    @Test
    void reportVerdictsAndRedoNotes() {
        var fail = Inspector.report(List.of(new Finding("FAIL", "SPEECH_MISMATCH", "Instruction read aloud.", "line 3"),
                new Finding("WARN", "CLIPPING", "Peak", "line 1")), 7);
        assertEquals("FAIL", fail.verdict());
        assertTrue(fail.summary().startsWith("Inspector: FAIL, 1 problem (7 files checked): line 3: Instruction"));
        assertTrue(fail.notes().contains("- line 3: Instruction read aloud."));

        var warn = Inspector.report(List.of(new Finding("WARN", "CLIPPING", "Peak", "line 1")), 0);
        assertEquals("WARN", warn.verdict());
        assertNull(warn.notes());

        var pass = Inspector.report(List.of(), 3);
        assertEquals("PASS", pass.verdict());
        assertFalse(pass.summary().contains("FAIL"));
    }
}
