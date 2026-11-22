package com.projectv.gpu.broker;

import com.projectv.gpu.GpuPodLimits;
import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** The canary moves to the next cheapest plan only when a plan is out of stock; fakes only, nothing is rented. */
class CanaryFallbackTest {

    /** A provider with three plans (cheapest first by price, not by list order). Plans in {@code outOfStock} refuse with the typed signal. */
    static class Multi extends GpuBrokerTest.Fake {
        final List<String> outOfStock = new java.util.ArrayList<>();
        RuntimeException otherFailure;

        Multi() {
            super("jarvislabs");
        }

        GpuProvider.Quote plan(String name, double usd) {
            return new GpuProvider.Quote(id, true, "HIGH", name, usd, "");
        }

        @Override
        public List<Quote> quotes(GpuSpec spec) {
            return List.of(plan("L40S", 1.2), plan("A40", 0.5), plan("A100", 2.0), plan("H100", 4.0));
        }

        @Override
        public Instance create(CreateRequest req) {
            tried.add(req.quote().gpu());
            if (otherFailure != null) {
                throw otherFailure;
            }
            if (outOfStock.contains(req.quote().gpu())) {
                throw new PlanUnavailableException("This GPU plan is temporarily not available");
            }
            return super.create(req);
        }

        final List<String> tried = new java.util.ArrayList<>();
    }

    final Multi jarvis = new Multi();
    final GpuBrokerTest.Records records = new GpuBrokerTest.Records();
    final ProviderStates states = new ProviderStates.Memory("runpod");

    Map<String, Object> run(double maxUsd) {
        BrokerConfig cfg = new BrokerConfig(List.of("jarvislabs"), new HashMap<>(Map.of("jarvislabs", 5.0)), 0,
                List.of("NVIDIA GeForce RTX 4090"), 24, 48, 80, 1.0, 8000);
        GpuBroker b = new GpuBroker(List.of(jarvis), cfg, new GpuWallet(cfg, p -> 0.0), records, new GpuBrokerTest.Leases(),
                (p, pod, r, h, u) -> { }, new GpuPodLimits("projectv-", 120, 15, 10, 2, 2.0, true), Clock.systemUTC());
        b.setStates(states);
        return b.canary("jarvislabs", "img", List.of("python", "w.py"), Map.of(), null, ep -> null, 1, 1, maxUsd);
    }

    @Test
    void firstPlanUnavailableThenTheNextCheapestSucceeds() {
        jarvis.outOfStock.add("A40");
        Map<String, Object> r = run(0.60);
        assertEquals(true, r.get("proven"), r.toString());
        assertEquals(List.of("A40", "L40S"), jarvis.tried, "price order, stops at the first success");
        assertEquals(2, ((List<?>) r.get("attempts")).size());
        assertEquals(1, jarvis.terminated.size());
    }

    @Test
    void allThreeUnavailableStopsAtThreeAndRentsNothing() {
        jarvis.outOfStock.addAll(List.of("A40", "L40S", "A100", "H100"));
        Map<String, Object> r = run(5.0);
        assertEquals(false, r.get("proven"));
        assertEquals(List.of("A40", "L40S", "A100"), jarvis.tried, "at most 3 attempts");
        assertEquals(3, ((List<?>) r.get("attempts")).size());
        assertTrue(jarvis.terminated.isEmpty());
        assertTrue(records.costs.isEmpty());
        assertTrue(r.get("result").toString().contains("unavailable"));
    }

    @Test
    void aCreditErrorDoesNotFallThrough() {
        jarvis.otherFailure = new IllegalStateException("E2E refused the call (HTTP 402): insufficient credit");
        Map<String, Object> r = run(5.0);
        assertEquals(false, r.get("proven"));
        assertEquals(List.of("A40"), jarvis.tried, "no second plan after a non-availability failure");
        assertFalse(states.proven("jarvislabs"));
    }

    @Test
    void thePerCanaryCostCapRefusesAnExpensivePlan() {
        Map<String, Object> r = run(0.01); // even the cheapest plan costs more than a cent for the run
        assertEquals(false, r.get("proven"));
        assertTrue(jarvis.tried.isEmpty(), "nothing was even attempted");
        assertTrue(r.get("result").toString().contains("over the approved cap"), r.toString());
    }

    @Test
    void aFallbackPlanOverTheCapIsNotRented() {
        jarvis.outOfStock.add("A40");
        // A40 $0.5/h and L40S $1.2/h for a 121 s run: cap between the two costs
        Map<String, Object> r = run(0.03);
        assertEquals(false, r.get("proven"));
        assertEquals(List.of("A40"), jarvis.tried, "L40S is over the cap, so it is never created");
    }
}
