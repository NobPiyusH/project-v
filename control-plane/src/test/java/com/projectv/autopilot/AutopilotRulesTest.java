package com.projectv.autopilot;

import com.projectv.autopilot.AutopilotService.Action;
import com.projectv.autopilot.AutopilotTypes.PostKit;
import org.junit.jupiter.api.Test;

import java.sql.Timestamp;
import java.time.Instant;
import java.time.LocalTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** The autopilot's daily rules, without a database or clock. */
class AutopilotRulesTest {

    static final LocalTime SEVEN = LocalTime.of(7, 0);
    static final Instant NOW = Instant.parse("2026-11-20T02:00:00Z");

    @Test
    void offByDefaultDoesNothing() {
        assertEquals(Action.NOTHING, AutopilotService.decide(false, LocalTime.of(9, 0), SEVEN, null, false, NOW));
    }

    @Test
    void startsOnceTheTimeHasCome() {
        assertEquals(Action.NOTHING, AutopilotService.decide(true, LocalTime.of(6, 59), SEVEN, null, false, NOW));
        assertEquals(Action.CREATE, AutopilotService.decide(true, LocalTime.of(7, 0), SEVEN, null, false, NOW));
        // the laptop was off at 7: it catches up when the backend is next running that day
        assertEquals(Action.CREATE, AutopilotService.decide(true, LocalTime.of(15, 30), SEVEN, null, false, NOW));
    }

    @Test
    void oneRunPerDay() {
        assertEquals(Action.NOTHING, AutopilotService.decide(true, LocalTime.of(9, 0), SEVEN,
                Map.of("status", "RUNNING"), false, NOW));
    }

    @Test
    void aFailedOrWaitingEarlierRunHoldsTheNextDay() {
        assertEquals(Action.BLOCKED, AutopilotService.decide(true, LocalTime.of(9, 0), SEVEN, null, true, NOW));
        assertTrue(AutopilotService.BLOCKING.containsAll(List.of("FAILED", "NEEDS_YOU", "RUNNING", "WAITING_START")));
        assertFalse(AutopilotService.BLOCKING.contains("READY"), "a Short waiting for Post does not stop the next one");
    }

    @Test
    void reviewModeStartsWhenItsWindowEnds() {
        Map<String, Object> waiting = Map.of("status", "WAITING_START", "starts_at", Timestamp.from(NOW.plusSeconds(60)));
        assertEquals(Action.NOTHING, AutopilotService.decide(true, LocalTime.of(9, 0), SEVEN, waiting, false, NOW));
        assertEquals(Action.START_DUE, AutopilotService.decide(true, LocalTime.of(9, 1), SEVEN, waiting, false,
                NOW.plusSeconds(60)));
    }

    @Test
    void manualModeNeverStartsByItself() {
        Map<String, Object> manual = new java.util.HashMap<>(Map.of("status", "WAITING_START"));
        manual.put("starts_at", null);
        assertEquals(Action.NOTHING, AutopilotService.decide(true, LocalTime.of(23, 0), SEVEN, manual, false, NOW));
    }

    @Test
    void runFollowsItsShort() {
        assertEquals("READY", AutopilotService.runStatus("COMPLETED"));
        assertEquals("NEEDS_YOU", AutopilotService.runStatus("FAILED_WAITING"));
        assertEquals("NEEDS_YOU", AutopilotService.runStatus("WAITING_COST"));
        assertEquals("FAILED", AutopilotService.runStatus("CANCELLED"));
        assertEquals("RUNNING", AutopilotService.runStatus("WAITING_REVIEW"));
    }

    @Test
    void postKitIsCleanedForYouTube() {
        PostKit k = AutopilotService.clean(new PostKit("The <quiet> station #dream " + "x".repeat(120), "Calm <b>place</b>.",
                List.of("#Dreamcore", "liminal spaces", "#shorts", "#a", "#b", "#c")));
        assertTrue(k.title().length() <= 100 && !k.title().contains("<") && !k.title().contains("#"));
        assertEquals("#Shorts", k.hashtags().getFirst());
        assertEquals(5, k.hashtags().size());
        assertTrue(k.hashtags().contains("#liminalspaces"));
        assertEquals(1, k.hashtags().stream().filter(h -> h.equalsIgnoreCase("#shorts")).count());
        assertThrows(IllegalStateException.class, () -> AutopilotService.clean(new PostKit(" ", "", List.of())));
    }
}
