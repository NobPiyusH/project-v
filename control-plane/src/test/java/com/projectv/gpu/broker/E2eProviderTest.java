package com.projectv.gpu.broker;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.client.RestClient;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** E2eProvider against a fake E2E REST server on localhost: quote, create body, start, never-ready, list, delete. No real nodes. */
class E2eProviderTest {

    static final String KEY = "e2e-api-key-must-never-reach-a-node";
    static final String TOKEN = "e2e-bearer-must-never-reach-a-node";
    static final GpuProvider.GpuSpec SPEC = new GpuProvider.GpuSpec(List.of("x"), 24, 48, 80);

    HttpServer server;
    final List<String> requests = new CopyOnWriteArrayList<>();
    final List<String> queries = new CopyOnWriteArrayList<>();
    volatile String createBody = "";
    volatile String nodeStatus = "Running";
    volatile String nodeIp = "9.8.7.6";
    volatile int deleteStatus = 200;

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/api/v1/images/", ex -> {
            record(ex);
            send(ex, 200, "{\"code\":200,\"data\":[" + row("GDC3.L40S-60.220GB_v1", "p-l40s", 60, 220, 250, 102) + ","
                    + row("GDC3.L4-25.110GB_v1", "p-l4", 25, 110, 250, 49) + ","
                    + row("GDC3.A40-16.100GB", "GDC3-16vCPU-100RAM-750DISK-GDC3.A40-16.100GB_Ubuntu20-Delhi", 16, 100, 750, 96, "Ubuntu-20.04-GPU") + ","
                    + row("GDC3.A40-16.100GB", "GDC3-16vCPU-100RAM-750DISK-GDC3.2xA40-32.200GB_Ubuntu22-Delhi", 16, 100, 750, 90, "Ubuntu-22.04-GPU") + "]}");
        });
        server.createContext("/api/v1/nodes/", ex -> {
            record(ex);
            String path = ex.getRequestURI().getPath();
            String m = ex.getRequestMethod();
            if (m.equals("POST")) {
                createBody = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
                send(ex, 200, "{\"code\":200,\"data\":{\"id\":555,\"name\":\"n\"}}");
            } else if (m.equals("DELETE")) {
                send(ex, deleteStatus, "{\"code\":" + deleteStatus + ",\"message\":\"x\"}");
            } else if (path.endsWith("/nodes/")) {
                send(ex, 200, "{\"code\":200,\"data\":[{\"id\":555,\"name\":\"projectv-e2e-aaaa\",\"status\":\"Running\",\"price\":\"Rs. 102.0/Hour\","
                        + "\"public_ip_address\":\"9.8.7.6\",\"created_at\":\"2026-11-22T01:00:00Z\"}]}");
            } else {
                send(ex, 200, "{\"code\":200,\"data\":{\"id\":555,\"status\":\"" + nodeStatus + "\",\"public_ip_address\":\"" + nodeIp + "\"}}");
            }
        });
        server.start();
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    void record(HttpExchange ex) {
        requests.add(ex.getRequestMethod() + " " + ex.getRequestURI().getPath());
        queries.add(String.valueOf(ex.getRequestURI().getRawQuery()));
    }

    static String row(String sku, String plan, int cpu, int ram, int disk, double inr) {
        return row(sku, plan, cpu, ram, disk, inr, "Ubuntu-22.04-GPU");
    }

    static String row(String sku, String plan, int cpu, int ram, int disk, double inr, String image) {
        return "{\"name\":\"" + sku + "\",\"plan\":\"" + plan + "\",\"image\":\"" + image + "\",\"specs\":{\"cpu\":" + cpu + ",\"ram\":\""
                + ram + ".00\",\"disk_space\":" + disk + ",\"price_per_hour\":" + inr + "}}";
    }

    static void send(HttpExchange ex, int status, String body) throws IOException {
        byte[] b = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(status, b.length);
        ex.getResponseBody().write(b);
        ex.close();
    }

    E2eProvider provider(int startTimeout) {
        E2eConfig c = new E2eConfig(E2eConfig.DEFAULT_PLANS, "Delhi", "Ubuntu-22.04-GPU", 94, 18, 443, startTimeout, 1, 0);
        return new E2eProvider(c, "http://127.0.0.1:" + server.getAddress().getPort() + "/api/v1", KEY, TOKEN, "", RestClient.builder().build(), 5);
    }

    GpuProvider.CreateRequest req(GpuProvider.Quote q) {
        Map<String, String> env = new LinkedHashMap<>();
        env.put("SV_BOOT_00", "QUJD");
        env.put("SV_TOKEN", "tok123");
        return new GpuProvider.CreateRequest("projectv-e2e-aaaa", "img/worker:1", 80, List.of("python", "-c", "print('hi')"), env, 8000, null, q);
    }

    @Test
    void quotesAllowedPlansCheapestFirstInDollarsWithGst() {
        List<GpuProvider.Quote> qs = provider(2).quotes(SPEC);
        assertEquals(2, qs.size(), "L4 has too little VRAM and is not in the allowed plans");
        assertEquals("A40", qs.getFirst().gpu().split("\\|")[0]);
        assertEquals(96 * 1.18 / 94, qs.getFirst().hourlyUsd(), 1e-9);
        assertEquals("L40S", qs.get(1).gpu().split("\\|")[0]);
        assertTrue(queries.getFirst().contains("apikey="));
    }

    @Test
    void createBodyHasNoSecretsAndNoLogin() {
        E2eProvider p = provider(2);
        GpuProvider.Quote q = p.quote(SPEC);
        p.create(req(q));
        assertFalse(createBody.contains(KEY));
        assertFalse(createBody.contains(TOKEN));
        assertTrue(createBody.contains("\"disable_password\":true"));
        assertTrue(createBody.contains("ssh-ed25519 "));
        assertTrue(createBody.contains("SV_TOKEN=tok123"), "the per-rental worker token is the only secret on the node");
        assertTrue(createBody.contains("\"image\":\"Ubuntu-20.04-GPU\""), "the A40 uses its single-GPU template image: " + createBody);
        String run = decodedRunScript(p.startScripts(req(q)));
        assertTrue(run.contains("--gpus all"));
        assertTrue(run.contains("-p 443:8000"));
    }

    /** Reassembles run.b64 from the entries the way the node does. */
    static String decodedRunScript(List<String> scripts) {
        StringBuilder b64 = new StringBuilder();
        for (String sc : scripts) {
            if (sc.contains(">> /opt/projectv/run.b64")) {
                b64.append(sc.substring(sc.indexOf("printf '%s' '") + 13, sc.indexOf("' >> /opt/projectv/run.b64")));
            }
        }
        return new String(java.util.Base64.getDecoder().decode(b64.toString()), StandardCharsets.UTF_8);
    }

    @Test
    void everyStartScriptEntryStaysUnderE2eLimitOf2048Chars() {
        E2eProvider p = provider(2);
        Map<String, String> env = new LinkedHashMap<>();
        for (int i = 0; i < 23; i++) { // the real worker payload: ~23 chunks of 900 chars
            env.put("SV_BOOT_%02d".formatted(i), "A".repeat(900));
        }
        env.put("SV_TOKEN", "t".repeat(64));
        List<String> guard = com.projectv.gpu.PodSelfStop.wrapExit(List.of("python", "-c", "print('x')"), 120, 15, java.time.Instant.now());
        GpuProvider.CreateRequest r = new GpuProvider.CreateRequest("projectv-e2e-aaaa", "ghcr.io/x/worker:latest", 80, guard, env, 8000, null, p.quote(SPEC));
        List<String> scripts = p.startScripts(r);
        assertTrue(scripts.size() > 10);
        scripts.forEach(s -> assertTrue(s.length() <= 2048, "entry of " + s.length() + " chars"));
        String last = scripts.getLast();
        assertTrue(last.contains("sha256sum -c"));
        assertTrue(last.contains(E2eProvider.sha256(decodedRunScript(scripts).getBytes(StandardCharsets.UTF_8))));
    }

    @Test
    void badNodeNamesAreRefusedBeforeCalling() {
        E2eProvider p = provider(2);
        GpuProvider.CreateRequest r = new GpuProvider.CreateRequest("projectv_e2e_aaaa", "img", 80, List.of("x"), Map.of(), 8000, null, p.quote(SPEC));
        assertThrows(IllegalArgumentException.class, () -> p.create(r));
    }

    @Test
    void multiGpuTemplateNamesAreNeverQuoted() {
        provider(2).quotes(SPEC).forEach(q -> assertFalse(q.gpu().contains("2xA40"), q.gpu()));
    }

    @Test
    void largeEnvIsSplitUnderTheScriptLimit() {
        E2eProvider p = provider(2);
        Map<String, String> env = new LinkedHashMap<>();
        for (int i = 0; i < 25; i++) {
            env.put("SV_BOOT_%02d".formatted(i), "A".repeat(900));
        }
        GpuProvider.CreateRequest r = new GpuProvider.CreateRequest("projectv-e2e-aaaa", "img", 80, List.of("x"), env, 8000, null, p.quote(SPEC));
        List<String> scripts = p.startScripts(r);
        assertTrue(scripts.size() >= 3);
        scripts.forEach(s -> assertTrue(s.length() <= 2048));
    }

    @Test
    void unsafeEnvIsRefused() {
        E2eProvider p = provider(2);
        GpuProvider.CreateRequest r = new GpuProvider.CreateRequest("projectv-e2e-aaaa", "img", 80, List.of("x"), Map.of("K", "a\nb"), 8000, null, p.quote(SPEC));
        assertThrows(IllegalArgumentException.class, () -> p.startScripts(r));
    }

    @Test
    void createReturnsEndpointOnPublicIp() {
        E2eProvider p = provider(2);
        GpuProvider.Instance i = p.create(req(p.quote(SPEC)));
        assertEquals("555", i.id());
        assertEquals("http://9.8.7.6:443", i.endpoint());
    }

    @Test
    void neverReadyNodeIsDeleted() {
        nodeStatus = "Creating";
        nodeIp = "";
        E2eProvider p = provider(1);
        assertThrows(IllegalStateException.class, () -> p.create(req(p.quote(SPEC))));
        assertTrue(requests.contains("DELETE /api/v1/nodes/555/"));
    }

    @Test
    void poweredOffCountsAsGoneSoItGetsDeleted() {
        nodeStatus = "Powered off";
        assertEquals(GpuProvider.State.GONE, provider(2).status("555").state());
    }

    @Test
    void listAndTerminate() {
        E2eProvider p = provider(2);
        List<com.projectv.gpu.PodApi.Pod> pods = p.listPods();
        assertEquals("projectv-e2e-aaaa", pods.getFirst().name());
        assertEquals(102 * 1.18 / 94, pods.getFirst().costPerHrUsd(), 1e-9);
        p.terminate("555");
        assertTrue(requests.contains("DELETE /api/v1/nodes/555/"));
    }

    @Test
    void errorMessagesNeverCarryTheKey() {
        server.removeContext("/api/v1/images/");
        server.createContext("/api/v1/images/", ex -> send(ex, 401, "{\"message\":\"bad " + KEY + "\"}"));
        IllegalStateException e = assertThrows(IllegalStateException.class, () -> provider(2).quote(SPEC));
        assertFalse(e.getMessage().contains(KEY));
    }

    @Test
    void notConfiguredWithoutKeys() {
        E2eConfig c = E2eConfig.defaults();
        assertFalse(new E2eProvider(c, "http://x/api/v1", "", "", "", RestClient.builder().build(), 5).configured());
    }
}
