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
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** VastProvider against a fake Vast REST server on localhost: search filter, create, start, never-ready, disappear, destroy. No real instances. */
class VastProviderTest {

    static final String KEY = "vast-key-must-never-reach-an-instance";
    static final String SESSION = "vast-2fa-session-key-must-never-reach-an-instance";
    static final GpuProvider.GpuSpec SPEC = new GpuProvider.GpuSpec(List.of("ignored"), 24, 48, 80);

    HttpServer server;
    final List<String> requests = new CopyOnWriteArrayList<>();
    final List<String> authHeaders = new CopyOnWriteArrayList<>();
    volatile String searchBody = "";
    volatile String createBody = "";
    final List<String> createdAsks = new CopyOnWriteArrayList<>();
    final List<String> destroyed = new CopyOnWriteArrayList<>();
    final Map<String, AtomicInteger> showCalls = new ConcurrentHashMap<>();
    volatile String offers = "[]";
    /** asks that answer 410 (taken by someone else). */
    final List<String> takenAsks = new CopyOnWriteArrayList<>();
    /** per instance id: the show-instance JSON for call number n (the last one repeats). */
    final Map<String, List<String>> script = new ConcurrentHashMap<>();
    volatile int listStatus = 200;
    volatile int destroyStatus = 200;
    volatile String instancesList = "{\"success\":true,\"instances\":[],\"next_token\":null}";
    volatile String instancesPage2 = "{\"success\":true,\"instances\":[],\"next_token\":null}";
    final AtomicInteger nextId = new AtomicInteger(777);

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/api/v0/bundles/", ex -> {
            record(ex);
            searchBody = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            send(ex, 200, "{\"offers\":" + offers + ",\"truncated\":false}");
        });
        server.createContext("/api/v0/asks/", ex -> {
            record(ex);
            String ask = ex.getRequestURI().getPath().replaceAll(".*/asks/(\\d+)/?", "$1");
            createBody = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            if (takenAsks.contains(ask)) {
                send(ex, 410, "{\"success\":false,\"error\":\"no_such_ask\",\"msg\":\"gone\"}");
                return;
            }
            createdAsks.add(ask);
            send(ex, 200, "{\"success\":true,\"new_contract\":" + nextId.getAndIncrement() + "}");
        });
        // destroy stays v0 (v1 has no by-id route)
        server.createContext("/api/v0/instances/", ex -> {
            record(ex);
            String id = ex.getRequestURI().getPath().replaceAll(".*/instances/?(\\d*)/?", "$1");
            if (!"DELETE".equals(ex.getRequestMethod())) {
                send(ex, 410, "{\"error\":\"deprecated_endpoint\",\"msg\":\"/api/v0/instances/ is deprecated. Use /api/v1/instances/ instead.\"}");
            } else if (listStatus != 200) {
                send(ex, listStatus, "{\"success\":false,\"msg\":\"requires Two Factor Authentication\"}");
            } else if (destroyStatus == 200) {
                destroyed.add(id);
                send(ex, 200, "{\"success\":true}");
            } else {
                send(ex, destroyStatus, "{\"success\":false,\"msg\":\"nope\"}");
            }
        });
        // list and show are v1: GET /api/v1/instances/?limit=..&after_token=..&select_filters={"id":{"eq":N}}
        server.createContext("/api/v1/instances/", ex -> {
            record(ex);
            String q = String.valueOf(ex.getRequestURI().getQuery());
            if (listStatus != 200) {
                send(ex, listStatus, "{\"success\":false,\"msg\":\"requires Two Factor Authentication\"}");
                return;
            }
            java.util.regex.Matcher m = java.util.regex.Pattern.compile("\"id\":\\{\"eq\":(\\d+)\\}").matcher(q);
            if (m.find()) {
                String id = m.group(1);
                List<String> steps = script.get(id);
                if (steps == null) {
                    send(ex, 200, "{\"success\":true,\"total_instances\":0,\"instances\":[],\"next_token\":null}");
                } else {
                    int n = showCalls.computeIfAbsent(id, k -> new AtomicInteger()).getAndIncrement();
                    send(ex, 200, "{\"success\":true,\"total_instances\":1,\"instances\":[{\"id\":" + id + "," + steps.get(Math.min(n, steps.size() - 1)).substring(1)
                            + "],\"next_token\":null}");
                }
            } else if (q.contains("after_token=p2")) {
                send(ex, 200, instancesPage2);
            } else {
                send(ex, 200, instancesList);
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
        authHeaders.add(String.valueOf(ex.getRequestHeaders().getFirst("Authorization")));
    }

    static void send(HttpExchange ex, int status, String body) throws IOException {
        byte[] b = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(status, b.length);
        ex.getResponseBody().write(b);
        ex.close();
    }

    VastConfig cfg(int startTimeoutSeconds, int attempts) {
        return new VastConfig(List.of("RTX 4090", "RTX 5090", "L40S", "RTX A6000"), 0.97, 500, 90, 0.60, 50, 3, 0.75,
                startTimeoutSeconds, 1, attempts, 1.25, 0);
    }

    VastProvider provider(String key, VastConfig cfg) {
        String url = "http://127.0.0.1:" + server.getAddress().getPort() + "/api/v0";
        return new VastProvider(cfg, url, key, () -> key.isEmpty() ? "" : SESSION, RestClient.builder().build(), 5);
    }

    VastProvider provider() {
        return provider(KEY, cfg(2, 2));
    }

    /** One offer JSON like the live search answer (fields checked on 21 Nov 2026). */
    static String offer(long id, String gpu, double dph, double reliability, double perGbDown) {
        return offer(id, gpu, dph, reliability, perGbDown, "verified", 1, "1.2.3.4", false);
    }

    static String offer(long id, String gpu, double dph, double reliability, double perGbDown, String verification, int gpus,
                        String ip, boolean bid) {
        return ("{\"id\":%d,\"gpu_name\":\"%s\",\"dph_total\":%s,\"reliability\":%s,\"inet_down_cost\":%s,\"inet_up_cost\":%s,"
                + "\"verification\":\"%s\",\"num_gpus\":%d,\"public_ipaddr\":\"%s\",\"is_bid\":%s,\"rentable\":true,\"rented\":false,"
                + "\"gpu_ram\":24564,\"cpu_ram\":60000,\"disk_space\":200,\"inet_down\":900,\"direct_port_count\":40,"
                + "\"geolocation\":\"Vietnam, VN\",\"machine_id\":51178,\"host_id\":55116}")
                .formatted(id, gpu, dph, reliability, perGbDown, perGbDown, verification, gpus, ip, bid);
    }

    static String running(String ip, int hostPort) {
        return "{\"actual_status\":\"running\",\"public_ipaddr\":\"" + ip + "\",\"ports\":{\"8000/tcp\":[{\"HostIp\":\"0.0.0.0\",\"HostPort\":\""
                + hostPort + "\"}]},\"dph_total\":0.42,\"label\":\"projectv-vast-x\"}";
    }

    static GpuProvider.CreateRequest request(GpuProvider.Quote q) {
        Map<String, String> env = new java.util.LinkedHashMap<>();
        env.put("SV_BOOT_00", "eJy1AAA+/=");
        env.put("SV_BOOT_N", "1");
        env.put("SV_PORT", "8000");
        env.put("SV_ROOT", "/workspace/data");
        env.put("SV_TOKEN", "0123456789abcdef");
        return new GpuProvider.CreateRequest("projectv-vast-abcd1234-ef01", "pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime", 80,
                List.of("bash", "-c", "guard", "projectv-guard", "python", "-c", "x"), env, 8000, null, q);
    }

    // ------------------------------------------------------------------------------------------------ search

    @Test
    void unconfiguredWithoutKeyAndSearchSendsTheFilter() {
        assertFalse(provider("", cfg(2, 2)).configured());
        offers = "[" + offer(1, "RTX 4090", 0.41, 0.98, 0.001) + "]";
        VastProvider p = provider();
        assertTrue(p.configured());
        p.quotes(SPEC);
        for (String must : List.of("\"verified\":{\"eq\":true}", "\"rentable\":{\"eq\":true}", "\"rented\":{\"eq\":false}", "\"num_gpus\":{\"eq\":1}",
                "\"gpu_ram\":{\"gte\":24000}", "\"reliability\":{\"gte\":0.97}", "\"direct_port_count\":{\"gte\":1}",
                "\"disk_space\":{\"gte\":90}", "\"inet_down\":{\"gte\":500.0}", "\"type\":\"on-demand\"", "\"allocated_storage\":90",
                "RTX 4090", "RTX 5090", "L40S", "RTX A6000")) {
            assertTrue(searchBody.contains(must), must + " in " + searchBody);
        }
        assertEquals("Bearer " + KEY, authHeaders.getFirst());
    }

    @Test
    void offerFilterKeepsOnlySafeOffersAndPricesComeFromTheLiveOffer() {
        offers = "[" + String.join(",",
                offer(1, "RTX 4090", 0.30, 0.98, 0.001, "unverified", 1, "1.2.3.4", false),   // not verified
                offer(2, "RTX 4090", 0.31, 0.90, 0.001),                                      // unreliable
                offer(3, "RTX 4090", 0.32, 0.99, 0.001, "verified", 2, "1.2.3.4", false),     // 2 GPUs
                offer(4, "RTX 4090", 0.33, 0.99, 0.001, "verified", 1, "", false),            // no public address
                offer(5, "RTX 4090", 0.34, 0.99, 0.001, "verified", 1, "1.2.3.4", true),      // interruptible bid
                offer(6, "RTX 3090", 0.20, 0.99, 0.001),                                      // GPU model not allowed
                offer(7, "RTX 4090", 0.35, 0.99, 0.0521),                                     // host charges $53/TB: 50 GB in = $2.6
                offer(8, "RTX 4090", 0.40, 0.99, 0.001),                                      // good
                offer(9, "RTX 4090", 0.45, 0.99, 0.001),                                      // good, dearer
                offer(10, "L40S", 0.53, 0.99, 0.001)) + "]";                                  // good, other model
        List<GpuProvider.Quote> q = provider().quotes(SPEC);
        assertEquals(2, q.size(), "one cheapest passing offer per GPU model: " + q);
        GpuProvider.Quote best = q.getFirst();
        assertEquals("vast", best.provider());
        assertTrue(best.gpu().startsWith("RTX 4090|8|"), best.gpu());
        assertTrue(best.gpu().contains("Vietnam, VN") && best.gpu().endsWith("|m51178"), "country and machine id in the key: " + best.gpu());
        // bandwidth (50 GB down + 3 GB up at $0.001/GB = $0.053) is spread over 0.75 h on top of the offer's own price
        assertEquals(0.40 + 0.053 / 0.75, best.hourlyUsd(), 1e-9);
        assertTrue(best.detail().contains("host 55116") && best.detail().contains("machine 51178"), best.detail());
        assertEquals("L40S", q.get(1).gpu().split("\\|")[0]);
        assertTrue(q.get(0).hourlyUsd() <= q.get(1).hourlyUsd(), "cheapest first");
    }

    @Test
    void emptyOrFullyRejectedSearchSaysWhy() {
        offers = "[" + offer(1, "RTX 4090", 0.41, 0.5, 0.001) + "]";
        GpuProvider.Quote q = provider().quote(SPEC);
        assertFalse(q.available());
        assertTrue(q.detail().contains("host reliability too low"), q.detail());
    }

    // ------------------------------------------------------------------------------------------------ create

    @Test
    void createSendsNoSecretsWaitsForTheMappedPortAndReturnsTheDirectAddress() {
        offers = "[" + offer(8, "RTX 4090", 0.40, 0.99, 0.001) + "]";
        VastProvider p = provider();
        GpuProvider.Quote q = p.quote(SPEC);
        script.put("777", List.of("{\"actual_status\":null}", "{\"actual_status\":\"loading\"}", running("5.6.7.8", 41234)));
        GpuProvider.Instance i = p.create(request(q));
        assertEquals("777", i.id());
        assertEquals("http://5.6.7.8:41234", i.endpoint());
        assertEquals(List.of("8"), createdAsks);
        assertEquals(q.hourlyUsd(), i.hourlyUsd(), 1e-9);
        assertEquals("RTX 4090", i.gpu());

        // the body: image, studio label, args mode (no ssh), guard command, worker env + port. Nothing secret.
        assertTrue(createBody.contains("\"label\":\"projectv-vast-abcd1234-ef01\""));
        assertTrue(createBody.contains("\"runtype\":\"args\""));
        assertTrue(createBody.contains("\"disk\":90"));
        assertTrue(createBody.contains("-p 8000:8000"));
        assertTrue(createBody.contains("-e SV_TOKEN=0123456789abcdef"), "the worker token is the one secret the box needs");
        assertTrue(createBody.contains("\"cancel_unavail\":true"));
        assertFalse(createBody.contains(KEY), "the Vast key never goes to an instance");
        for (String forbidden : List.of("RUNPOD", "API_KEY", "S3_", "GEMINI", "AWS_", "SECRET", "ssh_key", "SSH_PUBLIC_KEY", "jupyter")) {
            assertFalse(createBody.toUpperCase().contains(forbidden.toUpperCase()), forbidden + " must not be in the create body");
        }
        assertFalse(createBody.contains(SESSION), "the 2FA session key never goes to an instance");
        assertEquals("Bearer " + KEY, authHeaders.getFirst(), "offer search uses the normal key");
        assertTrue(authHeaders.stream().skip(1).allMatch(h -> h.equals("Bearer " + SESSION)), "create, show and destroy use the 2FA session key");
    }

    @Test
    void envValuesThatCouldBreakTheEnvStringAreRefused() {
        offers = "[" + offer(8, "RTX 4090", 0.40, 0.99, 0.001) + "]";
        VastProvider p = provider();
        GpuProvider.Quote q = p.quote(SPEC);
        var bad = new GpuProvider.CreateRequest("projectv-vast-a", "img", 80, List.of("x"), Map.of("SV_ROOT", "/a b; rm -rf /"), 8000, null, q);
        assertThrows(IllegalArgumentException.class, () -> p.createBody(bad));
        assertThrows(IllegalArgumentException.class, () -> p.create(new GpuProvider.CreateRequest("not-studio", "img", 80, List.of("x"), Map.of(), 8000, null, q)));
        assertTrue(createdAsks.isEmpty());
    }

    @Test
    void instanceThatExitsIsDestroyedAndTheNextOfferIsTried() {
        offers = "[" + offer(8, "RTX 4090", 0.40, 0.99, 0.001) + "," + offer(9, "RTX 5090", 0.45, 0.99, 0.001) + "]";
        VastProvider p = provider();
        GpuProvider.Quote q = p.quote(SPEC);
        script.put("777", List.of("{\"actual_status\":\"loading\"}", "{\"actual_status\":\"exited\"}"));
        script.put("778", List.of(running("9.9.9.9", 40000)));
        GpuProvider.Instance i = p.create(request(q));
        assertEquals("778", i.id());
        assertEquals(List.of("8", "9"), createdAsks);
        assertEquals(List.of("777"), destroyed, "the dead instance was destroyed, not left");
    }

    @Test
    void instanceThatNeverRunsIsDestroyedAfterTheTimeout() {
        offers = "[" + offer(8, "RTX 4090", 0.40, 0.99, 0.001) + "]";
        VastProvider p = provider(KEY, cfg(1, 1));
        GpuProvider.Quote q = p.quote(SPEC);
        script.put("777", List.of("{\"actual_status\":\"loading\"}"));
        IllegalStateException e = assertThrows(IllegalStateException.class, () -> p.create(request(q)));
        assertTrue(e.getMessage().contains("not running"), e.getMessage());
        assertEquals(List.of("777"), destroyed);
    }

    @Test
    void offerTakenBetweenSearchAndCreateFallsToTheNextOfferInsideThePriceSlack() {
        offers = "[" + String.join(",", offer(8, "RTX 4090", 0.40, 0.99, 0.001), offer(9, "RTX 5090", 0.45, 0.99, 0.001),
                offer(10, "L40S", 1.20, 0.99, 0.001)) + "]";
        VastProvider p = provider();
        GpuProvider.Quote q = p.quote(SPEC);
        takenAsks.add("8");
        script.put("777", List.of(running("5.6.7.8", 41234)));
        GpuProvider.Instance i = p.create(request(q));
        assertEquals(List.of("9"), createdAsks, "the dearer-than-slack L40S would never be used");
        assertEquals("777", i.id());
    }

    @Test
    void offerTakenAndNothingCheapEnoughFails() {
        offers = "[" + String.join(",", offer(8, "RTX 4090", 0.40, 0.99, 0.001), offer(10, "L40S", 1.20, 0.99, 0.001)) + "]";
        VastProvider p = provider();
        GpuProvider.Quote q = p.quote(SPEC);
        takenAsks.add("8");
        assertThrows(RuntimeException.class, () -> p.create(request(q)));
        assertTrue(createdAsks.isEmpty());
    }

    @Test
    void neverRentsASecondMachineWhenTheFirstCouldNotBeDestroyed() {
        offers = "[" + String.join(",", offer(8, "RTX 4090", 0.40, 0.99, 0.001), offer(9, "RTX 5090", 0.45, 0.99, 0.001)) + "]";
        VastProvider p = provider();
        GpuProvider.Quote q = p.quote(SPEC);
        script.put("777", List.of("{\"actual_status\":\"exited\"}"));
        destroyStatus = 500;
        IllegalStateException e = assertThrows(IllegalStateException.class, () -> p.create(request(q)));
        assertTrue(e.getMessage().contains("destroying it failed"), e.getMessage());
        assertEquals(List.of("8"), createdAsks, "no second create");
    }

    // ------------------------------------------------------------------------------------------------ status / list / destroy

    @Test
    void statusMapsRunningStartingAndGone() {
        VastProvider p = provider();
        script.put("1", List.of(running("5.6.7.8", 41234)));
        script.put("2", List.of("{\"actual_status\":\"loading\",\"start_date\":" + (System.currentTimeMillis() / 1000.0) + "}"));
        script.put("3", List.of("{\"actual_status\":\"exited\"}"));
        script.put("4", List.of("{\"actual_status\":\"offline\"}"));
        script.put("5", List.of("{\"actual_status\":\"loading\",\"start_date\":1000000000}")); // started long ago, never ran
        GpuProvider.InstanceStatus r = p.status("1");
        assertEquals(GpuProvider.State.RUNNING, r.state());
        assertEquals("http://5.6.7.8:41234", r.endpoint());
        assertEquals(GpuProvider.State.STARTING, p.status("2").state());
        assertEquals(GpuProvider.State.GONE, p.status("3").state(), "exited never reaches running again");
        assertEquals(GpuProvider.State.GONE, p.status("4").state());
        assertEquals(GpuProvider.State.GONE, p.status("5").state(), "never became running");
        assertEquals(GpuProvider.State.GONE, p.status("99").state(), "404: it disappeared (outbid / destroyed from outside)");
    }

    @Test
    void endpointForRebuildsTheAddressAfterARestartAndIsNullWhenUnknown() {
        VastProvider p = provider();
        script.put("1", List.of(running("5.6.7.8", 41234)));
        assertEquals("http://5.6.7.8:41234", p.endpointFor("1", 8000));
        assertEquals(null, p.endpointFor("99", 8000));
    }

    @Test
    void listPodsMapsLabelStatusAndCostAndTheWatchdogCanSeeStudioInstances() {
        instancesList = "{\"success\":true,\"instances\":[{\"id\":11,\"label\":\"projectv-vast-aaaa-bbbb\",\"actual_status\":\"running\",\"dph_total\":0.42,"
                + "\"start_date\":1795000000.5},{\"id\":12,\"label\":\"my-own-box\",\"actual_status\":\"loading\",\"dph_total\":1.5},"
                + "{\"id\":13,\"label\":null,\"actual_status\":null,\"dph_total\":0.1}],\"next_token\":null}";
        List<com.projectv.gpu.PodApi.Pod> pods = provider().listPods();
        assertEquals(3, pods.size());
        assertEquals("11", pods.get(0).id());
        assertEquals("projectv-vast-aaaa-bbbb", pods.get(0).name());
        assertEquals("RUNNING", pods.get(0).status());
        assertEquals(0.42, pods.get(0).costPerHrUsd(), 1e-9);
        assertEquals(java.time.Instant.ofEpochMilli(1795000000500L), pods.get(0).createdAt());
        assertEquals("LOADING", pods.get(2).status());
        assertEquals("", pods.get(2).name());
        assertTrue(pods.get(1).createdAt() != null, "never a null creation time: the watchdog would kill a fresh instance");
        assertNotEquals("projectv-", pods.get(1).name().substring(0, 3));
    }

    @Test
    void listPodsFollowsThePagesOfTheV1List() {
        instancesList = "{\"success\":true,\"instances\":[{\"id\":1,\"label\":\"projectv-vast-a\",\"actual_status\":\"running\",\"dph_total\":0.4}],\"next_token\":\"p2\"}";
        instancesPage2 = "{\"success\":true,\"instances\":[{\"id\":2,\"label\":\"projectv-vast-b\",\"actual_status\":\"exited\",\"dph_total\":0.4}],\"next_token\":null}";
        List<com.projectv.gpu.PodApi.Pod> pods = provider().listPods();
        assertEquals(List.of("1", "2"), pods.stream().map(com.projectv.gpu.PodApi.Pod::id).toList());
        assertEquals("EXITED", pods.get(1).status(), "exited instances are listed so the watchdog destroys them");
        assertTrue(requests.stream().allMatch(r -> r.startsWith("GET /api/v1/instances")), "list uses v1, never the deprecated v0 list: " + requests);
    }

    @Test
    void expiredOrMissingTwoFactorSessionGivesAClearReasonAndNothingIsRentedOrDestroyedBlind() {
        listStatus = 401;
        IllegalStateException e = assertThrows(IllegalStateException.class, () -> provider().listPods());
        assertTrue(e instanceof VastProvider.VastSessionExpired, e.getMessage());
        assertTrue(e.getMessage().startsWith("Vast 2FA session expired: run `vastai tfa login --method-type totp -c <code>`"), e.getMessage());
        assertFalse(e.getMessage().contains(KEY) || e.getMessage().contains(SESSION));
        listStatus = 403;
        assertTrue(assertThrows(IllegalStateException.class, () -> provider().listPods()) instanceof VastProvider.VastSessionExpired);
        // destroy and the status check say the same; status() never turns a login problem into "GONE" (the broker would replace a healthy pod)
        assertTrue(assertThrows(IllegalStateException.class, () -> provider().terminate("5")) instanceof VastProvider.VastSessionExpired);
        assertTrue(assertThrows(IllegalStateException.class, () -> provider().status("5")) instanceof VastProvider.VastSessionExpired);
    }

    @Test
    void searchWithABadNormalKeyKeepsTheOldMessage() {
        server.removeContext("/api/v0/bundles/");
        server.createContext("/api/v0/bundles/", ex -> send(ex, 401, "{\"success\":false,\"msg\":\"bad key\"}"));
        IllegalStateException e = assertThrows(IllegalStateException.class, () -> provider().quotes(SPEC));
        assertTrue(e.getMessage().contains("refused the key") && !(e instanceof VastProvider.VastSessionExpired), e.getMessage());
    }

    @Test
    void sessionKeyComesFromTheSettingElseTheVastCliFileAndIsReadFreshEachTime(@org.junit.jupiter.api.io.TempDir java.nio.file.Path dir) throws Exception {
        java.nio.file.Path f = dir.resolve("vast_tfa_key");
        var fromFile = VastProvider.sessionKeySource("", f);
        assertEquals("", fromFile.get(), "no file, no setting: empty");
        java.nio.file.Files.writeString(f, "abc123\r\n");
        assertEquals("abc123", fromFile.get());
        java.nio.file.Files.writeString(f, "newer");
        assertEquals("newer", fromFile.get(), "a fresh `vastai tfa login` is used without a restart");
        assertEquals("fromsetting", VastProvider.sessionKeySource(" fromsetting ", f).get(), "the setting wins over the file");
    }

    @Test
    void terminateDestroysAndTreatsAlreadyGoneAsDone() {
        VastProvider p = provider();
        p.terminate("777");
        assertEquals(List.of("777"), destroyed);
        assertTrue(requests.contains("DELETE /api/v0/instances/777/"));
        destroyStatus = 404;
        p.terminate("778"); // already gone: fine
        destroyStatus = 500;
        assertThrows(RuntimeException.class, () -> p.terminate("779"));
    }

    @Test
    void guardIsTheNoKeyExitGuard() {
        List<String> argv = provider().guard(List.of("python", "w.py"), 120, 15, java.time.Instant.parse("2026-11-21T12:00:00Z"));
        assertFalse(argv.get(2).contains("RUNPOD"));
        assertTrue(argv.get(2).contains("kill -TERM"));
        List<String> all = new ArrayList<>(argv);
        assertTrue(all.contains("w.py"));
    }
}
