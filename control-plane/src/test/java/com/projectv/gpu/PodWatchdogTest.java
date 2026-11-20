package com.projectv.gpu;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** The watchdog against a fake RunPod REST v2 server on localhost (no network, no real pods). */
class PodWatchdogTest {

    static final Instant NOW = Instant.parse("2026-11-19T12:00:00Z");
    static final Clock CLOCK = Clock.fixed(NOW, ZoneOffset.UTC);

    HttpServer server;
    volatile String podsJson = "{\"pods\":[],\"pagination\":{\"nextCursor\":null,\"hasNextPage\":false}}";
    volatile int listStatus = 200;
    final List<String> deleted = new CopyOnWriteArrayList<>();
    final List<String> authHeaders = new CopyOnWriteArrayList<>();
    final FakeLeases leases = new FakeLeases();
    final List<String> evidence = new ArrayList<>();
    final PodEvents events = (provider, pod, reason, hours, usd) -> evidence.add(pod.id() + "|" + reason + "|" + usd);

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/v2/pods", ex -> {
            authHeaders.add(ex.getRequestHeaders().getFirst("Authorization"));
            byte[] body;
            int status;
            if ("DELETE".equals(ex.getRequestMethod())) {
                String path = ex.getRequestURI().getPath();
                deleted.add(path.substring(path.lastIndexOf('/') + 1));
                status = 204;
                body = new byte[0];
            } else {
                status = listStatus;
                body = (status == 200 ? podsJson : "{\"error\":\"down\"}").getBytes(StandardCharsets.UTF_8);
            }
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(status, status == 204 ? -1 : body.length);
            if (status != 204) {
                ex.getResponseBody().write(body);
            }
            ex.close();
        });
        server.start();
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    RunPodApi api(String key) {
        return new RunPodApi("http://127.0.0.1:" + server.getAddress().getPort() + "/v2", key);
    }

    PodWatchdog watchdog(boolean dryRun) {
        return new PodWatchdog(api("test-key"), leases, events,
                new GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, dryRun), CLOCK);
    }

    static String pod(String id, String name, int ageMinutes, double cost) {
        String created = NOW.minusSeconds(ageMinutes * 60L).toString();
        return "{\"id\":\"%s\",\"name\":\"%s\",\"status\":\"RUNNING\",\"cost\":%s,\"createdAt\":\"%s\",\"startedAt\":\"%s\"}"
                .formatted(id, name, cost, created, created);
    }

    void pods(String... pods) {
        podsJson = "{\"pods\":[" + String.join(",", pods) + "],\"pagination\":{\"nextCursor\":null,\"hasNextPage\":false}}";
    }

    @Test
    void killsOverAgePodEvenWithAFreshLease() {
        pods(pod("old1", "projectv-wan-1", 130, 0.9));
        leases.open("runpod", "old1", "projectv-wan-1", "wan");
        PodWatchdog.Report r = watchdog(false).sweep();
        assertEquals(List.of("old1"), deleted);
        assertTrue(r.actions().getFirst().done());
        assertTrue(r.actions().getFirst().reason().startsWith("over max lifetime"));
        assertEquals(1, evidence.size(), "a CostEntry/evidence row is written for the kill");
        assertTrue(evidence.getFirst().endsWith("|" + (130 / 60.0 * 0.9)), evidence.getFirst());
        assertNotNull(leases.find("old1").get().releasedAt());
        assertEquals("Bearer test-key", authHeaders.getFirst());
    }

    @Test
    void sparesYoungLeasedPod() {
        pods(pod("young", "projectv-wan-2", 30, 0.9));
        leases.open("runpod", "young", "projectv-wan-2", "wan");
        PodWatchdog.Report r = watchdog(false).sweep();
        assertTrue(deleted.isEmpty());
        assertTrue(r.actions().isEmpty());
        assertEquals(1, r.studioPods());
    }

    @Test
    void sparesYoungPodInsideTheLeaseGraceButKillsAnUnleasedOne() {
        pods(pod("new", "projectv-a", 5, 0.5), pod("orphan", "projectv-b", 11, 0.5));
        watchdog(false).sweep();
        assertEquals(List.of("orphan"), deleted);
    }

    @Test
    void killsIdleAndReleasedPods() {
        pods(pod("idle", "projectv-a", 60, 0.5), pod("done", "projectv-b", 60, 0.5));
        leases.open("runpod", "idle", "projectv-a", "x");
        leases.beats.put("idle", NOW.minusSeconds(16 * 60));
        leases.open("runpod", "done", "projectv-b", "x");
        leases.release("done");
        watchdog(false).sweep();
        assertEquals(List.of("idle", "done"), deleted);
    }

    @Test
    void ignoresPodsWithoutThePrefix() {
        pods(pod("mine", "my-own-jupyter", 900, 2.5), pod("almost", "projectv", 900, 1), pod("x", "Projectv-x", 900, 1));
        PodWatchdog.Report r = watchdog(false).sweep();
        assertTrue(deleted.isEmpty());
        assertEquals(0, r.studioPods());
        assertTrue(evidence.isEmpty());
    }

    @Test
    void dryRunTerminatesNothing() {
        pods(pod("old1", "projectv-wan-1", 500, 0.9));
        PodWatchdog.Report r = watchdog(true).sweep();
        assertTrue(deleted.isEmpty());
        assertTrue(evidence.isEmpty());
        assertEquals(1, r.actions().size());
        assertFalse(r.actions().getFirst().done());
        assertTrue(r.dryRun());
    }

    @Test
    void apiFailureNeverThrowsOutOfTheSweepOrTheScheduler() {
        listStatus = 500;
        PodWatchdog w = watchdog(false);
        PodWatchdog.Report r = assertDoesNotThrow(w::sweep);
        assertNotNull(r.error());
        assertTrue(deleted.isEmpty());
        assertDoesNotThrow(() -> new PodWatchdogScheduler.Tick(w).tick());

        server.stop(0); // connection refused
        assertNotNull(assertDoesNotThrow(w::sweep).error());
    }

    @Test
    void noKeyMeansNoCallsAtAll() {
        pods(pod("old1", "projectv-wan-1", 500, 0.9));
        PodWatchdog w = new PodWatchdog(api(""), leases, events, new GpuPodLimits("projectv-", 120, 15, 10, 2, 2, false), CLOCK);
        PodWatchdog.Report r = w.sweep();
        assertFalse(r.configured());
        assertTrue(authHeaders.isEmpty());
    }

    @Test
    void followsPaginationCursor() {
        server.removeContext("/v2/pods");
        server.createContext("/v2/pods", ex -> {
            String q = ex.getRequestURI().getQuery();
            String body = q != null && q.contains("cursor=c2")
                    ? "{\"pods\":[" + pod("p2", "projectv-2", 200, 1) + "],\"pagination\":{\"nextCursor\":null,\"hasNextPage\":false}}"
                    : "{\"pods\":[" + pod("p1", "projectv-1", 200, 1) + "],\"pagination\":{\"nextCursor\":\"c2\",\"hasNextPage\":true}}";
            byte[] b = body.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(200, b.length);
            ex.getResponseBody().write(b);
            ex.close();
        });
        assertEquals(2, api("k").listPods().size());
    }

    @Test
    void warnsOverLimitsAndStartGuardRefuses() {
        pods(pod("a", "projectv-a", 5, 1.2), pod("b", "projectv-b", 5, 1.2), pod("c", "projectv-c", 5, 1.2));
        PodWatchdog w = watchdog(true);
        assertEquals(2, w.sweep().warnings().size());
        assertThrows(IllegalStateException.class, () -> w.checkCanStart(0.5));

        pods(pod("a", "projectv-a", 5, 1.2), pod("other", "someone-else", 5, 9));
        assertThrows(IllegalStateException.class, () -> w.checkCanStart(0.9), "1.2 + 0.9 > $2/h");
        assertDoesNotThrow(() -> w.checkCanStart(0.7));
    }

    @Test
    void dryRunStillBooksTheRunningCostOfAnOrphanInStepsAndTheRealKillBooksOnlyTheRest() {
        List<String> booked = new ArrayList<>();
        java.util.concurrent.atomic.AtomicReference<Instant> last = new java.util.concurrent.atomic.AtomicReference<>();
        PodEvents ev = new PodEvents() {
            public void terminated(String p, PodApi.Pod pod, String reason, double hours, double usd) {
                booked.add("kill|" + hours);
            }
            public java.util.Optional<Instant> lastOrphanBooking(String id) { return java.util.Optional.ofNullable(last.get()); }
            public void orphanBilled(String p, PodApi.Pod pod, String reason, double hours, double usd) {
                booked.add("orphan|" + hours);
                last.set(NOW);
            }
        };
        pods(pod("orphan", "projectv-orphan", 60, 0.6)); // no lease, 60 min old: past the grace period
        GpuPodLimits dry = new GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, true);
        new PodWatchdog(api("k"), leases, ev, dry, CLOCK).sweep();
        assertEquals(List.of("orphan|1.0"), booked);
        assertTrue(deleted.isEmpty(), "dry-run kills nothing");
        new PodWatchdog(api("k"), leases, ev, dry, CLOCK).sweep();
        assertEquals(List.of("orphan|1.0"), booked, "the same instant is not booked twice");
        new PodWatchdog(api("k"), leases, ev, new GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, false), CLOCK).sweep();
        assertEquals(List.of("orphan|1.0", "kill|0.0"), booked, "the real kill books only what dry-run had not");
    }

    /** In-memory leases. */
    static class FakeLeases implements PodLeases {
        final Map<String, Instant> beats = new HashMap<>();
        final Map<String, Instant> released = new HashMap<>();

        @Override
        public void open(String provider, String podId, String name, String purpose) {
            beats.put(podId, NOW);
        }

        @Override
        public void heartbeat(String podId) {
            beats.put(podId, NOW);
        }

        @Override
        public void release(String podId) {
            released.put(podId, NOW);
        }

        @Override
        public Optional<Lease> find(String podId) {
            return beats.containsKey(podId)
                    ? Optional.of(new Lease(podId, "x", beats.get(podId), released.get(podId)))
                    : Optional.empty();
        }
    }
}
