package com.projectv.gpu.broker;

import com.projectv.gpu.GpuPodLimits;
import com.projectv.gpu.PodEvents;
import com.projectv.gpu.PodLeases;
import com.projectv.gpu.PodSelfStop;
import com.projectv.gpu.PodWatchdog;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Provider-neutral GPU broker (docs/plans/m3-gpu-router.md). {@link #acquire} walks the configured provider order and
 * rents one GPU for a Dream Short from the cheapest (live price) provider that is configured, inside its wallet cap, in stock at a
 * live price within the watchdog limits, and that actually creates. Every attempt writes a RoutingDecision; every
 * rental ends in {@link #release}, which terminates and books a CostEntry. The boot command is always wrapped with
 * {@link PodSelfStop} and a lease is opened, so the watchdog covers whatever this class fails to clean up (rule 8).
 * Not wired to Dream Shorts yet: that is behind {@code projectv.dream.gpu-provider} (default {@code modal}).
 */
@Component
public class GpuBroker {

    private static final Logger log = LoggerFactory.getLogger(GpuBroker.class);

    /** An open rental for one Short. */
    public record Rental(UUID shortId, GpuProvider.Instance instance, Instant createdAt) {
    }

    private final Map<String, GpuProvider> providers = new LinkedHashMap<>();
    private final BrokerConfig config;
    private final GpuWallet wallet;
    private final BrokerRecords records;
    private final PodLeases leases;
    private final PodEvents events;
    private final GpuPodLimits limits;
    private final Clock clock;
    private final Map<UUID, Rental> open = new ConcurrentHashMap<>();

    @Autowired
    public GpuBroker(List<GpuProvider> providers, BrokerConfig config, GpuWallet wallet, BrokerRecords records,
                     PodLeases leases, PodEvents events, GpuPodLimits.FromConfig limits) {
        this(providers, config, wallet, records, leases, events, limits.limits, Clock.systemUTC());
    }

    public GpuBroker(List<GpuProvider> providers, BrokerConfig config, GpuWallet wallet, BrokerRecords records,
                     PodLeases leases, PodEvents events, GpuPodLimits limits, Clock clock) {
        providers.forEach(p -> this.providers.put(p.provider(), p));
        this.config = config;
        this.wallet = wallet;
        this.records = records;
        this.leases = leases;
        this.events = events;
        this.limits = limits;
        this.clock = clock;
    }

    public Optional<Rental> open(UUID shortId) {
        return Optional.ofNullable(open.get(shortId));
    }

    /**
     * Rents (or returns the open) GPU for this Short.
     *
     * @param workerArgv the pod's job command (the Python worker); wrapped here with the self-stop guard
     * @throws NoGpuAvailableException when no provider in the chain can serve it (the step then waits for the owner)
     */
    public synchronized Rental acquire(UUID shortId, String capability, String image, List<String> workerArgv,
                                       Map<String, String> env, String volumeId) {
        Rental existing = open.get(shortId);
        if (existing != null) {
            leases.heartbeat(existing.instance().id());
            return existing;
        }
        GpuProvider.GpuSpec spec = new GpuProvider.GpuSpec(config.gpuPreference(), config.minVramGb(), config.minRamGb(), config.diskGb());
        List<Map<String, Object>> considered = new ArrayList<>();
        // Phase 1 (read-only): quote every provider, drop the ones that cannot serve. Phase 2: cheapest live price first
        // (owner, 20 Nov 2026: "lesser price the better, any provider"); config order only breaks ties.
        record Eligible(String id, GpuProvider p, GpuProvider.Quote q, Map<String, Object> row) {
        }
        List<Eligible> eligible = new ArrayList<>();
        for (String id : config.order()) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("provider", id);
            considered.add(row);
            GpuProvider p = providers.get(id);
            if (p == null) {
                row.put("skipped", "no adapter");
                continue;
            }
            if (!p.configured()) {
                row.put("skipped", "not configured (no key in .env, or adapter not built)");
                continue;
            }
            GpuProvider.Quote q;
            try {
                q = p.quote(spec);
            } catch (RuntimeException e) {
                row.put("skipped", "quote failed: " + e.getMessage());
                continue;
            }
            row.put("stock", q.stock());
            row.put("gpu", q.gpu());
            row.put("hourlyUsd", q.hourlyUsd());
            if (!q.available()) {
                row.put("skipped", "no stock: " + q.detail());
                continue;
            }
            String refusal = wallet.refusal(id, q.hourlyUsd() * config.expectedHours());
            if (refusal != null) {
                row.put("skipped", refusal);
                continue;
            }
            try {
                new PodWatchdog(p, leases, events, limits, clock).checkCanStart(q.hourlyUsd());
            } catch (RuntimeException e) {
                row.put("skipped", e.getMessage());
                continue;
            }
            eligible.add(new Eligible(id, p, q, row));
        }
        eligible.sort(java.util.Comparator.comparingDouble(e -> e.q().hourlyUsd())); // stable: ties keep config order
        for (Eligible e : eligible) {
            String id = e.id();
            GpuProvider p = e.p();
            GpuProvider.Quote q = e.q();
            Map<String, Object> row = e.row();
            Instant now = clock.instant();
            String name = limits.prefix() + id + "-" + shortId.toString().substring(0, 8) + "-"
                    + UUID.randomUUID().toString().substring(0, 4);
            List<String> argv = PodSelfStop.wrap(workerArgv, limits.maxRuntimeMinutes(), limits.idleMinutes(), now);
            GpuProvider.Instance inst;
            try {
                inst = p.create(new GpuProvider.CreateRequest(name, image, config.diskGb(), argv, env, config.port(), volumeId, q));
            } catch (RuntimeException ex) {
                row.put("skipped", "create failed: " + ex.getMessage());
                log.warn("GPU broker: {} create failed for Short {}: {}", id, shortId, ex.getMessage());
                continue;
            }
            try {
                leases.open(id, inst.id(), inst.name(), "Dream Short " + shortId);
            } catch (RuntimeException ex) { // never keep a pod we cannot lease
                safeTerminate(p, inst.id());
                row.put("skipped", "lease failed, pod terminated: " + ex.getMessage());
                continue;
            }
            row.put("chosen", true);
            String reason = id + ": cheapest eligible live price, $%.2f/h (%d eligible)".formatted(inst.hourlyUsd(), eligible.size());
            records.decision(shortId, capability, considered, id, reason);
            Rental r = new Rental(shortId, inst, now);
            open.put(shortId, r);
            log.info("GPU broker: rented {} {} ({}, ${}/h live) for Short {}", id, inst.id(), inst.gpu(), inst.hourlyUsd(), shortId);
            return r;
        }
        String reason = "No GPU provider can run " + capability + " now: " + considered.stream()
                .map(r -> r.get("provider") + " (" + r.get("skipped") + ")").toList();
        records.decision(shortId, capability, considered, null, reason);
        throw new NoGpuAvailableException(reason);
    }

    /** Cheapest live quote that could be rented right now (configured, in stock, inside its wallet); read-only. For estimates. */
    public Optional<GpuProvider.Quote> cheapestQuote() {
        GpuProvider.GpuSpec spec = new GpuProvider.GpuSpec(config.gpuPreference(), config.minVramGb(), config.minRamGb(), config.diskGb());
        GpuProvider.Quote best = null;
        for (String id : config.order()) {
            GpuProvider p = providers.get(id);
            if (p == null || !p.configured()) {
                continue;
            }
            try {
                GpuProvider.Quote q = p.quote(spec);
                if (q.available() && wallet.refusal(id, q.hourlyUsd() * config.expectedHours()) == null
                        && (best == null || q.hourlyUsd() < best.hourlyUsd())) {
                    best = q;
                }
            } catch (RuntimeException e) {
                log.warn("GPU broker: quote from {} failed: {}", id, e.getMessage());
            }
        }
        return Optional.ofNullable(best);
    }

    /** Keeps the lease warm without any provider call (the worker pings this while it waits or runs jobs). */
    public void heartbeat(UUID shortId) {
        Rental r = open.get(shortId);
        if (r != null) {
            leases.heartbeat(r.instance().id());
        }
    }

    /** Readiness of the Short's pod (the worker's own /health is the final truth); also keeps the lease warm. */
    public GpuProvider.InstanceStatus status(UUID shortId) {
        Rental r = open.get(shortId);
        if (r == null) {
            return new GpuProvider.InstanceStatus(GpuProvider.State.GONE, null, "no open rental");
        }
        leases.heartbeat(r.instance().id());
        return providers.get(r.instance().provider()).status(r.instance().id());
    }

    /** Ends the rental: terminate, book the time, release the lease. Safe to call twice. */
    public synchronized void release(UUID shortId, String why) {
        Rental r = open.remove(shortId);
        if (r == null) {
            return;
        }
        GpuProvider p = providers.get(r.instance().provider());
        boolean gone = safeTerminate(p, r.instance().id());
        double hours = Math.max(0, Duration.between(r.createdAt(), clock.instant()).toSeconds() / 3600.0);
        try {
            records.cost(shortId, r.instance().provider(), hours, r.instance().hourlyUsd(),
                    "GPU rental %s (%s) ended: %s".formatted(r.instance().id(), r.instance().gpu(), why));
            leases.release(r.instance().id());
        } catch (RuntimeException e) {
            log.warn("GPU broker: rental {} ended but its ledger row failed: {}", r.instance().id(), e.getMessage());
        }
        if (!gone) {
            log.warn("GPU broker: could not terminate {}; lease released, the watchdog will", r.instance().id());
        }
    }

    private boolean safeTerminate(GpuProvider p, String id) {
        try {
            p.terminate(id);
            return true;
        } catch (RuntimeException e) {
            log.warn("GPU broker: terminate {} failed: {}", id, e.getMessage());
            return false;
        }
    }

    /** Nothing in the chain can serve the job; non-retryable, the step waits for the owner (rule 2b). */
    public static class NoGpuAvailableException extends RuntimeException {
        public NoGpuAvailableException(String message) {
            super(message);
        }
    }
}
