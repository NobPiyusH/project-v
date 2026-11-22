package com.projectv.gpu.broker;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** JarvislabsProvider against a fake helper (no SDK, no network, no instance is ever created). */
class JarvislabsProviderTest {

    static final String KEY = "jl-key-must-never-reach-an-instance";
    static final GpuProvider.GpuSpec SPEC = new GpuProvider.GpuSpec(List.of("x"), 24, 48, 80);

    final List<String> calls = new ArrayList<>();
    final List<String> stdins = new ArrayList<>();
    String gpus = "[{\"gpu_type\":\"A100-80GB\",\"region\":\"IN2\",\"free\":3,\"price_per_hour\":1.49,\"workload_type\":\"container\"},"
            + "{\"gpu_type\":\"A100\",\"region\":\"IN2\",\"free\":2,\"price_per_hour\":0.89,\"workload_type\":\"container\"},"
            + "{\"gpu_type\":\"L4\",\"region\":\"IN2\",\"free\":5,\"price_per_hour\":0.44,\"workload_type\":\"container\"},"
            + "{\"gpu_type\":\"RTX-PRO6000\",\"region\":\"IN1\",\"free\":0,\"price_per_hour\":2.19,\"workload_type\":\"container\"}]";
    String balance = "{\"balance\":500.0,\"grants\":0.0,\"currency\":\"INR\"}";
    String instance = "{\"machine_id\":777,\"name\":\"projectv-jarvislabs-aaaa\",\"status\":\"Running\",\"cost\":0.89,"
            + "\"endpoints\":[\"abc123.jarvislabs.net\"]}";

    JarvislabsProvider provider(String key) {
        return new JarvislabsProvider(JarvislabsConfig.defaults(), key, (args, stdin) -> {
            calls.add(String.join(" ", args));
            stdins.add(stdin);
            return switch (args.getFirst()) {
                case "gpus" -> gpus;
                case "balance" -> balance;
                case "create", "get" -> instance;
                case "list" -> "[" + instance + "]";
                case "destroy" -> "{\"destroyed\":true}";
                default -> "{\"error\":\"?\",\"type\":\"ValueError\"}";
            };
        });
    }

    GpuProvider.CreateRequest req(JarvislabsProvider p) {
        return new GpuProvider.CreateRequest("projectv-jarvislabs-aaaa", "img", 80, List.of("python", "-c", "run it's"),
                Map.of("SV_BOOT_00", "QUJD", "SV_BOOT_N", "1", "SV_PORT", "8000", "SV_TOKEN", "tok"), 8000, null, p.quote(SPEC));
    }

    @Test
    void notConfiguredWithoutAKey() {
        assertFalse(provider("").configured());
        assertTrue(provider(KEY).configured());
    }

    @Test
    void quotesAreTheAllowedGpusInStockCheapestFirst() {
        List<GpuProvider.Quote> q = provider(KEY).quotes(SPEC);
        assertEquals(2, q.size(), "L4 is not on the allow list, RTX-PRO6000 has none free");
        assertEquals("A100|IN2", q.getFirst().gpu());
        assertEquals(0.89, q.getFirst().hourlyUsd());
    }

    @Test
    void noStockIsAnUnavailableQuoteWithAReason() {
        gpus = "[]";
        GpuProvider.Quote q = provider(KEY).quote(SPEC);
        assertFalse(q.available());
    }

    @Test
    void createSendsTheNameGpuAndAScriptWithoutTheKey() {
        JarvislabsProvider p = provider(KEY);
        GpuProvider.Instance i = p.create(req(p));
        assertEquals("777", i.id());
        assertEquals("https://abc123.jarvislabs.net", i.endpoint());
        String body = stdins.getLast();
        assertTrue(body.contains("\"gpu_type\":\"A100\"") && body.contains("\"region\":\"IN2\"") && body.contains("projectv-jarvislabs-aaaa"));
        assertFalse(body.contains(KEY), "the account key never goes into the request, let alone the script");
        String script = p.startScript(req(p));
        assertTrue(script.contains("export SV_PORT=6006") && !script.contains("SV_PORT=8000"), "the worker listens on the published port");
        assertTrue(script.contains("export SV_TOKEN=tok") && script.contains("'run it'\\''s'"), "token set, args shell-quoted");
    }

    @Test
    void createRefusesNamesWithoutTheStudioPrefixAndUnsafeEnv() {
        JarvislabsProvider p = provider(KEY);
        GpuProvider.Quote q = p.quote(SPEC);
        assertThrows(IllegalArgumentException.class, () -> p.create(new GpuProvider.CreateRequest("other", "i", 80, List.of("x"), Map.of(), 8000, null, q)));
        assertThrows(IllegalArgumentException.class, () -> p.startScript(new GpuProvider.CreateRequest("projectv-x", "i", 80, List.of("x"),
                Map.of("A", "b; rm -rf /"), 8000, null, q)));
    }

    @Test
    void statusMapsRunningMissingAndPaused() {
        JarvislabsProvider p = provider(KEY);
        assertEquals(GpuProvider.State.RUNNING, p.status("777").state());
        instance = instance.replace("Running", "Paused");
        assertEquals(GpuProvider.State.GONE, p.status("777").state(), "paused still bills storage: treated as gone so it is destroyed");
        instance = instance.replace("Paused", "Creating");
        assertEquals(GpuProvider.State.STARTING, p.status("777").state());
        JarvislabsProvider missing = new JarvislabsProvider(JarvislabsConfig.defaults(), KEY, (a, s) -> "{\"error\":\"nope\",\"type\":\"NotFoundError\"}");
        assertEquals(GpuProvider.State.GONE, missing.status("1").state());
    }

    @Test
    void listAndTerminateUseTheHelper() {
        JarvislabsProvider p = provider(KEY);
        assertEquals("projectv-jarvislabs-aaaa", p.listPods().getFirst().name());
        p.terminate("777");
        assertEquals("destroy 777", calls.getLast());
    }

    @Test
    void terminateOfAnAlreadyGoneInstanceIsFine() {
        JarvislabsProvider p = new JarvislabsProvider(JarvislabsConfig.defaults(), KEY, (a, s) -> "{\"error\":\"gone\",\"type\":\"NotFoundError\"}");
        p.terminate("5");
    }

    @Test
    void helperErrorsAreScrubbedOfTheKey() {
        JarvislabsProvider p = new JarvislabsProvider(JarvislabsConfig.defaults(), KEY, (a, s) -> "{\"error\":\"bad " + KEY + "\",\"type\":\"AuthError\"}");
        IllegalStateException e = assertThrows(IllegalStateException.class, () -> p.listPods());
        assertFalse(e.getMessage().contains(KEY));
    }

    @Test
    void preflightChecksScriptSizeAndTheGpuList() {
        JarvislabsProvider p = provider(KEY);
        GpuProvider.CreateRequest r = new GpuProvider.CreateRequest("projectv-jarvislabs-preflight", "img", 80, List.of("python"),
                Map.of("SV_BOOT_00", "A".repeat(800)), 8000, null, null);
        assertTrue(p.preflight(SPEC, r).ok());
        GpuProvider.CreateRequest big = new GpuProvider.CreateRequest("projectv-jarvislabs-preflight", "img", 80, List.of("python"),
                Map.of("SV_BOOT_00", "A".repeat(70_000)), 8000, null, null);
        assertFalse(p.preflight(SPEC, big).ok());
        gpus = "[]";
        assertFalse(provider(KEY).preflight(SPEC, r).ok());
        assertFalse(provider("").preflight(SPEC, r).ok());
    }

    @Test
    void inrPricesAreConvertedToUsdAndALowBalanceFailsThePreflight() {
        gpus = "[{\"gpu_type\":\"A100\",\"region\":\"IN2\",\"free\":2,\"price_per_hour\":84.24,\"workload_type\":\"container\",\"currency\":\"INR\"}]";
        JarvislabsProvider p = provider(KEY);
        assertEquals(84.24 / 94, p.quote(SPEC).hourlyUsd(), 1e-9);
        GpuProvider.CreateRequest r = new GpuProvider.CreateRequest("projectv-jarvislabs-preflight", "img", 80, List.of("python"),
                Map.of("SV_BOOT_00", "A"), 8000, null, null);
        assertTrue(p.preflight(SPEC, r).ok());
        assertTrue(calls.contains("balance"));
        balance = "{\"balance\":5.0,\"grants\":0.0,\"currency\":\"INR\"}";
        assertFalse(provider(KEY).preflight(SPEC, r).ok());
    }
}
