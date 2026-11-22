package com.projectv.gpu.broker;

import com.projectv.gpu.GpuPodLimits;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Unproven providers are never searched; the canary and the preflight against fake providers (nothing is rented for real). */
class ProviderGateTest {

    GpuBrokerTest.Fake runpod, jarvis;
    GpuBrokerTest.Leases leases;
    GpuBrokerTest.Records records;
    ProviderStates states;

    @BeforeEach
    void setUp() {
        runpod = new GpuBrokerTest.Fake("runpod");
        jarvis = new GpuBrokerTest.Fake("jarvislabs");
        runpod.hourly = 0.80;
        jarvis.hourly = 0.30; // cheaper, but unproven
        leases = new GpuBrokerTest.Leases();
        records = new GpuBrokerTest.Records();
        states = new ProviderStates.Memory("runpod");
    }

    GpuBroker broker() {
        BrokerConfig cfg = new BrokerConfig(List.of("runpod", "jarvislabs"), new HashMap<>(Map.of("runpod", 5.0, "jarvislabs", 5.0)), 0,
                List.of("NVIDIA GeForce RTX 4090"), 24, 48, 80, 1.0, 8000);
        GpuWallet wallet = new GpuWallet(cfg, p -> 0.0);
        GpuPodLimits limits = new GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, true);
        GpuBroker b = new GpuBroker(List.of(runpod, jarvis), cfg, wallet, records, leases, (p, pod, r, h, u) -> { },
                limits, Clock.systemUTC());
        b.setStates(states);
        return b;
    }

    GpuBroker.Rental acquire(GpuBroker b) {
        return b.acquire(UUID.randomUUID(), "video.i2v.dream", "img", List.of("python", "w.py"), Map.of(), null);
    }

    GpuBroker.Health ok = ep -> null;

    Map<String, Object> canary(GpuBroker b, GpuBroker.Health h) {
        return b.canary("jarvislabs", "img", List.of("python", "w.py"), Map.of(), null, h, 1, 1);
    }

    @Test
    void autoSkipsAnUnprovenProviderEvenWhenItIsCheaper() {
        GpuBroker b = broker();
        assertEquals("runpod", acquire(b).instance().provider());
        assertTrue(jarvis.created.isEmpty());
        assertTrue(b.options().stream().anyMatch(o -> o.provider().equals("jarvislabs") && !o.available()
                && o.why().contains("not proven")), "the options list says why");
    }

    @Test
    void autoStillFallsThroughToTheNextCheapestWhenACreateFails() {
        states.markProven("jarvislabs", "test");
        runpod.hourly = 0.80;
        jarvis.failCreate = true;
        GpuBroker b = broker();
        assertEquals("runpod", acquire(b).instance().provider());
        assertEquals(1, runpod.created.size());
    }

    @Test
    void canarySuccessTerminatesBooksAndMarksProven() {
        GpuBroker b = broker();
        Map<String, Object> r = canary(b, ok);
        assertEquals(true, r.get("proven"), r.toString());
        assertTrue(states.proven("jarvislabs"));
        assertEquals(List.of("jarvislabs-pod-1"), jarvis.terminated, "the canary pod is always ended");
        assertEquals(1, records.costs.size(), "and booked");
        assertTrue(leases.released.contains("jarvislabs-pod-1"));
        assertTrue(b.providerStatus("jarvislabs").get("proven").equals(true));
        assertEquals("jarvislabs", acquire(b).instance().provider(), "now cheapest and searched");
    }

    @Test
    void canaryWithAnUnhealthyWorkerStaysUnprovenButStillTerminates() {
        GpuBroker b = broker();
        Map<String, Object> r = canary(b, ep -> "HTTP 503");
        assertEquals(false, r.get("proven"));
        assertFalse(states.proven("jarvislabs"));
        assertEquals(1, jarvis.terminated.size());
        assertTrue(r.get("result").toString().contains("HTTP 503"));
    }

    @Test
    void canaryWhoseCreateFailsRentsNothingAndStaysUnproven() {
        jarvis.failCreate = true;
        Map<String, Object> r = canary(broker(), ok);
        assertEquals(false, r.get("proven"));
        assertTrue(jarvis.terminated.isEmpty());
        assertTrue(records.costs.isEmpty(), "nothing was rented, nothing is booked");
    }

    @Test
    void canaryAdoptsAPodWhoseCreateReplyWasLost() {
        jarvis.createThenFail = true;
        Map<String, Object> r = canary(broker(), ok);
        assertEquals(false, r.get("proven"));
        assertEquals(1, jarvis.terminated.size(), "the pod the provider did create is terminated");
    }

    @Test
    void failedPreflightMeansNoCanaryAndNothingRented() {
        jarvis.stock = false; // the default preflight needs a live quote
        Map<String, Object> r = canary(broker(), ok);
        assertEquals(false, r.get("proven"));
        assertTrue(jarvis.created.isEmpty());
        assertEquals(false, states.get("jarvislabs").preflightOk());
    }

    @Test
    void canaryIsRefusedWhileAPodIsOn() {
        GpuBroker b = broker();
        acquire(b);
        Map<String, Object> r = canary(b, ok);
        assertTrue(r.get("result").toString().startsWith("not run"));
        assertTrue(jarvis.created.isEmpty());
    }

    @Test
    void statusShowsConfiguredPreflightProven() {
        GpuBroker b = broker();
        b.preflight("jarvislabs", "img", List.of("python"), Map.of(), null);
        Map<String, Object> s = b.providerStatus("jarvislabs");
        assertEquals(true, s.get("configured"));
        assertEquals(true, s.get("preflightOk"));
        assertEquals(false, s.get("proven"));
        assertEquals(false, s.get("inAutoSearch"));
        assertEquals(true, b.providerStatus("runpod").get("inAutoSearch"));
    }
}
