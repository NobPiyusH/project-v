package com.projectv.dream;

import com.projectv.dream.DreamTimeline.Event;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DreamTimelineTest {

    static final Instant T0 = Instant.parse("2026-11-19T10:00:00Z");

    static Event status(String step, String status, int sec) {
        return new Event(step, "status", null, Map.of("status", status), T0.plusSeconds(sec));
    }

    @Test
    void workCountsOnlyRunningAndStepsCloseWhenTheNextStarts() {
        var events = List.of(
                status("WRITE", "RUNNING", 0),
                status("WRITE", "WAITING_REVIEW", 60),   // 60 s work, then 600 s waiting
                status("PICTURES", "RUNNING", 660),
                status("PICTURES", "RUNNING", 700),
                status("CLIPS", "WAITING_COST", 760),    // 100 s pictures
                status("CLIPS", "RUNNING", 900));        // still running at 1000
        var steps = DreamTimeline.steps(events, List.of(new DreamTimeline.Cost(12.5, T0.plusSeconds(950)),
                new DreamTimeline.Cost(1, T0.plusSeconds(680))), T0.plusSeconds(1000), Map.of("CLIPS", 300L));
        assertEquals("PENDING", steps.get(0).status());          // SCOUT never ran
        var write = steps.get(1);
        assertEquals("DONE", write.status());
        assertEquals(60, write.workSeconds());
        assertEquals(T0, write.startedAt());
        assertEquals(T0.plusSeconds(660), write.finishedAt());
        assertEquals(100, steps.get(2).workSeconds());
        assertEquals(1.0, steps.get(2).costInr());
        var clips = steps.get(3);
        assertEquals("RUNNING", clips.status());
        assertNull(clips.finishedAt());
        assertEquals(100, clips.workSeconds());
        assertEquals(12.5, clips.costInr());
        assertEquals(300L, clips.typicalSeconds());
        assertNull(steps.get(4).typicalSeconds());
    }

    @Test
    void aRerunReplacesTheStepsTimesAndCompletionClosesFinish() {
        var events = List.of(
                status("MUSIC", "RUNNING", 0), status("FINISH", "RUNNING", 10), status("FINISH", "COMPLETED", 40),
                status("FINISH", "RUNNING", 100), status("FINISH", "COMPLETED", 120));
        var work = DreamTimeline.finishedWork(events);
        assertEquals(10L, work.get("MUSIC"));
        assertEquals(20L, work.get("FINISH"));
    }

    @Test
    void typicalIsTheMedianAndNeedsTwoShorts() {
        var typical = DreamTimeline.typical(List.of(
                Map.of("CLIPS", 100L, "WRITE", 30L), Map.of("CLIPS", 300L), Map.of("CLIPS", 200L)));
        assertEquals(200L, typical.get("CLIPS"));
        assertTrue(!typical.containsKey("WRITE"));          // one sample: no estimate yet
        assertEquals(150L, DreamTimeline.typical(List.of(Map.of("MUSIC", 100L), Map.of("MUSIC", 200L))).get("MUSIC"));
    }

    @Test
    void logIsNewestFirstInPlainWords() {
        var events = List.of(
                status("CLIPS", "RUNNING", 0),
                new Event("CLIPS", "cancel", null, Map.of("afterClip", true), T0.plusSeconds(5)),
                status("CLIPS", "CANCELLED", 60));
        var log = DreamTimeline.log(events);
        assertEquals("Short cancelled (at Clips)", log.get(0).text());
        assertEquals("Cancel requested: it will apply after the current clip finishes", log.get(1).text());
        assertEquals("Clips started", log.get(2).text());
    }
}
