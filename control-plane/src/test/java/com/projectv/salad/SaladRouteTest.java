package com.projectv.salad;

import com.projectv.dream.DreamService;
import com.projectv.gpu.GpuPodLimits;
import com.projectv.gpu.PodEvents;
import com.projectv.gpu.PodLeases;
import com.projectv.gpu.PodWatchdog;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.Pricing;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.math.BigDecimal;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.zip.Inflater;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** The Salad route against a fake SaladCloud REST server on localhost: no network, no real group, no spend. */
class SaladRouteTest {

    HttpServer server;
    final List<String> calls = new CopyOnWriteArrayList<>();
    final List<String> createBodies = new CopyOnWriteArrayList<>();
    final Map<String, String> groups = new HashMap<>(); // name -> status
    volatile Instant listCreated = Instant.parse("2026-11-20T10:00:00Z");
    volatile int deleteStatus = 202;
    volatile String listJson = "{\"items\":[]}";

    final Fakes fakes = new Fakes();

    static final String GPU_CLASSES = """
            {"items":[
             {"id":"11111111-1111-1111-1111-111111111111","name":"RTX 5090 (32 GB)","prices":[
               {"priority":"high","price":"0.600"},{"priority":"batch","price":"0.250"}]},
             {"id":"22222222-2222-2222-2222-222222222222","name":"RTX 4090 (24 GB)","prices":[{"priority":"high","price":"0.440"}]}]}
            """;

    @BeforeEach
    void start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/api/public/organizations/org", ex -> {
            String method = ex.getRequestMethod();
            String path = ex.getRequestURI().getPath().substring("/api/public/organizations/org".length());
            calls.add(method + " " + path + " key=" + ex.getRequestHeaders().getFirst("Salad-Api-Key"));
            int status = 200;
            String body = "{}";
            if (path.equals("/gpu-classes")) {
                body = GPU_CLASSES;
            } else if (path.equals("/projects/default/containers") && method.equals("POST")) {
                String req = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
                createBodies.add(req);
                String name = req.replaceAll("(?s).*\"name\":\"([^\"]+)\".*", "$1");
                groups.put(name, "running");
                status = 201;
                body = "{\"name\":\"" + name + "\",\"current_state\":{\"status\":\"pending\"},\"networking\":{\"dns\":\"" + name + ".salad.cloud\"}}";
            } else if (path.equals("/projects/default/containers") && method.equals("GET")) {
                body = listJson;
            } else if (path.startsWith("/projects/default/containers/")) {
                String name = path.substring(path.lastIndexOf('/') + 1);
                if (method.equals("DELETE")) {
                    status = deleteStatus;
                    if (deleteStatus < 300) {
                        groups.remove(name);
                    }
                    body = "";
                } else if (!groups.containsKey(name)) {
                    status = 404;
                    body = "{\"title\":\"not found\"}";
                } else {
                    body = "{\"name\":\"" + name + "\",\"current_state\":{\"status\":\"" + groups.get(name)
                            + "\"},\"networking\":{\"dns\":\"" + name + ".salad.cloud\"}}";
                }
            }
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(status, bytes.length == 0 ? -1 : bytes.length);
            if (bytes.length > 0) {
                ex.getResponseBody().write(bytes);
            }
            ex.close();
        });
        server.start();
    }

    @AfterEach
    void stop() {
        server.stop(0);
    }

    SaladSettings settings(String key, String priority, boolean dryRun) {
        return new SaladSettings("http://127.0.0.1:" + server.getAddress().getPort() + "/api/public", key, "org", "default",
                "pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime", priority, List.of("RTX 5090", "RTX 4090"), 8, 49152, 120,
                8000, 30, "../workers/salad/server.py", dryRun, "projectv-salad-", 90, 15, 10, 1, 1.0);
    }

    /** In-memory leases, store and ledger. */
    static class Fakes implements PodLeases, SaladStore {
        final Map<String, Lease> leases = new HashMap<>();
        final List<String> leaseLog = new ArrayList<>();
        final Map<UUID, SaladSessions.Session> sessions = new HashMap<>();
        final Map<String, Double> booked = new HashMap<>();
        final List<String> costs = new ArrayList<>();
        final List<String> events = new ArrayList<>();

        public void open(String provider, String podId, String name, String purpose) {
            leases.put(podId, new Lease(podId, purpose, Instant.now(), null));
            leaseLog.add("open " + podId);
        }

        public void heartbeat(String podId) {
            leaseLog.add("beat " + podId);
        }

        public void release(String podId) {
            Lease l = leases.get(podId);
            if (l != null) {
                leases.put(podId, new Lease(podId, l.purpose(), l.heartbeatAt(), Instant.now()));
            }
            leaseLog.add("release " + podId);
        }

        public Optional<Lease> find(String podId) {
            return Optional.ofNullable(leases.get(podId));
        }

        public void insert(String name, UUID dreamId, String url, String gpuClass, String priority, double hourlyUsd) {
            sessions.put(dreamId, new SaladSessions.Session(name, url, hourlyUsd, gpuClass, Instant.now().minusSeconds(1800)));
            booked.put(name, 0.0);
        }

        public Optional<SaladSessions.Session> open(UUID dreamId) {
            return Optional.ofNullable(sessions.get(dreamId));
        }

        public void addBooked(UUID dreamId, double seconds) {
            sessions.computeIfPresent(dreamId, (k, s) -> {
                booked.merge(s.groupName(), seconds, Double::sum);
                return s;
            });
        }

        public double booked(String groupName) {
            return booked.getOrDefault(groupName, 0.0);
        }

        public void markDeleted(String groupName) {
            sessions.values().removeIf(s -> s.groupName().equals(groupName));
        }

        public void cost(UUID dreamId, BigDecimal inr, double units, String note) {
            costs.add(inr + "|" + units + "|" + note);
        }
    }

    SaladSessions sessions(SaladApi api, double spentMonthInr, double spentTotalInr, boolean dryRun) throws Exception {
        Pricing pricing = new Pricing("../config");
        BudgetGuard budget = new BudgetGuard(null, pricing) {
            @Override
            public BigDecimal saladSpentThisMonth() {
                return BigDecimal.valueOf(spentMonthInr);
            }

            @Override
            public BigDecimal saladSpentTotal() {
                return BigDecimal.valueOf(spentTotalInr);
            }
        };
        PodEvents events = (provider, pod, reason, hours, usd) -> fakes.events.add(pod.id() + "|" + reason);
        return new SaladSessions(api, fakes, fakes, pricing, budget, new SaladWatchdogConfig(api, fakes, events));
    }

    // ------------------------------------------------------------------------------------------- tests

    @Test
    void picksFirstConfiguredClassWithLivePrice() {
        var api = new SaladApi(settings("k", "high", true));
        var gpu = api.pickGpu();
        assertEquals("RTX 5090 (32 GB)", gpu.name());
        assertEquals(0.6, gpu.hourlyUsd(), 1e-9);
        // priority with no price for the 5090-or-4090 list ("low" is absent everywhere): a clear failure, never a guess
        assertThrows(IllegalStateException.class, () -> new SaladApi(settings("k", "low", true)).pickGpu());
        // 4090 is used when only it has a price for the priority
        assertEquals("RTX 4090 (24 GB)", new SaladApi(new SaladSettings(settings("k", "high", true).baseUrl(), "k", "org", "default", "i",
                "high", List.of("RTX 4090"), 8, 1, 1, 8000, 30, "w", true, "p-", 1, 1, 1, 1, 1)).pickGpu().name());
    }

    @Test
    void createsOneGroupReusesItAndDeletesIt() throws Exception {
        var api = new SaladApi(settings("secret-key", "high", true));
        var sessions = sessions(api, 0, 0, true);
        UUID dream = UUID.randomUUID();

        var s1 = sessions.ensure(dream);
        assertTrue(s1.groupName().startsWith("projectv-salad-" + dream.toString().substring(0, 8)));
        assertEquals("https://" + s1.groupName() + ".salad.cloud", s1.url());
        assertEquals(0.6, s1.hourlyUsd(), 1e-9);
        assertEquals(1, createBodies.size());
        String body = createBodies.getFirst();
        assertTrue(body.contains("\"priority\":\"high\"") && body.contains("11111111-1111-1111-1111-111111111111"));
        assertTrue(body.contains("SV_BOOT_00") && body.contains("\"auth\":true") && body.contains("pytorch/pytorch"));
        assertTrue(calls.stream().allMatch(c -> c.endsWith("key=secret-key")), "every call sends the API key header");

        var s2 = sessions.ensure(dream); // reuse: no second group
        assertEquals(s1.groupName(), s2.groupName());
        assertEquals(1, createBodies.size());
        assertTrue(fakes.leaseLog.contains("open " + s1.groupName()) && fakes.leaseLog.contains("beat " + s1.groupName()));

        sessions.bookedJobSeconds(dream, 100);
        sessions.release(dream, "test");
        assertFalse(groups.containsKey(s1.groupName()), "group deleted at Salad");
        assertTrue(fakes.leaseLog.contains("release " + s1.groupName()));
        assertEquals(1, fakes.costs.size(), "the rest of the group time (alive minus booked jobs) is booked");
        assertTrue(fakes.costs.getFirst().contains("not covered by jobs"));
        assertTrue(sessions.open(dream).isEmpty());
    }

    @Test
    void groupLostAtSaladIsRecreated() throws Exception {
        var sessions = sessions(new SaladApi(settings("k", "high", true)), 0, 0, true);
        UUID dream = UUID.randomUUID();
        var first = sessions.ensure(dream);
        groups.remove(first.groupName()); // Salad lost it (404)
        var second = sessions.ensure(dream);
        assertEquals(2, createBodies.size());
        assertTrue(!first.groupName().equals(second.groupName()));
    }

    @Test
    void capExceededCreatesNothing() throws Exception {
        // 5 USD monthly cap; the ledger says the month is already spent
        Pricing pricing = new Pricing("../config");
        var sessions = sessions(new SaladApi(settings("k", "high", true)), pricing.saladMonthlyCapInr().doubleValue(), 0, true);
        var e = assertThrows(BudgetGuard.BudgetExceededException.class, () -> sessions.ensure(UUID.randomUUID()));
        assertTrue(e.getMessage().contains("Salad cap"));
        assertTrue(createBodies.isEmpty(), "no group is created over the cap");
        // the all-time ceiling (the owner's credit) stops it too
        var total = sessions(new SaladApi(settings("k", "high", true)), 0, pricing.saladTotalCapInr().doubleValue(), true);
        assertThrows(BudgetGuard.BudgetExceededException.class, () -> total.ensure(UUID.randomUUID()));
        assertTrue(createBodies.isEmpty());
    }

    @Test
    void failedDeleteLeavesAReleasedLeaseForTheWatchdog() throws Exception {
        var sessions = sessions(new SaladApi(settings("k", "high", true)), 0, 0, true);
        UUID dream = UUID.randomUUID();
        var s = sessions.ensure(dream);
        deleteStatus = 500;
        sessions.release(dream, "test"); // never throws
        assertTrue(groups.containsKey(s.groupName()), "still there");
        assertNotNull(fakes.leases.get(s.groupName()).releasedAt(), "released lease: the watchdog deletes it next sweep");
        assertTrue(sessions.open(dream).isPresent(), "not marked deleted, so a later release retries");
    }

    @Test
    void watchdogDeletesOverdueStudioGroupsOnlyAndDryRunDoesNot() {
        Instant now = Instant.parse("2026-11-20T12:00:00Z");
        listJson = """
                {"items":[
                 {"name":"projectv-salad-aaaa1111-bbbb","current_state":{"status":"running"},"create_time":"2026-11-20T10:00:00Z"},
                 {"name":"owner-own-group","current_state":{"status":"running"},"create_time":"2026-11-20T01:00:00Z"}]}
                """;
        groups.put("projectv-salad-aaaa1111-bbbb", "running");
        groups.put("owner-own-group", "running");
        var api = new SaladApi(settings("k", "high", true));
        var limits = new GpuPodLimits("projectv-salad-", 90, 15, 10, 1, 1.0, true);
        var dry = new PodWatchdog(api, fakes, (p, pod, r, h, u) -> fakes.events.add(pod.id()), limits, Clock.fixed(now, ZoneOffset.UTC));
        var r1 = dry.sweep();
        assertEquals(1, r1.studioPods(), "only the studio-prefixed group is seen");
        assertEquals(1, r1.actions().size());
        assertFalse(r1.actions().getFirst().done());
        assertTrue(groups.containsKey("projectv-salad-aaaa1111-bbbb"));

        var live = new PodWatchdog(api, fakes, (p, pod, r, h, u) -> fakes.events.add(pod.id()),
                new GpuPodLimits("projectv-salad-", 90, 15, 10, 1, 1.0, false), Clock.fixed(now, ZoneOffset.UTC));
        var r2 = live.sweep();
        assertTrue(r2.actions().getFirst().done());
        assertTrue(r2.actions().getFirst().reason().contains("max lifetime"));
        assertFalse(groups.containsKey("projectv-salad-aaaa1111-bbbb"), "overdue group deleted");
        assertTrue(groups.containsKey("owner-own-group"), "the owner's other groups are never touched");
        assertTrue(fakes.events.contains("projectv-salad-aaaa1111-bbbb"));
    }

    @Test
    void bootPayloadFitsSaladLimitsAndRoundTrips() throws Exception {
        byte[] src = java.nio.file.Files.readAllBytes(java.nio.file.Path.of("../workers/salad/server.py"));
        Map<String, String> env = SaladBoot.env(src, 8000);
        int n = Integer.parseInt(env.get("SV_BOOT_N"));
        StringBuilder b64 = new StringBuilder();
        for (int i = 0; i < n; i++) {
            String v = env.get("SV_BOOT_%02d".formatted(i));
            assertTrue(v.length() <= 1000, "env value over Salad's 1000-char limit");
            b64.append(v);
        }
        Inflater inf = new Inflater();
        inf.setInput(Base64.getDecoder().decode(b64.toString()));
        byte[] out = new byte[src.length + 16];
        int len = inf.inflate(out);
        assertEquals(new String(src, StandardCharsets.UTF_8), new String(out, 0, len, StandardCharsets.UTF_8));
        assertTrue(SaladBoot.command().stream().allMatch(c -> c.length() <= 1000));
        assertEquals("python", SaladBoot.command().getFirst());
    }

    @Test
    void flagOffLeavesDreamServiceOnModalAndRejectsBadValues() throws Exception {
        Pricing pricing = new Pricing("../config");
        BudgetGuard budget = new BudgetGuard(null, pricing);
        DreamService modal = new DreamService(null, null, null, null, pricing, budget, null, null, "z", "Z", "w", "W", "http://x",
                BigDecimal.ZERO, 3.5, "", "m", "", "v", "b", true);
        assertFalse(modal.usesSalad(), "default constructor = Modal route");
        assertThrows(IllegalArgumentException.class, () -> new DreamService(null, null, null, null, pricing, budget, null, null,
                "z", "Z", "w", "W", "http://x", BigDecimal.ZERO, 3.5, "", "m", "", "v", "b", true, null, "runpod"));
        assertThrows(IllegalStateException.class, () -> new DreamService(null, null, null, null, pricing, budget, null, null,
                "z", "Z", "w", "W", "http://x", BigDecimal.ZERO, 3.5, "", "m", "", "v", "b", true, null, "salad"));
        assertTrue(new SaladSettings("u", "", "", "p", "i", "high", List.of(), 1, 1, 1, 1, 1, "w", true, "x-", 1, 1, 1, 1, 1).configured() == false);
    }

    @Test
    void unconfiguredSaladRefusesToStartAndWatchdogSkips() throws Exception {
        var api = new SaladApi(settings("", "high", true));
        var sessions = sessions(api, 0, 0, true);
        assertThrows(IllegalStateException.class, () -> sessions.ensure(UUID.randomUUID()));
        assertTrue(createBodies.isEmpty());
        var wd = new PodWatchdog(api, fakes, (p, pod, r, h, u) -> { }, new GpuPodLimits("projectv-salad-", 90, 15, 10, 1, 1.0, false),
                Clock.systemUTC());
        assertFalse(wd.sweep().configured());
        assertTrue(calls.isEmpty(), "no API calls without a key");
    }
}
