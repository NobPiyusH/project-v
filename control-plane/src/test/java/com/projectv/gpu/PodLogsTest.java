package com.projectv.gpu;

import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** The log reader against a fake RunPod SSE endpoint on localhost. */
class PodLogsTest {

    private HttpServer server;
    private String base;
    private final List<String> queries = new CopyOnWriteArrayList<>();
    private final List<String> auth = new CopyOnWriteArrayList<>();

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        base = "http://127.0.0.1:" + server.getAddress().getPort() + "/v2";
        server.start();
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    private void sse(String path, String body, boolean holdOpen) {
        server.createContext(path, ex -> {
            queries.add(ex.getRequestURI().getRawQuery());
            auth.add(ex.getRequestHeaders().getFirst("Authorization"));
            ex.getResponseHeaders().add("Content-Type", "text/event-stream");
            ex.sendResponseHeaders(200, 0);
            OutputStream o = ex.getResponseBody();
            o.write(body.getBytes(StandardCharsets.UTF_8));
            o.flush();
            if (holdOpen) { // the real stream never ends by itself
                try {
                    Thread.sleep(2_500);
                } catch (InterruptedException ignored) {
                    Thread.currentThread().interrupt();
                }
            }
            o.close();
        });
    }

    private static String ev(String ts, String line) {
        return "id: " + ts + "\ndata: {\"ts\":\"" + ts + "\",\"source\":\"container\",\"line\":\"" + line + "\"}\n\n";
    }

    @Test
    void readsLinesAndReturnsWithoutWaitingForTheStreamToEnd() {
        sse("/v2/pods/abcd1234/logs", ev("2026-11-20T01:00:00Z", "booting") + ev("2026-11-20T01:00:05Z", "ready"), true);
        PodLogs logs = new PodLogs(base, "k", 3000, 400);
        long t = System.nanoTime();
        PodLogs.Page p = logs.read("abcd1234", null, 150);
        long ms = (System.nanoTime() - t) / 1_000_000;
        assertEquals(2, p.lines().size());
        assertEquals("booting", p.lines().get(0).line());
        assertEquals("2026-11-20T01:00:05Z", p.next());
        assertNull(p.error());
        assertTrue(ms < 2500, "returned once the stream went quiet, took " + ms + " ms");
        assertEquals("tail=150", queries.get(0));
        assertEquals("Bearer k", auth.get(0));
    }

    @Test
    void usesSinceWhenGivenAndKeepsItWhenNothingNew() {
        sse("/v2/pods/abcd1234/logs", "", true);
        PodLogs logs = new PodLogs(base, "k", 700, 200);
        PodLogs.Page p = logs.read("abcd1234", "2026-11-20T01:00:05Z", 150);
        assertTrue(p.lines().isEmpty());
        assertEquals("2026-11-20T01:00:05Z", p.next());
        assertTrue(queries.get(0).startsWith("since=2026-11-20T01%3A00%3A05Z"));
    }

    @Test
    void reportsAnUpstreamErrorInsteadOfThrowing() {
        server.createContext("/v2/pods/abcd1234/logs", ex -> {
            ex.sendResponseHeaders(502, -1);
            ex.close();
        });
        PodLogs.Page p = new PodLogs(base, "k", 1500, 300).read("abcd1234", null, 10);
        assertTrue(p.lines().isEmpty());
        assertNotNull(p.error());
        assertTrue(p.error().contains("502"));
    }

    @Test
    void rejectsABadPodIdAndMissingKeyWithoutCallingRunPod() {
        PodLogs logs = new PodLogs(base, "k", 500, 100);
        assertEquals("Not a valid pod id", logs.read("../secrets", null, 10).error());
        assertEquals("Not a valid pod id", logs.read("a/b", null, 10).error());
        assertNotNull(new PodLogs(base, "", 500, 100).read("abcd1234", null, 10).error());
        assertTrue(queries.isEmpty());
    }

    @Test
    void skipsMalformedEventsAndTruncatesHugeLines() {
        String huge = "x".repeat(5000);
        sse("/v2/pods/abcd1234/logs", "data: not json\n\n" + ev("2026-11-20T01:00:00Z", huge), true);
        PodLogs.Page p = new PodLogs(base, "k", 2000, 300).read("abcd1234", null, 10);
        assertEquals(1, p.lines().size());
        assertEquals(PodLogs.MAX_LINE_CHARS + 1, p.lines().get(0).line().length());
    }
}
