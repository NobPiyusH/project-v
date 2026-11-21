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
import static org.junit.jupiter.api.Assertions.assertNotEquals;
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
        Map<String, String> lastEnv;
        State state = State.RUNNING;
        boolean createThenFail = false;
        boolean listFails = false;
        boolean listFailsAfterCreate = false;
        final List<Pod> pods = new ArrayList<>();

        Fake(String id) {
            this.id = id;
        }

        public String provider() { return id; }
        public boolean configured() { return configured; }
        public boolean enabledByDefault() { return true; }
        public List<Pod> listPods() {
            if (listFails) {
                throw new IllegalStateException("list down");
            }
            return pods;
        }
        public String endpointFor(String instanceId, int port) { return "https://" + instanceId + ":" + port; }
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
            lastEnv = req.env();
            String podId = id + "-pod-" + created.size();
            if (createThenFail) { // the provider made the pod, the reply timed out
                pods.add(new Pod(podId, req.name(), "RUNNING", hourly, NOW, NOW));
                listFails = listFailsAfterCreate;
                throw new IllegalStateException("read timed out");
            }
            return new Instance(id, podId, req.name(), hourly, "4090", "https://x");
        }
        public InstanceStatus status(String instanceId) { return new InstanceStatus(state, null, state.name()); }
    }

    static class Leases implements PodLeases {
        final Map<String, Lease> map = new HashMap<>();
        final List<String> released = new ArrayList<>();
        public void open(String provider, String podId, String name, String purpose) {
            map.put(podId, new Lease(podId, purpose, NOW, null));
        }
        final List<OpenLease> shorts = new ArrayList<>();
        public void open(String provider, String podId, String name, String purpose, UUID short_, double hourly, String gpu) {
            open(provider, podId, name, purpose);
            shorts.add(new OpenLease(podId, provider, name, short_, hourly, gpu, NOW));
        }
        public List<OpenLease> openForShorts() { return shorts.stream().filter(l -> !released.contains(l.podId())).toList(); }
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
        final Map<UUID, Double> hoursByShort = new java.util.LinkedHashMap<>();
        public void cost(UUID d, String provider, double hours, double hourlyUsd, String note) {
            costs.add(provider + "|" + hours + "|" + hourlyUsd);
            hoursByShort.merge(d, hours, Double::sum);
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

    @Test
    void workerTokenGoesIntoTheEnvAndTheAddressForTokenProviders() {
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        WorkerAuth auth = new WorkerAuth("test-secret".getBytes(), java.util.Set.of("runpod"));
        b.setAuth(auth);
        GpuBroker.Rental r = b.acquire(shortId, "video.i2v.dream", "img", List.of("python", "worker.py"), Map.of("SV_PORT", "8000"), null);
        String name = r.instance().name();
        assertEquals(auth.token(name), runpod.lastEnv.get("SV_TOKEN"), "the pod gets the token for its own name");
        assertEquals("8000", runpod.lastEnv.get("SV_PORT"), "the rest of the env is kept");
        assertEquals("https://worker:" + auth.token(name) + "@x", r.instance().endpoint(), "the Python side gets it inside the address");
        assertNotEquals(auth.token(name), auth.token(name + "x"), "one token per rental");
    }

    @Test
    void noTokenForProvidersThatDidNotAskForOne() {
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        b.setAuth(new WorkerAuth("test-secret".getBytes(), java.util.Set.of("vast")));
        GpuBroker.Rental r = b.acquire(shortId, "video.i2v.dream", "img", List.of("python", "worker.py"), Map.of("SV_PORT", "8000"), null);
        assertFalse(runpod.lastEnv.containsKey("SV_TOKEN"));
        assertEquals("https://x", r.instance().endpoint());
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

    @Test
    void gonePodIsReleasedAndRentedAgainInsteadOfReused() {
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        GpuBroker.Rental first = acquire(b);
        runpod.state = GpuProvider.State.GONE; // the pod self-stopped (idle guard) or was killed
        GpuBroker.Rental second = acquire(b);
        assertNotEquals(first.instance().id(), second.instance().id());
        assertEquals(2, runpod.created.size());
        assertEquals(List.of(first.instance().id()), leases.released, "the dead rental is closed and booked");
        assertEquals(1, records.costs.size());
    }

    @Test
    void podStateTellsTheWorkerOpenGoneOrReleased() {
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        assertEquals(GpuBroker.PodState.NONE, b.podState(shortId), "no rental yet");
        acquire(b);
        assertEquals(GpuBroker.PodState.OPEN, b.podState(shortId));
        runpod.state = GpuProvider.State.GONE; // deleted from outside while the clips step waits
        assertEquals(GpuBroker.PodState.GONE, b.podState(shortId));
        assertEquals(1, runpod.created.size(), "asking never rents");
        b.release(shortId, "test");
        assertEquals(GpuBroker.PodState.NONE, b.podState(shortId), "released by the studio: the worker must stop");
    }

    GpuBroker.Rental acquireFor(GpuBroker b, UUID short_) {
        return b.acquire(short_, "video.i2v.dream", "img", List.of("python", "worker.py"), Map.of(), null);
    }

    @Test
    void twoShortsShareOnePodAndItEndsOnlyWhenTheLastOneLetsGo() {
        Tick clock = new Tick();
        GpuBroker b = broker(clock);
        UUID a = UUID.randomUUID();
        UUID c = UUID.randomUUID();
        GpuBroker.Rental ra = acquireFor(b, a);
        GpuBroker.Rental rc = acquireFor(b, c);
        assertEquals(ra.instance().id(), rc.instance().id(), "the second Short uses the pod that is already on");
        assertEquals(1, runpod.created.size(), "one pod only");
        assertEquals(2, b.openShortIds().size());
        assertTrue(b.hasOpenPod());

        clock.now = NOW.plusSeconds(30 * 60);
        b.release(a, "A done");
        assertTrue(runpod.terminated.isEmpty(), "B still uses it");
        assertTrue(records.costs.isEmpty(), "nothing is booked until the pod ends");
        assertEquals(GpuBroker.PodState.NONE, b.podState(a));
        assertEquals(GpuBroker.PodState.OPEN, b.podState(c));
        assertTrue(b.hasOpenPod());

        clock.now = NOW.plusSeconds(60 * 60);
        b.release(c, "C done");
        assertEquals(List.of(ra.instance().id()), runpod.terminated);
        assertFalse(b.hasOpenPod());
        assertEquals(2, records.costs.size(), "one cost row per Short that used the pod");
        double ha = records.hoursByShort.get(a);
        double hc = records.hoursByShort.get(c);
        assertEquals(1.0, ha + hc, 1e-9, "the whole hour is booked, none lost, none doubled");
        assertEquals(1.0 / 3, ha, 1e-9, "A used the pod 30 of the 90 Short-minutes");
        assertEquals(2.0 / 3, hc, 1e-9);
    }

    @Test
    void turningTheSharedPodOffEndsItForEveryShort() {
        Tick clock = new Tick();
        GpuBroker b = broker(clock);
        UUID a = UUID.randomUUID();
        UUID c = UUID.randomUUID();
        GpuBroker.Rental r = acquireFor(b, a);
        acquireFor(b, c);
        clock.now = NOW.plusSeconds(10 * 60);
        b.releaseAll("turned off by the owner");
        assertEquals(List.of(r.instance().id()), runpod.terminated);
        assertEquals(GpuBroker.PodState.NONE, b.podState(a));
        assertEquals(GpuBroker.PodState.NONE, b.podState(c));
        assertEquals(2, records.costs.size());
        b.releaseAll("again"); // safe with no pod
        assertEquals(1, runpod.terminated.size());
    }

    @Test
    void whenTheSharedPodIsGoneEveryShortMovesToTheReplacement() {
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        UUID a = UUID.randomUUID();
        UUID c = UUID.randomUUID();
        GpuBroker.Rental first = acquireFor(b, a);
        acquireFor(b, c);
        runpod.state = GpuProvider.State.GONE; // deleted from outside
        GpuBroker.Rental second = acquireFor(b, a); // A's worker asks for a replacement
        assertNotEquals(first.instance().id(), second.instance().id());
        assertEquals(2, runpod.created.size());
        assertEquals(2, b.openShortIds().size(), "C did not lose its place: it uses the new pod too");
        assertTrue(b.open(c).isPresent());
        assertEquals(List.of(first.instance().id()), leases.released, "the dead pod is closed and booked");
        assertEquals(2, records.costs.size(), "its time is split between the two Shorts");
    }

    @Test
    void onlyOneSharedPodIsKeptAfterARestartAndExtraOnesAreEndedAndBooked() {
        UUID other = UUID.randomUUID();
        leases.shorts.add(new PodLeases.OpenLease("runpod-pod-1", "runpod", "projectv-runpod-a", shortId, 0.5, "4090", NOW));
        leases.shorts.add(new PodLeases.OpenLease("runpod-pod-2", "runpod", "projectv-runpod-b", other, 0.5, "4090", NOW));
        GpuBroker b = broker(Clock.fixed(NOW.plusSeconds(3600), ZoneOffset.UTC));
        b.recover();
        assertTrue(b.open(shortId).isPresent(), "the oldest pod is the shared one");
        assertFalse(b.open(other).isPresent());
        assertEquals(List.of("runpod-pod-2"), runpod.terminated, "the extra pod (from the one-pod-per-Short days) is ended");
        assertEquals(1.0, records.hoursByShort.get(other), 1e-9, "and booked to the Short that rented it");
    }

    @Test
    void unreadableStatusKeepsTheRental() {
        runpod = new Fake("runpod") {
            @Override
            public InstanceStatus status(String id) {
                throw new IllegalStateException("api blip");
            }
        };
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        GpuBroker.Rental first = acquire(b);
        assertEquals(first.instance().id(), acquire(b).instance().id());
        assertEquals(1, runpod.created.size());
    }

    @Test
    void rentalsComeBackAfterARestartSoReleaseStillTerminatesAndBooks() {
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        GpuBroker.Rental r = acquire(b);
        GpuBroker restarted = broker(Clock.fixed(NOW.plusSeconds(3600), ZoneOffset.UTC)); // same leases, empty memory
        assertFalse(restarted.open(shortId).isPresent());
        restarted.recover();
        assertEquals(r.instance().id(), restarted.open(shortId).orElseThrow().instance().id());
        assertEquals(r.instance().id(), acquire(restarted).instance().id(), "reused, not a second pod");
        assertEquals(1, runpod.created.size());
        restarted.release(shortId, "Short done");
        assertEquals(List.of(r.instance().id()), runpod.terminated);
        assertEquals(List.of("runpod|1.0|0.5"), records.costs, "one hour at the live rate, from the lease's creation time");
    }

    @Test
    void createTimeoutAdoptsThePodTheProviderMadeInsteadOfRentingAnother() {
        runpod.createThenFail = true;
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        GpuBroker.Rental r = acquire(b);
        assertEquals("runpod-pod-1", r.instance().id());
        assertTrue(jarvis.created.isEmpty(), "no second pod anywhere");
        assertEquals(1, runpod.created.size());
        assertNotNull(leases.map.get("runpod-pod-1"), "adopted pod is leased");
    }

    @Test
    void createFailureWithUnreadablePodListStopsInsteadOfRentingElsewhere() {
        runpod.createThenFail = true;
        runpod.listFailsAfterCreate = true;
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        assertThrows(GpuBroker.NoGpuAvailableException.class, () -> acquire(b));
        assertTrue(jarvis.created.isEmpty() && modal.created.isEmpty());
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
    void releaseOfAPodThatIsAlreadyGoneBooksOnlyUpToItsLastHeartbeat() {
        Tick clock = new Tick();
        GpuBroker b = broker(clock);
        GpuBroker.Rental r = acquire(b);
        runpod.state = GpuProvider.State.GONE; // deleted from outside 10 min in; the step only failed at 30 min
        leases.map.put(r.instance().id(), new PodLeases.Lease(r.instance().id(), "p", NOW.plusSeconds(600), null));
        clock.now = NOW.plusSeconds(1800);
        b.release(shortId, "step failed");
        assertEquals(1, records.costs.size());
        double hours = Double.parseDouble(records.costs.getFirst().split("[|]")[1]);
        assertEquals(600 / 3600.0, hours, 1e-9);
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

    /** A provider offering several GPUs, like RunPod: 4090 cheap, A6000 mid, 5090 dear. */
    static class Multi extends Fake {
        Multi(String id) {
            super(id);
        }

        @Override
        public List<Quote> quotes(GpuSpec spec) {
            return List.of(new Quote(id, true, "HIGH", "4090|COMMUNITY", 0.34, ""), new Quote(id, true, "LOW", "A6000|SECURE", 0.49, ""),
                    new Quote(id, true, "HIGH", "5090|SECURE", 0.89, ""));
        }

        @Override
        public Quote quote(GpuSpec spec) {
            return quotes(spec).getFirst();
        }
    }

    @Test
    void optionsAreSortedByPriceAvailableFirstWithReasonsForTheRest() {
        runpod = new Multi("runpod");
        jarvis.configured = false;
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        List<GpuBroker.Option> o = b.options();
        assertEquals(List.of("runpod|4090|COMMUNITY", "runpod|A6000|SECURE", "runpod|5090|SECURE"),
                o.stream().filter(GpuBroker.Option::available).map(GpuBroker.Option::key).toList());
        GpuBroker.Option blocked = o.stream().filter(x -> x.provider().equals("jarvislabs")).findFirst().orElseThrow();
        assertFalse(blocked.available());
        assertTrue(blocked.why().contains("not configured"));
        assertTrue(o.getLast().hourlyUsd() == 0 || !o.getLast().available() || o.getLast().hourlyUsd() >= o.getFirst().hourlyUsd());
    }

    @Test
    void manualPickRentsExactlyThatOptionEvenIfDearer() {
        runpod = new Multi("runpod");
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        GpuBroker.Rental r = b.acquire(shortId, "video.i2v.dream", "img", List.of("w"), Map.of(), null,
                new GpuBroker.Pick("manual", "runpod|5090|SECURE"));
        assertEquals("runpod", r.instance().provider());
        assertTrue(records.decisions.getFirst().contains("your manual pick"));
    }

    @Test
    void manualPickThatIsGoneFailsToTheOwnerWithoutFallingBack() {
        GpuBroker b = broker(Clock.fixed(NOW, ZoneOffset.UTC));
        GpuBroker.NoGpuAvailableException e = assertThrows(GpuBroker.NoGpuAvailableException.class,
                () -> b.acquire(shortId, "video.i2v.dream", "img", List.of("w"), Map.of(), null, new GpuBroker.Pick("manual", "runpod|nope|X")));
        assertTrue(e.getMessage().contains("your pick"));
        assertTrue(runpod.created.isEmpty() && jarvis.created.isEmpty(), "no silent fallback after a manual pick");
        assertThrows(GpuBroker.NoGpuAvailableException.class,
                () -> b.acquire(UUID.randomUUID(), "video.i2v.dream", "img", List.of("w"), Map.of(), null, new GpuBroker.Pick("manual", null)));
    }

    @Test
    void autoFollowsALearnedPreferenceWithinThePremiumLimit() {
        runpod = new Multi("runpod");
        PreferenceStore learned = new PreferenceStore() {
            public java.util.Optional<String> learnedKey() { return java.util.Optional.of("runpod|A6000|SECURE"); }
            public void recordChoice(UUID s, String c, String ch, Map<String, Object> ctx) { }
        };
        GpuBroker b = new GpuBroker(List.of(runpod, jarvis, modal), new BrokerConfig(List.of("runpod", "jarvislabs", "modal"), caps, 0,
                List.of("x"), 24, 48, 80, 1.0, 8000), new GpuWallet(new BrokerConfig(List.of("runpod"), caps, 0, List.of("x"), 24, 48, 80, 1, 8000),
                p -> 0.0), records, leases, (p, pod, r, h, u) -> { }, new GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, true),
                Clock.fixed(NOW, ZoneOffset.UTC), learned);
        b.acquire(shortId, "video.i2v.dream", "img", List.of("w"), Map.of(), null);
        assertTrue(records.decisions.getFirst().contains("learned from your last picks"));
        assertEquals("runpod|A6000|SECURE", records.consideredLog.getFirst().stream()
                .filter(r -> Boolean.TRUE.equals(r.get("chosen"))).map(r -> r.get("provider") + "|" + r.get("gpu")).findFirst().orElseThrow());
        // 5090 at $0.89 is 2.6x the cheapest ($0.34): over the 1.5x premium limit, so it would not be followed
        PreferenceStore dear = new PreferenceStore() {
            public java.util.Optional<String> learnedKey() { return java.util.Optional.of("runpod|5090|SECURE"); }
            public void recordChoice(UUID s, String c, String ch, Map<String, Object> ctx) { }
        };
        runpod.created.clear();
        records.decisions.clear();
        GpuBroker b2 = new GpuBroker(List.of(runpod, jarvis, modal), new BrokerConfig(List.of("runpod"), caps, 0, List.of("x"), 24, 48, 80, 1.0, 8000),
                new GpuWallet(new BrokerConfig(List.of("runpod"), caps, 0, List.of("x"), 24, 48, 80, 1, 8000), p -> 0.0), records, leases,
                (p, pod, r, h, u) -> { }, new GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, true), Clock.fixed(NOW, ZoneOffset.UTC), dear);
        b2.acquire(UUID.randomUUID(), "video.i2v.dream", "img", List.of("w"), Map.of(), null);
        assertTrue(records.decisions.getFirst().contains("cheapest eligible"));
    }

    // ------------------------------------------------------------------ daily cap and never-ready pods (21 Nov 2026)

    GpuBroker dailyBroker(Clock clock, double spentTodayUsd) {
        BrokerConfig cfg = new BrokerConfig(List.of("runpod", "jarvislabs", "modal"), caps, 0,
                List.of("NVIDIA GeForce RTX 4090"), 24, 48, 80, 1.0, 8000, 3.50);
        GpuWallet wallet = new GpuWallet(cfg, p -> spent.getOrDefault(p, 0.0), () -> spentTodayUsd);
        return new GpuBroker(List.of(runpod, jarvis, modal), cfg, wallet, records, leases, (p, pod, r, h, u) -> { },
                new GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, false), clock);
    }

    @Test
    void dailyCapRefusesARentalPastItWithAClearMessage() {
        GpuBroker b = dailyBroker(Clock.fixed(NOW, ZoneOffset.UTC), 3.20); // $0.30 left, a rental needs about $0.50
        GpuBroker.NoGpuAvailableException e = assertThrows(GpuBroker.NoGpuAvailableException.class, () -> acquire(b));
        assertTrue(e.getMessage().startsWith("Daily GPU cap reached"), e.getMessage());
        assertTrue(e.getMessage().contains("$3.20 spent today (UTC) of the $3.50 cap"), e.getMessage());
        assertTrue(e.getMessage().contains("waits for you"), e.getMessage());
        assertTrue(runpod.created.isEmpty() && jarvis.created.isEmpty(), "nothing rented");
        assertEquals(1, records.decisions.size(), "the refusal is a routing decision too (rule 6)");
    }

    @Test
    void underTheDailyCapItRentsAndTodayCountsTheRunningPod() {
        java.util.concurrent.atomic.AtomicReference<Instant> now = new java.util.concurrent.atomic.AtomicReference<>(NOW);
        Clock clock = new Clock() {
            public java.time.ZoneId getZone() { return ZoneOffset.UTC; }
            public Clock withZone(java.time.ZoneId z) { return this; }
            public Instant instant() { return now.get(); }
        };
        GpuBroker b = dailyBroker(clock, 1.00);
        acquire(b);
        now.set(NOW.plusSeconds(3600)); // one hour at $0.50/h
        Map<String, Object> t = b.today();
        assertEquals(1.50, (double) t.get("spentUsd"), 1e-9);
        assertEquals(0.50, (double) t.get("runningUsd"), 1e-9);
        assertEquals(3.50, (double) t.get("capUsd"), 1e-9);
    }

    @Test
    void aPodThatNeverBecameReadyIsRetiredBookedAndReplaced() {
        GpuBroker b = dailyBroker(Clock.fixed(NOW, ZoneOffset.UTC), 0);
        UUID other = UUID.randomUUID();
        GpuBroker.Rental first = acquire(b);
        b.acquire(other, "video.i2v.dream", "img", List.of("python", "worker.py"), Map.of(), null);
        assertFalse(b.retireUnready("https://somewhere-else", "late"), "only the pod the worker waited on");
        assertTrue(b.retireUnready("https://x/", "not ready after 30 min"));
        assertEquals(List.of(first.instance().id()), runpod.terminated, "terminated");
        assertEquals(List.of(first.instance().id()), leases.released);
        assertFalse(records.costs.isEmpty(), "its time is booked");
        GpuBroker.Rental second = acquire(b);
        assertNotEquals(first.instance().id(), second.instance().id());
        assertTrue(b.open(other).isPresent(), "the other Short moved to the new pod");
    }
}
