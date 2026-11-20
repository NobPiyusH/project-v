package com.projectv.gpu.broker;

import com.projectv.gpu.GpuPodLimits;
import com.projectv.gpu.PodLeases;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** The broker against fake providers, wallet and ledger: no network, no database, nothing is ever rented. */
class GpuBrokerTest {

    static final Instant NOW = Instant.parse("2026-11-20T12:00:00Z");

    static class Fake implements GpuProvider {
        final String id;
        boolean configured = true;
        boolean stock = true;
        boolean failCreate = false;
        double hourly = 0.5;
        final List<String> created = new ArrayList<>();
        final List<String> terminated = new ArrayList<>();
        List<String> lastArgv;

        Fake(String id) {
            this.id = id;
        }

        public String provider() { return id; }
        public boolean configured() { return configured; }
        public boolean enabledByDefault() { return true; }
        public List<Pod> listPods() { return List.of(); }
        public void terminate(String podId) { terminated.add(podId); }
        public Quote quote(GpuSpec spec) {
            return stock ? new Quote(id, true, "HIGH", "NVIDIA GeForce RTX 4090|SECURE", hourly, "")
                    : Quote.unavailable(id, "no instances");
        }
        public Instance create(CreateRequest req) {
            if (failCreate) {
                throw new IllegalStateException("boom");
            }
            created.add(req.name());
            lastArgv = req.bootArgv();
            return new Instance(id, id + "-pod-" + created.size(), req.name(), hourly, "4090", "https://x");
        }
        public InstanceStatus status(String instanceId) { return new InstanceStatus(State.RUNNING, null, "RUNNING"); }
    }

    static class Leases implements PodLeases {
        final Map<String, Lease> map = new HashMap<>();
        final List<String> released = new ArrayList<>();
        public void open(String provider, String podId, String name, String purpose) {
            map.put(podId, new Lease(podId, purpose, NOW, null));
        }
        public void heartbeat(String podId) { }
        public void release(String podId) { released.add(podId); }
        public Optional<Lease> find(String podId) { return Optional.ofNullable(map.get(podId)); }
    }

    static class Records implements BrokerRecords {
        final List<String> decisions = new ArrayList<>();
        final List<String> costs = new ArrayList<>();
        final List<List<Map<String, Object>>> consideredLog = new ArrayList<>();
        public void decision(UUID d, String cap, List<Map<String, Object>> considered, String chosen, String reason) {
            decisions.add(chosen + "|" + reason);
            consideredLog.add(considered);
        }
        public void cost(UUID d, String provider, double hours, double hourlyUsd, String note) {
            costs.add(provider + "|" + hours + "|" + hourlyUsd);
        }
    }

    Fake runpod, jarvis, modal;
    Leases leases;
    Records records;
    Map<String, Double> spent;
    Map<String, Double> caps;
    UUID shortId = UUID.randomUUID();

    GpuBroker broker(Clock clock) {
        BrokerConfig cfg = new BrokerConfig(List.of("runpod", "jarvislabs", "modal"), caps, 0,
                List.of("NVIDIA GeForce RTX 4090"), 24, 48, 80, 1.0, 8000);
        GpuWallet wallet = new GpuWallet(cfg, p -> spent.getOrDefault(p, 0.0));
        GpuPodLimits limits = new GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, true);
        return new GpuBroker(List.of(runpod, jarvis, modal), cfg, wallet, records, leases, (p, pod, r, h, u) -> { },
                limits, clock);
    }

    @BeforeEach
    void setUp() {
        runpod = new Fake("runpod");
        jarvis = new Fake("jarvislabs");
        modal = new Fake("modal");
        leases = new Leases();
        records = new Records();
        spent = new HashMap<>();
        caps = new HashMap<>(Map.of("runpod", 5.0, "jarvislabs", 5.0, "modal", 0.0));
    }

    GpuBroker.Rental acquire(GpuBroker b) {
        return b.acquire(shortId, "video.i2v.dream", "img", List.of("python", "worker.py"), Map.of(), null);
    }

    @Test
    void cheapestLivePriceWinsOverConfigOrder() {
        runpod.hourly = 0.89;
        jarvis.hourly = 0.45;
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        assertEquals("jarvislabs", acquire(b).instance().provider());
        assertTrue(runpod.created.isEmpty());
    }

    @Test
    void cheapestFailsCreateThenNextCheapest() {
        runpod.hourly = 0.89;
        jarvis.hourly = 0.45;
        jarvis.failCreate = true;
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        assertEquals("runpod", acquire(b).instance().provider());
    }

    @Test
    void equalPriceKeepsConfigOrderWithLeaseDecisionAndSelfStop() {
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        GpuBroker.Rental r = acquire(b);
        assertEquals("runpod", r.instance().provider());
        assertTrue(runpod.created.getFirst().startsWith("projectv-runpod-"), "studio prefix so the watchdog sees it");
        assertEquals("bash", runpod.lastArgv.get(0)); // wrapped by PodSelfStop
        assertTrue(runpod.lastArgv.contains("worker.py"));
        assertNotNull(leases.map.get(r.instance().id()), "lease opened right after create");
        assertEquals(1, records.decisions.size());
        assertTrue(records.decisions.getFirst().startsWith("runpod|"));
        assertTrue(jarvis.created.isEmpty());
    }

    @Test
    void fallsThroughNoStockUnconfiguredAndFailedCreate() {
        runpod.stock = false;
        jarvis.failCreate = true;
        modal.configured = true;
        caps.put("modal", 5.0);
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        GpuBroker.Rental r = acquire(b);
        assertEquals("modal", r.instance().provider());
        List<Map<String, Object>> c = records.consideredLog.getFirst();
        assertTrue(String.valueOf(c.get(0).get("skipped")).contains("no stock"));
        assertTrue(String.valueOf(c.get(1).get("skipped")).contains("create failed"));
        assertEquals(Boolean.TRUE, c.get(2).get("chosen"));
    }

    @Test
    void walletCapBlocksAndZeroCapMeansBlocked() {
        spent.put("runpod", 4.9);            // $0.10 left, rental needs $0.50
        caps.put("jarvislabs", 0.0);         // blocked
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        GpuBroker.NoGpuAvailableException e = assertThrows(GpuBroker.NoGpuAvailableException.class, () -> acquire(b));
        assertTrue(e.getMessage().contains("wallet"));
        assertTrue(e.getMessage().contains("blocked"));
        assertTrue(runpod.created.isEmpty() && jarvis.created.isEmpty() && modal.created.isEmpty(), "nothing rented");
        assertEquals(1, records.decisions.size());
        assertTrue(records.decisions.getFirst().startsWith("null|"), "the failed attempt is still a RoutingDecision");
    }

    @Test
    void secondAcquireReusesTheOpenRental() {
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        GpuBroker.Rental a = acquire(b);
        GpuBroker.Rental again = acquire(b);
        assertEquals(a.instance().id(), again.instance().id());
        assertEquals(1, runpod.created.size());
    }

    static class Tick extends Clock {
        Instant now = NOW;
        public java.time.ZoneId getZone() { return ZoneOffset.UTC; }
        public Clock withZone(java.time.ZoneId z) { return this; }
        public Instant instant() { return now; }
    }

    @Test
    void releaseTerminatesBooksCostAndReleasesLease() {
        Tick clock = new Tick();
        GpuBroker b = broker(clock);
        GpuBroker.Rental r = acquire(b);
        clock.now = NOW.plusSeconds(1800);
        b.release(shortId, "Short done");
        assertEquals(List.of(r.instance().id()), runpod.terminated);
        assertEquals(List.of(r.instance().id()), leases.released);
        assertEquals(List.of("runpod|0.5|0.5"), records.costs);
        assertFalse(b.open(shortId).isPresent());
        b.release(shortId, "twice is harmless");
        assertEquals(1, runpod.terminated.size());
    }

    @Test
    void terminateFailureStillReleasesLeaseSoWatchdogFinishesTheJob() {
        runpod = new Fake("runpod") {
            @Override
            public void terminate(String podId) {
                throw new IllegalStateException("api down");
            }
        };
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        GpuBroker.Rental r = acquire(b);
        b.release(shortId, "cancelled");
        assertEquals(List.of(r.instance().id()), leases.released);
        assertEquals(1, records.costs.size());
    }
}
