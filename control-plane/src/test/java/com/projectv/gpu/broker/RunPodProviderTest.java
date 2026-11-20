package com.projectv.gpu.broker;

import com.projectv.gpu.RunPodApi;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** RunPodProvider against a fake REST v2 server on localhost: quote/create/status shapes, no real pods. */
class RunPodProviderTest {

    HttpServer server;
    final List<String> requests = new CopyOnWriteArrayList<>();
    volatile String communityStock = "NONE";
    volatile String createdBody = "";

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/v2/catalog/gpus", ex -> {
            String q = ex.getRequestURI().getQuery();
            requests.add(ex.getRequestMethod() + " " + ex.getRequestURI());
            boolean secure = q.contains("cloud=SECURE");
            String body = secure
                    ? "{\"availability\":\"HIGH\",\"memory\":24,\"price\":{\"community\":0.34,\"secure\":0.89}}"
                    : "{\"availability\":\"" + communityStock + "\",\"memory\":24,\"price\":{\"community\":0.34,\"secure\":0.89}}";
            send(ex, 200, body);
        });
        server.createContext("/v2/pods", ex -> {
            requests.add(ex.getRequestMethod() + " " + ex.getRequestURI());
            if ("POST".equals(ex.getRequestMethod())) {
                createdBody = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
                send(ex, 201, "{\"id\":\"pod123\",\"cost\":0.89}");
            } else if (ex.getRequestURI().getPath().endsWith("/gone")) {
                send(ex, 404, "{}");
            } else {
                send(ex, 200, "{\"id\":\"pod123\",\"status\":\"RUNNING\"}");
            }
        });
        server.start();
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    static void send(com.sun.net.httpserver.HttpExchange ex, int status, String body) throws IOException {
        byte[] b = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(status, b.length);
        ex.getResponseBody().write(b);
        ex.close();
    }

    RunPodProvider provider(String key) {
        String url = "http://127.0.0.1:" + server.getAddress().getPort() + "/v2";
        return new RunPodProvider(new RunPodApi(url, key), url, key);
    }

    static final GpuProvider.GpuSpec SPEC = new GpuProvider.GpuSpec(List.of("NVIDIA GeForce RTX 4090"), 24, 48, 80);

    @Test
    void unconfiguredWithoutKey() {
        assertFalse(provider("").configured());
        assertTrue(provider("k").configured());
    }

    @Test
    void communityPreferredWhenInStock() {
        communityStock = "LOW";
        GpuProvider.Quote q = provider("k").quote(SPEC);
        assertTrue(q.available());
        assertEquals(0.34, q.hourlyUsd());
        assertTrue(q.gpu().endsWith("|COMMUNITY"));
    }

    @Test
    void fallsBackToSecureWhenCommunityHasNoInstances() {
        communityStock = "NONE";
        GpuProvider.Quote q = provider("k").quote(SPEC);
        assertTrue(q.available());
        assertEquals(0.89, q.hourlyUsd());
        assertTrue(q.gpu().endsWith("|SECURE"));
        assertTrue(requests.stream().noneMatch(r -> r.startsWith("POST") || r.startsWith("DELETE")), "a quote never creates");
    }

    @Test
    void createSendsPrefixedNameWrappedCmdAndReturnsProxyEndpoint() {
        RunPodProvider p = provider("k");
        GpuProvider.Quote q = new GpuProvider.Quote("runpod", true, "HIGH", "NVIDIA GeForce RTX 4090|SECURE", 0.89, "");
        GpuProvider.Instance i = p.create(new GpuProvider.CreateRequest("projectv-runpod-abc", "img", 80,
                List.of("bash", "-c", "guard"), Map.of("K", "V"), 8000, "vol1", q));
        assertEquals("pod123", i.id());
        assertEquals("https://pod123-8000.proxy.runpod.net", i.endpoint());
        assertTrue(createdBody.contains("\"name\":\"projectv-runpod-abc\""));
        assertTrue(createdBody.contains("\"cloud\":\"SECURE\""));
        assertTrue(createdBody.contains("\"cmd\":[\"bash\",\"-c\",\"guard\"]"));
        assertTrue(createdBody.contains("\"volumeId\":\"vol1\""));
        assertThrows(IllegalArgumentException.class, () -> p.create(new GpuProvider.CreateRequest("other-pod", "img", 80,
                List.of("x"), Map.of(), 8000, null, q)));
    }

    @Test
    void statusMapsRunningAndGone() {
        RunPodProvider p = provider("k");
        assertEquals(GpuProvider.State.RUNNING, p.status("pod123").state());
        assertEquals(GpuProvider.State.GONE, p.status("gone").state());
    }
}
