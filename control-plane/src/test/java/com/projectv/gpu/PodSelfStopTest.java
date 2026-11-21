package com.projectv.gpu;

import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class PodSelfStopTest {

    @Test
    void wrapsTheJobWithAnAbsoluteDeadlineAndIdleLimit() {
        Instant created = Instant.parse("2026-11-19T12:00:00Z");
        List<String> argv = PodSelfStop.wrap(List.of("python", "-m", "worker", "--batch", "it's"), 120, 15, created);
        assertEquals(List.of("bash", "-c"), argv.subList(0, 2));
        assertEquals("projectv-guard", argv.get(3), "$0 of the script");
        assertEquals(List.of("python", "-m", "worker", "--batch", "it's"), argv.subList(4, argv.size()),
                "the job's argv is passed through untouched as \"$@\"");
        String s = argv.get(2);
        assertTrue(s.contains("DEADLINE=" + (created.getEpochSecond() + 7200) + ";"), s);
        assertTrue(s.contains("IDLE=900;"));
        assertTrue(s.contains("runpodctl pod delete \"$RUNPOD_POD_ID\""), "terminate first, not stop");
        assertTrue(s.indexOf("pod delete") < s.indexOf("pod stop"), "stop only as the last resort");
        assertTrue(s.contains("DELETE") && s.contains("/v2/pods/$RUNPOD_POD_ID"));
        assertTrue(s.contains("\"$@\"\nrc=$?\nselfterm \"job finished"), "the pod ends when the job ends");
        assertTrue(s.contains("nvidia-smi"), "a busy GPU counts as not idle");
    }

    @Test
    void exitGuardHoldsNoProviderKeyAndOnlyEndsTheWorker() {
        Instant created = Instant.parse("2026-11-21T12:00:00Z");
        List<String> argv = PodSelfStop.wrapExit(List.of("python", "-c", "x"), 120, 15, created);
        assertEquals(List.of("bash", "-c"), argv.subList(0, 2));
        assertEquals(List.of("python", "-c", "x"), argv.subList(4, argv.size()));
        String s = argv.get(2);
        assertTrue(s.contains("DEADLINE=" + (created.getEpochSecond() + 7200) + ";") && s.contains("IDLE=900;"));
        for (String forbidden : List.of("RUNPOD", "runpodctl", "curl", "API_KEY", "Authorization", "vast")) {
            assertTrue(!s.contains(forbidden), "no provider call or key in the in-pod guard: " + forbidden);
        }
        assertTrue(s.contains("kill -TERM \"$JOB\""), "it ends the worker process");
        assertTrue(s.contains("wait \"$JOB\"") && s.contains("exit \"$rc\""), "the container exits when the worker ends");
        assertTrue(s.contains("nvidia-smi"), "a busy GPU counts as not idle");
        assertThrows(IllegalArgumentException.class, () -> PodSelfStop.wrapExit(List.of(), 10, 5, created));
    }

    @Test
    void shellStringQuotesSafely() {
        assertEquals("'bash' '-c' 'echo '\"'\"'hi'\"'\"''", PodSelfStop.asShellString(List.of("bash", "-c", "echo 'hi'")));
    }

    @Test
    void rejectsBadInput() {
        Instant t = Instant.EPOCH;
        assertThrows(IllegalArgumentException.class, () -> PodSelfStop.wrap(List.of(), 10, 5, t));
        assertThrows(IllegalArgumentException.class, () -> PodSelfStop.wrap(List.of("x"), 0, 5, t));
        assertThrows(IllegalArgumentException.class, () -> new GpuPodLimits("", 1, 1, 1, 1, 1, true));
    }
}
