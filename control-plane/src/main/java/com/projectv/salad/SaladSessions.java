package com.projectv.salad;

import com.projectv.gpu.GpuPodLimits;
import com.projectv.gpu.PodLeases;
import com.projectv.gpu.PodWatchdog;
import com.projectv.ledger.BudgetGuard;
import com.projectv.ledger.Pricing;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

/**
 * One Salad container group per Dream Short (docs/plans/salad-route.md). {@link #ensure} creates it (or returns the open
 * one) and keeps its lease warm; {@link #release} deletes it and books the group time the jobs did not cover. Every
 * failure path ends in a delete or a released lease, which the watchdog turns into a delete (rule 8).
 */
@Component
public class SaladSessions {

    private static final Logger log = LoggerFactory.getLogger(SaladSessions.class);

    /** What a request needs to reach the group's worker. */
    public record Session(String groupName, String url, double hourlyUsd, String gpuClass, Instant createdAt) {
    }

    private final SaladApi api;
    private final PodLeases leases;
    private final SaladStore store;
    private final Pricing pricing;
    private final BudgetGuard budget;
    private final PodWatchdog watchdog;

    public SaladSessions(SaladApi api, PodLeases leases, SaladStore store, Pricing pricing, BudgetGuard budget,
                         SaladWatchdogConfig watchdog) {
        this.api = api;
        this.leases = leases;
        this.store = store;
        this.pricing = pricing;
        this.budget = budget;
        this.watchdog = watchdog.watchdog();
    }

    public SaladSettings settings() {
        return api.settings();
    }

    public SaladApi api() {
        return api;
    }

    /** The live hourly rate for the group we would create (for estimates). Throws when Salad can't be reached. */
    public double liveHourlyUsd() {
        requireConfigured();
        return api.pickGpu().hourlyUsd();
    }

    private void requireConfigured() {
        if (!api.configured()) {
            throw new IllegalStateException("Salad is not configured: set SALAD_API_KEY, SALAD_ORG and SALAD_PROJECT in .env");
        }
    }

    /** Opens (or returns) this Short's group. Throws a plain failure when Salad is unavailable: the step then waits for the owner. */
    public synchronized Session ensure(UUID dreamId) {
        requireConfigured();
        Optional<Session> open = open(dreamId);
        if (open.isPresent()) {
            Optional<Map<String, Object>> g = api.get(open.get().groupName());
            String st = g.map(SaladApi::status).orElse("gone");
            if (!"gone".equals(st) && !"failed".equalsIgnoreCase(st) && !"stopped".equalsIgnoreCase(st)) {
                leases.heartbeat(open.get().groupName());
                return open.get();
            }
            log.warn("Salad group {} is {}; creating a fresh one for Short {}", open.get().groupName(), st, dreamId);
            release(dreamId, "group " + st);
        }
        SaladApi.Gpu gpu = api.pickGpu();
        BigDecimal startInr = pricing.saladInr(gpu.hourlyUsd(), pricing.saladStartSeconds());
        budget.requireSalad(startInr, "starting a Salad GPU group");
        watchdog.checkCanStart(gpu.hourlyUsd());
        String name = settings().prefix() + dreamId.toString().substring(0, 8) + "-" + UUID.randomUUID().toString().substring(0, 4);
        byte[] src = worker();
        Map<String, Object> created = api.create(name, gpu, SaladBoot.env(src, settings().port()), SaladBoot.command());
        leases.open("salad", name, name, "Dream Short " + dreamId);
        String url;
        try {
            url = SaladApi.gatewayUrl(created).or(() -> api.get(name).flatMap(SaladApi::gatewayUrl))
                    .orElseThrow(() -> new IllegalStateException("Salad gave group " + name + " no gateway address"));
        } catch (RuntimeException e) {
            leases.release(name);
            try {
                api.delete(name);
            } catch (RuntimeException de) {
                log.warn("Could not delete Salad group {} ({}); the watchdog will", name, de.getMessage());
            }
            throw e;
        }
        store.insert(name, dreamId, url, gpu.name(), settings().priority(), gpu.hourlyUsd());
        log.info("Salad group {} created for Short {} ({} {} at ${}/h live)", name, dreamId, gpu.name(), settings().priority(), gpu.hourlyUsd());
        return open(dreamId).orElseThrow();
    }

    /** Keeps the lease warm (the Python worker calls this through the control plane while it waits or runs jobs). */
    public void heartbeat(UUID dreamId) {
        open(dreamId).ifPresent(s -> leases.heartbeat(s.groupName()));
    }

    public Optional<Session> open(UUID dreamId) {
        return store.open(dreamId);
    }

    /** Job seconds the ledger already holds for this Short's group, so the release books only the remainder. */
    public void bookedJobSeconds(UUID dreamId, double seconds) {
        store.addBooked(dreamId, seconds);
    }

    /** Deletes the Short's group (if any) and books the time the jobs did not. Never throws: a failed delete is left to the watchdog. */
    public synchronized void release(UUID dreamId, String why) {
        Optional<Session> open = open(dreamId);
        if (open.isEmpty()) {
            return;
        }
        Session s = open.get();
        leases.release(s.groupName()); // first: even if the delete below fails, the watchdog now sees a released lease
        boolean deleted = false;
        try {
            api.delete(s.groupName());
            deleted = true;
        } catch (RuntimeException e) {
            log.warn("Could not delete Salad group {} ({}): {}; the watchdog will retry", s.groupName(), why, e.getMessage());
        }
        if (!deleted) {
            return;
        }
        double booked = store.booked(s.groupName());
        double alive = Math.max(0, Duration.between(s.createdAt(), Instant.now()).toMillis() / 1000.0);
        double rest = Math.max(0, alive - booked);
        if (rest > 0) {
            BigDecimal inr = pricing.saladInr(s.hourlyUsd(), rest).setScale(4, RoundingMode.HALF_UP);
            store.cost(dreamId, inr, rest, "Salad group %s: start-up, download and idle time not covered by jobs (%s; $%.4f/h live rate)"
                    .formatted(s.groupName(), why, s.hourlyUsd()));
        }
        store.markDeleted(s.groupName());
        log.info("Salad group {} deleted ({}); {} s alive, {} s booked by jobs", s.groupName(), why, Math.round(alive), Math.round(booked));
    }

    private byte[] worker() {
        try {
            return Files.readAllBytes(Path.of(settings().workerFile()));
        } catch (java.io.IOException e) {
            throw new IllegalStateException("Salad worker file not found: " + settings().workerFile() + " (projectv.salad.worker-file)", e);
        }
    }

    /** Limits for the Salad watchdog, from config. */
    static GpuPodLimits limits(SaladSettings s) {
        return new GpuPodLimits(s.prefix(), s.maxLifetimeMinutes(), s.idleMinutes(), s.leaseGraceMinutes(), s.maxGroups(),
                s.maxHourlyUsd(), s.watchdogDryRun());
    }
}
