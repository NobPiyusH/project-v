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

    /**
     * The studio's ONE shared pod (owner, 20 Nov 2026: "1 pod only to run as many Shorts as I want"). {@code shortId} is the
     * Short that rented it first; any number of Shorts may use it, see {@link #users}.
     */
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
    /** Per-rental worker tokens (Vast.ai always, RunPod by config); OFF in tests that never asked for them. */
    private WorkerAuth auth = WorkerAuth.OFF;
    /** The one shared pod (null = none); changed only inside the synchronized methods, read lock-free. */
    private volatile Rental shared;
    /** The Shorts using the shared pod, and since when. A Short that detached keeps its used seconds in {@link #usedSeconds}. */
    private final Map<UUID, Instant> users = new ConcurrentHashMap<>();
    /** Shorts that were on a pod retired as never-ready: they move to the next rental. */
    private final List<UUID> pendingCarry = new ArrayList<>();
    /** Seconds already used by Shorts that detached from the current pod: with the attached ones, the cost split. */
    private final Map<UUID, Long> usedSeconds = new ConcurrentHashMap<>();

    @Autowired
    public GpuBroker(List<GpuProvider> providers, BrokerConfig config, GpuWallet wallet, BrokerRecords records,
                     PodLeases leases, PodEvents events, GpuPodLimits.FromConfig limits, PreferenceStore prefs) {
        this(providers, config, wallet, records, leases, events, limits.limits, Clock.systemUTC(), prefs);
    }

    @Autowired(required = false)
    public void setAuth(WorkerAuth auth) {
        this.auth = auth;
    }

    /** Which providers have run a real worker (only those are searched); everything counts as proven until this is set. */
    private ProviderStates states = ProviderStates.ALL_PROVEN;
    /** The provider whose canary is running (null = none): no other rental starts meanwhile (one pod only). */
    private volatile String canaryRunning;

    @Autowired(required = false)
    public void setStates(ProviderStates states) {
        this.states = states;
    }

    /** Where each rental's boot time goes (rent / running / worker timeline). Nothing is kept until this is set. */
    private BootTimings bootTimings = BootTimings.NONE;

    @Autowired(required = false)
    public void setBootTimings(BootTimings bootTimings) {
        this.bootTimings = bootTimings;
    }

    /** The boot timeline of the Short's current pod: steps in seconds since the rent, and each weight download. */
    public Optional<Map<String, Object>> bootTiming(UUID shortId) {
        return open(shortId).flatMap(r -> bootTimings.get(r.instance().id())).map(BootTimings::summary);
    }

    /** The worker's own timeline (from its ready /health), stored with the rental and logged. Best effort. */
    public void reportBoot(UUID shortId, Map<String, Object> timing) {
        Rental r = open(shortId).orElse(null);
        if (r == null || timing == null) {
            return;
        }
        try {
            bootTimings.workerReport(r.instance().id(), timing);
            bootTimings.get(r.instance().id()).ifPresent(row -> log.info("GPU boot timing {} {}: {}",
                    row.provider(), row.podId(), BootTimings.summary(row)));
        } catch (RuntimeException e) {
            log.warn("GPU broker: could not store the boot timing of {}: {}", r.instance().id(), e.getMessage());
        }
    }

    private void noteRented(String provider, GpuProvider.Instance inst, String image, Instant at) {
        try {
            bootTimings.rented(inst.id(), provider, image, at);
        } catch (RuntimeException e) {
            log.warn("GPU broker: boot timing not started for {}: {}", inst.id(), e.getMessage());
        }
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
                    auth.decorate(l.provider(), l.name(), p.endpointFor(l.podId(), config.port())));
            Rental r = new Rental(l.dreamShortId(), inst, l.createdAt());
            if (shared != null) {
                // Older builds rented one pod per Short; only ONE shared pod is kept. The others end now and are booked to their Short.
                log.warn("GPU broker: second open pod {} at startup; ending it (one shared pod only)", l.podId());
                endPod(r, Map.of(l.dreamShortId(), 1L), "extra pod ended at startup: only one shared pod is kept");
                continue;
            }
            shared = r;
            List<UUID> attached = safeAttached(l.podId());
            (attached.isEmpty() ? List.of(l.dreamShortId()) : attached).forEach(u -> users.put(u, clock.instant()));
            try {
                leases.heartbeat(l.podId());
            } catch (RuntimeException e) {
                log.warn("GPU broker: heartbeat of recovered lease {} failed: {}", l.podId(), e.getMessage());
            }
            log.info("GPU broker: recovered the shared pod {} {} for {} Short(s) after restart", l.provider(), l.podId(), users.size());
        }
    }

    private List<UUID> safeAttached(String podId) {
        try {
            return leases.attached(podId);
        } catch (RuntimeException e) {
            return List.of();
        }
    }

    /** The shared pod, when this Short is using it. */
    public Optional<Rental> open(UUID shortId) {
        Rental r = shared;
        return r != null && users.containsKey(shortId) ? Optional.of(r) : Optional.empty();
    }

    /** Whether the shared pod is on (rented by any Short): a new Short then starts without a boot. */
    public boolean hasOpenPod() {
        return shared != null;
    }

    /** The Shorts using the shared pod right now. */
    public List<UUID> openShortIds() {
        return shared == null ? List.of() : List.copyOf(users.keySet());
    }

    /**
     * When the Short's pod was last used: the newest lease heartbeat (the worker pings it while it waits on or runs a job;
     * renting or reusing the pod also counts). Falls back to the time it was rented.
     */
    public Optional<Instant> lastActivity(UUID shortId) {
        Rental r = open(shortId).orElse(null);
        if (r == null) {
            return Optional.empty();
        }
        Instant beat = leases.find(r.instance().id()).map(PodLeases.Lease::heartbeatAt).orElse(null);
        return Optional.of(beat != null && beat.isAfter(r.createdAt()) ? beat : r.createdAt());
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
        return candidates(spec, config.expectedHours());
    }

    /** {@code hours}: how long this rental is expected to run (the wallet and daily-cap checks use hourly x hours). */
    private List<Cand> candidates(GpuProvider.GpuSpec spec, double hours) {
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
            if (!states.proven(id)) {
                out.add(cand(id, p, GpuProvider.Quote.unavailable(id, ""), "not proven yet: no worker has run on it. Run its preflight, then the owner-approved canary"
                        + " (POST /api/gpu/broker/providers/" + id + "/canary?approve=true)"));
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
                String why = wallet.refusal(id, q.hourlyUsd() * hours);
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
            if (q.detail() != null && !q.detail().isBlank()) {
                row.put("detail", q.detail()); // e.g. Vast: the host's country and machine id
            }
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
        return acquire(shortId, capability, image, workerArgv, env, volumeId, pick, config.expectedHours());
    }

    /**
     * As above, with the rental's expected hours for this Short (com.projectv.dream.RentalEstimate); 0 or less = the
     * config's expectedHours.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public synchronized Rental acquire(UUID shortId, String capability, String image, List<String> workerArgv,
                                       Map<String, String> env, String volumeId, Pick pick, double expectedHours) {
        double hours = expectedHours > 0 ? expectedHours : config.expectedHours();
        if (canaryRunning != null && shared == null) {
            throw new NoGpuAvailableException("The " + canaryRunning + " canary rental is running (a few minutes); nothing else is rented meanwhile. Press Retry shortly.");
        }
        // ONE shared pod: when it is on, every Short uses it (a manual GPU pick applies only when no pod is on).
        Rental existing = shared;
        List<UUID> carried = List.of();
        if (existing != null) {
            if (!gone(existing)) {
                existing = withEndpoint(existing);
                attach(shortId);
                leases.heartbeat(existing.instance().id());
                return existing;
            }
            log.warn("GPU broker: the shared pod {} is gone (self-stopped or killed); ending it and renting again", existing.instance().id());
            carried = new ArrayList<>(users.keySet()); // the Shorts that were using it move to the new pod
            endShared("pod gone before reuse (self-stopped or killed)");
        }
        GpuProvider.GpuSpec spec = new GpuProvider.GpuSpec(config.gpuPreference(), config.minVramGb(), config.minRamGb(), config.diskGb());
        List<Cand> all = candidates(spec, hours);
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
            List<String> argv = p.guard(workerArgv, limits.maxRuntimeMinutes(), limits.idleMinutes(), now);
            GpuProvider.Instance inst;
            try {
                inst = p.create(new GpuProvider.CreateRequest(name, image, config.diskGb(), argv,
                        auth.env(id, name, env), config.port(), volumeId, q));
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
            inst = new GpuProvider.Instance(inst.provider(), inst.id(), inst.name(), inst.hourlyUsd(), inst.gpu(),
                    auth.decorate(id, inst.name(), inst.endpoint()));
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
            noteRented(id, inst, image, now);
            Rental r = new Rental(shortId, inst, now);
            shared = r;
            attach(shortId);
            carried.forEach(this::attach);
            pendingCarry.forEach(this::attach);
            pendingCarry.clear();
            log.info("GPU broker: rented {} {} ({}, ${}/h live) for Short {}", id, inst.id(), inst.gpu(), inst.hourlyUsd(), shortId);
            return r;
        }
        String daily = considered.stream().map(r -> r.get("skipped")).filter(w -> w != null
                && w.toString().startsWith(GpuWallet.DAILY_CAP)).map(Object::toString).findFirst().orElse(null);
        if (daily != null && eligible.isEmpty() && !unsafe) {
            String msg = "Daily GPU cap reached (" + daily + "). Nothing was rented; this Short waits for you. Press Retry"
                    + " after 00:00 UTC, or raise pricing.gpuBroker.dailyCapUsd in config/providers.yaml.";
            records.decision(shortId, capability, considered, null, msg);
            throw new NoGpuAvailableException(msg);
        }
        String reason = "No GPU can run " + capability + " now: " + considered.stream()
                .filter(r -> r.get("skipped") != null)
                .map(r -> r.get("provider") + (r.get("gpu") == null ? "" : " " + r.get("gpu")) + " (" + r.get("skipped") + ")").toList();
        records.decision(shortId, capability, considered, null, reason);
        throw new NoGpuAvailableException(reason);
    }

    /** A rental rebuilt after a restart may not know its address yet (Vast maps the port late): ask the provider again. */
    private Rental withEndpoint(Rental r) {
        GpuProvider.Instance i = r.instance();
        GpuProvider p = providers.get(i.provider());
        if (i.endpoint() != null || p == null) {
            return r;
        }
        String ep = auth.decorate(i.provider(), i.name(), p.endpointFor(i.id(), config.port()));
        if (ep == null) {
            return r;
        }
        Rental fixed = new Rental(r.shortId(), new GpuProvider.Instance(i.provider(), i.id(), i.name(), i.hourlyUsd(), i.gpu(), ep), r.createdAt());
        shared = fixed;
        return fixed;
    }

    /** Cheapest live quote that could be rented right now (configured, in stock, inside its wallet); read-only. For estimates. */
    public Optional<GpuProvider.Quote> cheapestQuote() {
        GpuProvider.GpuSpec spec = new GpuProvider.GpuSpec(config.gpuPreference(), config.minVramGb(), config.minRamGb(), config.diskGb());
        GpuProvider.Quote best = null;
        for (String id : config.order()) {
            GpuProvider p = providers.get(id);
            if (p == null || !p.configured() || !states.proven(id)) {
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
    /** What the pod that is on now has cost since it was rented (not booked until it ends), USD. 0 when none is on. */
    public double runningUsd() {
        Rental r = shared;
        return r == null ? 0 : Math.max(0, Duration.between(r.createdAt(), clock.instant()).toSeconds() / 3600.0) * r.instance().hourlyUsd();
    }

    /** Today's GPU spend (UTC) vs the daily cap, for the dashboard: booked + the running pod so far. */
    public Map<String, Object> today() {
        double booked = wallet.spentTodayUsd(), running = runningUsd();
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("spentUsd", Math.round((booked + running) * 100) / 100.0);
        m.put("bookedUsd", Math.round(booked * 100) / 100.0);
        m.put("runningUsd", Math.round(running * 100) / 100.0);
        m.put("capUsd", wallet.dailyCapUsd());
        return m;
    }

    public void heartbeat(UUID shortId) {
        Rental r = open(shortId).orElse(null);
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
        Rental r = open(shortId).orElse(null);
        if (r == null) {
            return PodState.NONE;
        }
        return gone(r) ? PodState.GONE : PodState.OPEN;
    }

    /** Readiness of the Short's pod (the worker's own /health is the final truth); also keeps the lease warm. */
    public GpuProvider.InstanceStatus status(UUID shortId) {
        Rental r = open(shortId).orElse(null);
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
            GpuProvider.State state = p.status(r.instance().id()).state();
            if (state == GpuProvider.State.RUNNING) {
                try {
                    bootTimings.running(r.instance().id(), clock.instant());
                } catch (RuntimeException ignored) { // a timing row never decides whether a pod is alive
                }
            }
            return state == GpuProvider.State.GONE;
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

    /**
     * This Short stops using the shared pod. The pod itself ends (terminated, booked, lease released) when the last Short
     * lets go; while others still use it, it stays on. Safe to call twice.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public synchronized void release(UUID shortId, String why) {
        Rental r = shared;
        Instant since = users.remove(shortId);
        if (r == null || since == null) {
            return;
        }
        usedSeconds.merge(shortId, Math.max(1, Duration.between(since, clock.instant()).toSeconds()), Long::sum);
        persistDetach(r.instance().id(), shortId);
        if (users.isEmpty()) {
            endShared(why);
        } else {
            log.info("GPU broker: Short {} let go of the shared pod {} ({}); it stays on for {} other Short(s)",
                    shortId, r.instance().id(), why, users.size());
        }
    }

    /**
     * A media worker waited the whole ready limit (or saw the boot fail) on the shared pod at {@code url}: that pod is
     * useless, so it is terminated and booked now, and the Shorts on it move to the next rental (21 Nov 2026: a pod that
     * never became ready). Does nothing when the shared pod is already another one (a second worker asked late).
     * @return true when the pod was retired
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public synchronized boolean retireUnready(String url, String why) {
        Rental r = shared;
        if (r == null || url == null || !url.strip().replaceAll("/+$", "").equals(
                java.util.Objects.toString(r.instance().endpoint(), "").replaceAll("/+$", ""))) {
            return false;
        }
        log.warn("GPU broker: pod {} never became ready ({}); terminating it and renting another", r.instance().id(), why);
        pendingCarry.addAll(users.keySet());
        endShared("never became ready: " + why);
        return true;
    }

    /** The owner's "Turn off GPU": ends the shared pod now, for every Short that uses it. Safe to call with no pod. */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public synchronized void releaseAll(String why) {
        endShared(why);
    }

    private void attach(UUID shortId) {
        Rental r = shared;
        if (r != null && users.putIfAbsent(shortId, clock.instant()) == null) {
            try {
                leases.attach(r.instance().id(), shortId);
            } catch (RuntimeException e) {
                log.warn("GPU broker: could not record Short {} on pod {}: {}", shortId, r.instance().id(), e.getMessage());
            }
        }
    }

    private void persistDetach(String podId, UUID shortId) {
        try {
            leases.detach(podId, shortId);
        } catch (RuntimeException e) {
            log.warn("GPU broker: could not record Short {} leaving pod {}: {}", shortId, podId, e.getMessage());
        }
    }

    /** Ends the shared pod for everyone: the cost is split by how long each Short used it. */
    private void endShared(String why) {
        Rental r = shared;
        if (r == null) {
            return;
        }
        Instant now = clock.instant();
        Map<UUID, Long> weights = new LinkedHashMap<>(usedSeconds);
        users.forEach((u, since) -> weights.merge(u, Math.max(1, Duration.between(since, now).toSeconds()), Long::sum));
        users.keySet().forEach(u -> persistDetach(r.instance().id(), u));
        shared = null;
        users.clear();
        usedSeconds.clear();
        endPod(r, weights, why);
    }

    /** Terminates a pod, books its time (split by {@code weights}) and releases its lease. */
    private void endPod(Rental r, Map<UUID, Long> weights, String why) {
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
            bookRental(r, hours, weights, why);
            leases.release(r.instance().id());
        } catch (RuntimeException e) {
            log.warn("GPU broker: rental {} ended but its ledger row failed: {}", r.instance().id(), e.getMessage());
        }
        if (!gone) {
            log.warn("GPU broker: could not terminate {}; lease released, the watchdog will", r.instance().id());
        }
    }

    private void bookRental(Rental r, double hours, Map<UUID, Long> weights, String why) {
        String note = "GPU rental %s (%s) ended: %s".formatted(r.instance().id(), r.instance().gpu(), why);
        Map<UUID, Long> w = weights.isEmpty() ? Map.of(r.shortId(), 1L) : weights;
        if (w.size() == 1) {
            records.cost(w.keySet().iterator().next(), r.instance().provider(), hours, r.instance().hourlyUsd(), note);
            return;
        }
        long total = w.values().stream().mapToLong(Long::longValue).sum();
        for (Map.Entry<UUID, Long> e : w.entrySet()) {
            double share = total > 0 ? (double) e.getValue() / total : 1.0 / w.size();
            records.cost(e.getKey(), r.instance().provider(), hours * share, r.instance().hourlyUsd(),
                    note + " (%d%% of the shared pod)".formatted(Math.round(share * 100)));
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

    // ------------------------------------------------------------------------------------------------ pre-check and canary

    /** One provider's state for the GPU API: configured / preflight / proven. */
    public Map<String, Object> providerStatus(String id) {
        GpuProvider p = providers.get(id);
        ProviderStates.State st = states.get(id);
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("provider", id);
        m.put("configured", p != null && p.configured());
        m.put("preflightOk", st.preflightOk());
        m.put("preflightAt", st.preflightAt() == null ? null : st.preflightAt().toString());
        m.put("preflightNote", st.preflightNote());
        m.put("proven", st.proven());
        m.put("provenNote", st.provenNote());
        m.put("monthlyCapUsd", config.capUsd(id));
        m.put("inAutoSearch", p != null && p.configured() && st.proven() && config.capUsd(id) > 0);
        return m;
    }

    public List<Map<String, Object>> providerStatuses() {
        return config.order().stream().filter(providers::containsKey).map(this::providerStatus).toList();
    }

    /** Free pre-check of one provider with the real create request (never rents). The result is stored for the GPU API. */
    public GpuProvider.Preflight preflight(String id, String image, List<String> workerArgv, Map<String, String> env, String volumeId) {
        GpuProvider p = providers.get(id);
        if (p == null) {
            return new GpuProvider.Preflight(false, List.of("no adapter for '" + id + "'"));
        }
        GpuProvider.GpuSpec spec = new GpuProvider.GpuSpec(config.gpuPreference(), config.minVramGb(), config.minRamGb(), config.diskGb());
        GpuProvider.Preflight r;
        try {
            String name = limits.prefix() + id + "-preflight";
            List<String> argv = p.guard(workerArgv, limits.maxRuntimeMinutes(), limits.idleMinutes(), clock.instant());
            GpuProvider.CreateRequest probe = new GpuProvider.CreateRequest(name, image, config.diskGb(), argv,
                    auth.env(id, name, env), config.port(), volumeId, null);
            r = p.preflight(spec, probe);
        } catch (RuntimeException e) {
            r = new GpuProvider.Preflight(false, List.of("preflight failed: " + e.getMessage()));
        }
        try {
            states.recordPreflight(id, r.ok(), String.join("; ", r.checks()));
        } catch (RuntimeException e) {
            log.warn("GPU broker: could not store the preflight of {}: {}", id, e.getMessage());
        }
        return r;
    }

    /** Asks the worker at {@code endpoint} (token already in the address) if it is ready; null = ready, else why not. */
    public interface Health {
        String check(String endpoint);
    }

    /**
     * The canary: ONE tiny owner-approved rental on {@code id}. Rents the provider's cheapest GPU, waits for the worker's health
     * check, always terminates and books it, and sets proven=true only when the worker answered. Refused while any pod is on.
     * Leased without a Short, so the watchdog covers it if this method dies.
     */
    public Map<String, Object> canary(String id, String image, List<String> workerArgv, Map<String, String> env, String volumeId,
                                      Health health, int timeoutSeconds, long pollMillis) {
        return canary(id, image, workerArgv, env, volumeId, health, timeoutSeconds, pollMillis, DEFAULT_CANARY_MAX_USD);
    }

    static final int CANARY_MAX_ATTEMPTS = 3;
    public static final double DEFAULT_CANARY_MAX_USD = 0.60;

    /** @param maxUsd the owner-approved cap: a plan whose price for the whole canary run would exceed it is never rented */
    public Map<String, Object> canary(String id, String image, List<String> workerArgv, Map<String, String> env, String volumeId,
                                      Health health, int timeoutSeconds, long pollMillis, double maxUsd) {
        GpuProvider p = providers.get(id);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("provider", id);
        out.put("proven", false);
        GpuProvider.Preflight pre = preflight(id, image, workerArgv, env, volumeId);
        out.put("preflight", pre.checks());
        if (p == null || !p.configured() || !pre.ok()) {
            out.put("result", "not run: preflight failed, nothing was rented");
            return out;
        }
        String name;
        GpuProvider.Quote q = null;
        List<GpuProvider.Quote> plans;
        double canaryHours = (timeoutSeconds + 120) / 3600.0;
        synchronized (this) {
            if (shared != null || canaryRunning != null) {
                out.put("result", "not run: a pod or another canary is on; turn the GPU off first");
                return out;
            }
            GpuProvider.GpuSpec spec = new GpuProvider.GpuSpec(config.gpuPreference(), config.minVramGb(), config.minRamGb(), config.diskGb());
            plans = p.quotes(spec).stream().sorted(java.util.Comparator.comparingDouble(GpuProvider.Quote::hourlyUsd))
                    .limit(CANARY_MAX_ATTEMPTS).toList();
            if (plans.isEmpty()) {
                out.put("result", "not run: no GPU in stock, nothing was rented");
                return out;
            }
            q = plans.getFirst();
            double cost = q.hourlyUsd() * canaryHours;
            if (cost > maxUsd) {
                out.put("result", String.format("not run: the cheapest plan (%s, $%.2f/h) would cost $%.2f for a %d s canary, over the approved cap of $%.2f",
                        q.gpu(), q.hourlyUsd(), cost, timeoutSeconds + 120, maxUsd));
                return out;
            }
            String why = wallet.refusal(id, cost);
            if (why != null) {
                out.put("result", "not run: " + why);
                return out;
            }
            canaryRunning = id;
            name = limits.prefix() + id + "-canary-" + UUID.randomUUID().toString().substring(0, 4);
        }
        Instant start = clock.instant();
        GpuProvider.Instance inst = null;
        String failure = null;
        List<String> attempts = new java.util.ArrayList<>();
        out.put("attempts", attempts);
        try {
            List<String> argv = p.guard(workerArgv, Math.max(10, timeoutSeconds / 60 + 5), limits.idleMinutes(), start);
            for (int i = 0; i < plans.size(); i++) {
                q = plans.get(i);
                double cost = q.hourlyUsd() * canaryHours;
                if (cost > maxUsd) {
                    attempts.add(q.gpu() + " $" + q.hourlyUsd() + "/h: skipped, $" + String.format("%.2f", cost) + " is over the cap of $" + String.format("%.2f", maxUsd));
                    failure = "create failed: no plan left within the approved cap after: " + String.join("; ", attempts);
                    break;
                }
                try {
                    inst = p.create(new GpuProvider.CreateRequest(name, image, config.diskGb(), argv, auth.env(id, name, env),
                            config.port(), volumeId, q));
                    attempts.add(q.gpu() + " $" + q.hourlyUsd() + "/h: created");
                    failure = null;
                    break;
                } catch (GpuProvider.PlanUnavailableException ex) {
                    attempts.add(q.gpu() + " $" + q.hourlyUsd() + "/h: not available (" + ex.getMessage() + ")");
                    failure = "create failed: every plan tried was unavailable: " + String.join("; ", attempts);
                } catch (RuntimeException ex) {
                    attempts.add(q.gpu() + " $" + q.hourlyUsd() + "/h: failed (" + ex.getMessage() + ")");
                    failure = "create failed: " + ex.getMessage();
                    try {
                        inst = find(p, name, q); // a pod whose create reply was lost is adopted, so it is terminated below
                    } catch (RuntimeException le) {
                        failure += " (and the pod list failed: the watchdog ends any pod named " + name + ")";
                    }
                    break; // only "plan not available" moves on to the next plan
                }
            }
            if (inst != null) {
                try {
                    leases.open(id, inst.id(), inst.name(), "canary");
                } catch (RuntimeException ex) {
                    log.warn("GPU broker: canary lease failed: {}", ex.getMessage());
                }
                out.put("gpu", inst.gpu());
                out.put("hourlyUsd", inst.hourlyUsd());
                if (failure == null) {
                    failure = waitHealthy(p, inst, health, timeoutSeconds, pollMillis);
                }
            }
        } finally {
            if (inst != null) {
                boolean ended = safeTerminate(p, inst.id());
                double hours = Math.max(1 / 3600.0, Duration.between(start, clock.instant()).toSeconds() / 3600.0);
                try {
                    records.cost(null, id, hours, inst.hourlyUsd(), "canary rental " + inst.id() + " (" + inst.gpu() + ")");
                    leases.release(inst.id());
                } catch (RuntimeException e) {
                    log.warn("GPU broker: canary {} ended but its ledger row failed: {}", inst.id(), e.getMessage());
                }
                out.put("terminated", ended);
                out.put("hours", hours);
            }
            canaryRunning = null;
        }
        if (failure == null && inst != null) {
            states.markProven(id, "canary ok " + clock.instant() + " on " + inst.gpu() + " $" + inst.hourlyUsd() + "/h");
            out.put("proven", true);
            out.put("result", "worker healthy; pod terminated; provider marked proven");
        } else {
            out.put("result", "failed, provider stays unproven: " + failure);
        }
        return out;
    }

    /** Null when the worker answered its health check in time, else why not. */
    private String waitHealthy(GpuProvider p, GpuProvider.Instance inst, Health health, int timeoutSeconds, long pollMillis) {
        String ep = auth.decorate(inst.provider(), inst.name(), inst.endpoint());
        long deadline = System.currentTimeMillis() + timeoutSeconds * 1000L;
        String last = "no address yet";
        while (System.currentTimeMillis() < deadline) {
            if (ep == null) {
                ep = auth.decorate(inst.provider(), inst.name(), p.endpointFor(inst.id(), config.port()));
            }
            if (ep != null) {
                last = health.check(ep);
                if (last == null) {
                    return null;
                }
            }
            try {
                Thread.sleep(pollMillis);
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
                return "interrupted";
            }
        }
        return "worker never became healthy within " + timeoutSeconds + " s: " + last;
    }

    /** Nothing in the chain can serve the job; non-retryable, the step waits for the owner (rule 2b). */
    public static class NoGpuAvailableException extends RuntimeException {
        public NoGpuAvailableException(String message) {
            super(message);
        }
    }
}
