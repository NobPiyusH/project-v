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
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import jakarta.annotation.PostConstruct;

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
    private final PreferenceStore prefs;
    private final Map<UUID, Rental> open = new ConcurrentHashMap<>();

    @Autowired
    public GpuBroker(List<GpuProvider> providers, BrokerConfig config, GpuWallet wallet, BrokerRecords records,
                     PodLeases leases, PodEvents events, GpuPodLimits.FromConfig limits, PreferenceStore prefs) {
        this(providers, config, wallet, records, leases, events, limits.limits, Clock.systemUTC(), prefs);
    }

    public GpuBroker(List<GpuProvider> providers, BrokerConfig config, GpuWallet wallet, BrokerRecords records,
                     PodLeases leases, PodEvents events, GpuPodLimits limits, Clock clock) {
        this(providers, config, wallet, records, leases, events, limits, clock, PreferenceStore.NONE);
    }

    public GpuBroker(List<GpuProvider> providers, BrokerConfig config, GpuWallet wallet, BrokerRecords records,
                     PodLeases leases, PodEvents events, GpuPodLimits limits, Clock clock, PreferenceStore prefs) {
        this.prefs = prefs;
        providers.forEach(p -> this.providers.put(p.provider(), p));
        this.config = config;
        this.wallet = wallet;
        this.records = records;
        this.leases = leases;
        this.events = events;
        this.limits = limits;
        this.clock = clock;
    }

    /**
     * Rebuilds the open rentals from the unreleased leases after a control-plane restart, so release() still terminates
     * the pod and books its cost, the wallet counts it, and the next acquire reuses it instead of renting a second pod.
     */
    @PostConstruct
    public synchronized void recover() {
        List<PodLeases.OpenLease> held;
        try {
            held = leases.openForShorts();
        } catch (RuntimeException e) {
            log.warn("GPU broker: could not read open leases at startup ({}); the watchdog still covers every studio pod", e.getMessage());
            return;
        }
        for (PodLeases.OpenLease l : held) {
            GpuProvider p = providers.get(l.provider());
            if (p == null) {
                log.warn("GPU broker: open lease {} belongs to unknown provider {}; left to the watchdog", l.podId(), l.provider());
                continue;
            }
            GpuProvider.Instance inst = new GpuProvider.Instance(l.provider(), l.podId(), l.name(), l.hourlyUsd(), l.gpu(),
                    p.endpointFor(l.podId(), config.port()));
            open.put(l.dreamShortId(), new Rental(l.dreamShortId(), inst, l.createdAt()));
            try {
                leases.heartbeat(l.podId());
            } catch (RuntimeException e) {
                log.warn("GPU broker: heartbeat of recovered lease {} failed: {}", l.podId(), e.getMessage());
            }
            log.info("GPU broker: recovered open rental {} {} for Short {} after restart", l.provider(), l.podId(), l.dreamShortId());
        }
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
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public synchronized Rental acquire(UUID shortId, String capability, String image, List<String> workerArgv,
                                       Map<String, String> env, String volumeId) {
        return acquire(shortId, capability, image, workerArgv, env, volumeId, Pick.AUTO);
    }

    /** How the owner wants the GPU chosen for a Short: Auto (the studio picks) or Manual (the owner's "provider|gpu" key). */
    public record Pick(String mode, String choice) {
        public static final Pick AUTO = new Pick("auto", null);

        public boolean manual() {
            return "manual".equalsIgnoreCase(mode);
        }
    }

    /** One rentable (or not) option, for the owner's list. {@code key} is what a manual pick sends back. */
    public record Option(String provider, String gpu, String key, String stock, double hourlyUsd, boolean available, String why) {
    }

    private record Cand(String id, GpuProvider p, GpuProvider.Quote q, String why, Map<String, Object> row) {
        boolean ok() {
            return why == null;
        }

        String key() {
            return id + "|" + q.gpu();
        }
    }

    /** Every provider's every option with the reason it can or cannot be rented now. Read-only. */
    private List<Cand> candidates(GpuProvider.GpuSpec spec) {
        List<Cand> out = new ArrayList<>();
        for (String id : config.order()) {
            GpuProvider p = providers.get(id);
            if (p == null) {
                out.add(cand(id, null, GpuProvider.Quote.unavailable(id, ""), "no adapter"));
                continue;
            }
            if (!p.configured()) {
                out.add(cand(id, p, GpuProvider.Quote.unavailable(id, ""), "not configured (no key in .env, or adapter not built)"));
                continue;
            }
            List<GpuProvider.Quote> quotes;
            try {
                quotes = p.quotes(spec);
                if (quotes.isEmpty()) {
                    out.add(cand(id, p, GpuProvider.Quote.unavailable(id, ""), "no stock: " + p.quote(spec).detail()));
                    continue;
                }
            } catch (RuntimeException e) {
                out.add(cand(id, p, GpuProvider.Quote.unavailable(id, ""), "quote failed: " + e.getMessage()));
                continue;
            }
            for (GpuProvider.Quote q : quotes) {
                String why = wallet.refusal(id, q.hourlyUsd() * config.expectedHours());
                if (why == null) {
                    try {
                        new PodWatchdog(p, leases, events, limits, clock).checkCanStart(q.hourlyUsd());
                    } catch (RuntimeException e) {
                        why = e.getMessage();
                    }
                }
                out.add(cand(id, p, q, why));
            }
        }
        return out;
    }

    private static Cand cand(String id, GpuProvider p, GpuProvider.Quote q, String why) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("provider", id);
        if (q.available()) {
            row.put("stock", q.stock());
            row.put("gpu", q.gpu());
            row.put("hourlyUsd", q.hourlyUsd());
        }
        if (why != null) {
            row.put("skipped", why);
        }
        return new Cand(id, p, q, why, row);
    }

    /** What the owner can choose from, rentable options first, cheapest first. */
    public List<Option> options() {
        GpuProvider.GpuSpec spec = new GpuProvider.GpuSpec(config.gpuPreference(), config.minVramGb(), config.minRamGb(), config.diskGb());
        return candidates(spec).stream()
                .map(c -> new Option(c.id(), c.q().available() ? c.q().gpu() : null, c.q().available() ? c.key() : null,
                        c.q().stock(), c.q().hourlyUsd(), c.ok(), c.why()))
                .sorted(java.util.Comparator.comparing((Option o) -> !o.available()).thenComparingDouble(Option::hourlyUsd))
                .toList();
    }

    /**
     * REQUIRES_NEW: the pod, its lease and its RoutingDecision commit on their own, so a rollback of the caller's
     * transaction (DreamService writes shots and requests inside one) cannot orphan a running pod.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public synchronized Rental acquire(UUID shortId, String capability, String image, List<String> workerArgv,
                                       Map<String, String> env, String volumeId, Pick pick) {
        Rental existing = open.get(shortId);
        if (existing != null) {
            if (!gone(existing)) {
                leases.heartbeat(existing.instance().id());
                return existing;
            }
            log.warn("GPU broker: pod {} of Short {} is gone (self-stopped or killed); releasing and renting again",
                    existing.instance().id(), shortId);
            release(shortId, "pod gone before reuse (self-stopped or killed)");
        }
        GpuProvider.GpuSpec spec = new GpuProvider.GpuSpec(config.gpuPreference(), config.minVramGb(), config.minRamGb(), config.diskGb());
        List<Cand> all = candidates(spec);
        List<Map<String, Object>> considered = new ArrayList<>(all.stream().map(Cand::row).toList());
        // Cheapest live price first (owner, 20 Nov 2026); config order only breaks ties (the sort is stable).
        List<Cand> eligible = new ArrayList<>(all.stream().filter(Cand::ok)
                .sorted(java.util.Comparator.comparingDouble(c -> c.q().hourlyUsd())).toList());
        String how = "cheapest eligible live price";
        if (pick.manual()) {
            String key = pick.choice();
            if (key == null || key.isBlank()) {
                eligible.clear();
                considered.add(Map.of("provider", "-", "skipped", "Manual mode: no GPU picked yet (GET /api/gpu/broker/options, then choose one)"));
            } else {
                Cand wanted = all.stream().filter(c -> key.equals(c.key())).findFirst().orElse(null);
                if (wanted == null || !wanted.ok()) {
                    considered.add(Map.of("provider", "-", "skipped", "your pick " + key + " is not available now"
                            + (wanted == null ? "" : ": " + wanted.why())));
                    eligible.clear();
                } else {
                    eligible = new ArrayList<>(List.of(wanted));
                    how = "your manual pick";
                }
            }
        } else if (!eligible.isEmpty()) {
            double cheapest = eligible.getFirst().q().hourlyUsd();
            String learned = prefs.learnedKey().orElse(null);
            Cand fav = learned == null ? null : eligible.stream().filter(c -> learned.equals(c.key())).findFirst().orElse(null);
            if (fav != null && fav.q().hourlyUsd() <= cheapest * PreferenceStore.MAX_PREMIUM) {
                eligible.remove(fav);
                eligible.addFirst(fav);
                how = "learned from your last picks (you keep choosing it over the cheapest)";
            }
        }
        boolean unsafe = false;
        for (Cand e : eligible) {
            if (unsafe) {
                break;
            }
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
                // The call may have failed after the provider created the pod (timeout on the reply): look it up by its
                // generated name and adopt it before any other create, or it would run unleased and unbooked.
                GpuProvider.Instance found;
                try {
                    found = find(p, name, q);
                } catch (RuntimeException le) {
                    row.put("skipped", "create failed (" + ex.getMessage() + ") and the pod list failed (" + le.getMessage()
                            + "): not renting anywhere else until that is known");
                    log.warn("GPU broker: {} create failed for Short {} and the pod list failed too; stopping", id, shortId);
                    unsafe = true;
                    continue;
                }
                if (found == null) {
                    row.put("skipped", "create failed: " + ex.getMessage());
                    log.warn("GPU broker: {} create failed for Short {}: {}", id, shortId, ex.getMessage());
                    continue;
                }
                log.warn("GPU broker: {} create reported '{}' but pod {} exists; adopting it", id, ex.getMessage(), found.id());
                inst = found;
            }
            try {
                leases.open(id, inst.id(), inst.name(), "Dream Short " + shortId, shortId, inst.hourlyUsd(), inst.gpu());
            } catch (RuntimeException ex) { // never keep a pod we cannot lease
                safeTerminate(p, inst.id());
                row.put("skipped", "lease failed, pod terminated: " + ex.getMessage());
                continue;
            }
            row.put("chosen", true);
            String reason = id + ": " + how + ", $%.2f/h (%d eligible)".formatted(inst.hourlyUsd(), all.stream().filter(Cand::ok).count());
            try {
                records.decision(shortId, capability, considered, id, reason);
            } catch (RuntimeException ex) { // the pod is rented and leased; a ledger hiccup must not strand it
                log.warn("GPU broker: rented pod {} but its RoutingDecision failed: {}", inst.id(), ex.getMessage());
            }
            Rental r = new Rental(shortId, inst, now);
            open.put(shortId, r);
            log.info("GPU broker: rented {} {} ({}, ${}/h live) for Short {}", id, inst.id(), inst.gpu(), inst.hourlyUsd(), shortId);
            return r;
        }
        String reason = "No GPU can run " + capability + " now: " + considered.stream()
                .filter(r -> r.get("skipped") != null)
                .map(r -> r.get("provider") + (r.get("gpu") == null ? "" : " " + r.get("gpu")) + " (" + r.get("skipped") + ")").toList();
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

    /** What the media worker should do about the Short's pod while it waits on it or runs jobs. */
    public enum PodState {
        /** Still there (or its status is unreadable right now): carry on. */
        OPEN,
        /** The provider says it no longer exists (deleted from outside, self-stopped): rent another. */
        GONE,
        /** No open rental: the studio released it (Short ended): stop, never rent again for it. */
        NONE
    }

    /**
     * One provider status call. Lets a worker waiting for a pod notice within a minute that it was deleted, instead of
     * polling a dead address until its 30-minute ready limit.
     */
    public PodState podState(UUID shortId) {
        Rental r = open.get(shortId);
        if (r == null) {
            return PodState.NONE;
        }
        return gone(r) ? PodState.GONE : PodState.OPEN;
    }

    /** Readiness of the Short's pod (the worker's own /health is the final truth); also keeps the lease warm. */
    public GpuProvider.InstanceStatus status(UUID shortId) {
        Rental r = open.get(shortId);
        if (r == null) {
            return new GpuProvider.InstanceStatus(GpuProvider.State.GONE, null, "no open rental");
        }
        GpuProvider.InstanceStatus st = providers.get(r.instance().provider()).status(r.instance().id());
        if (st.state() != GpuProvider.State.GONE) { // a gone pod must not look alive: release books up to the last real beat
            leases.heartbeat(r.instance().id());
        }
        return st;
    }

    /** True when the provider says the pod no longer exists. An unreadable status is not "gone". */
    private boolean gone(Rental r) {
        GpuProvider p = providers.get(r.instance().provider());
        if (p == null) {
            return false;
        }
        try {
            return p.status(r.instance().id()).state() == GpuProvider.State.GONE;
        } catch (RuntimeException e) {
            log.warn("GPU broker: status of {} unreadable ({}); keeping the rental", r.instance().id(), e.getMessage());
            return false;
        }
    }

    /** The studio pod with this generated name, if the provider has it (a create whose reply was lost). */
    private GpuProvider.Instance find(GpuProvider p, String name, GpuProvider.Quote q) {
        for (GpuProvider.Pod pod : p.listPods()) {
            if (name.equals(pod.name()) && !"TERMINATED".equalsIgnoreCase(pod.status())) {
                double hourly = pod.costPerHrUsd() > 0 ? pod.costPerHrUsd() : q.hourlyUsd();
                return new GpuProvider.Instance(p.provider(), pod.id(), name, hourly, q.gpu().split("\\|")[0],
                        p.endpointFor(pod.id(), config.port()));
            }
        }
        return null;
    }

    /** Ends the rental: terminate, book the time, release the lease. Safe to call twice. */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public synchronized void release(UUID shortId, String why) {
        Rental r = open.remove(shortId);
        if (r == null) {
            return;
        }
        GpuProvider p = providers.get(r.instance().provider());
        Instant end = clock.instant();
        if (gone(r)) { // deleted or self-stopped earlier: it stopped billing at about its last heartbeat, not now
            Instant beat = leases.find(r.instance().id()).map(PodLeases.Lease::heartbeatAt).orElse(null);
            if (beat != null && beat.isAfter(r.createdAt()) && beat.isBefore(end)) {
                end = beat;
            }
        }
        boolean gone = safeTerminate(p, r.instance().id());
        double hours = Math.max(0, Duration.between(r.createdAt(), end).toSeconds() / 3600.0);
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
