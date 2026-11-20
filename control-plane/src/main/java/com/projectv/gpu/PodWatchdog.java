package com.projectv.gpu;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/**
 * Control-plane layer of the GPU safety net (CLAUDE.md rule 8). Each sweep lists the provider's pods and terminates
 * every pod named with the studio prefix that is past the max lifetime, has no lease after the grace period, has a
 * released lease, or whose lease heartbeat is older than the idle limit. Pods without the prefix are never touched.
 * Dry-run (the default) only logs. The in-pod layer ({@link PodSelfStop}) covers the laptop being off.
 */
public class PodWatchdog {

    private static final Logger log = LoggerFactory.getLogger(PodWatchdog.class);

    /** One pod the sweep decided to terminate (or would, in dry-run). */
    public record Action(String podId, String name, String reason, boolean done, String error) {
    }

    /** What the last sweep saw: served to the dashboard as the watchdog status. */
    public record Report(Instant at, boolean dryRun, boolean configured, int studioPods, double studioHourlyUsd,
                         List<Action> actions, List<String> warnings, String error) {
    }

    static final long ORPHAN_BOOK_MIN_SECONDS = 600;

    private final PodApi api;
    private final PodLeases leases;
    private final PodEvents events;
    private final GpuPodLimits limits;
    private final Clock clock;
    private volatile Report last;

    public PodWatchdog(PodApi api, PodLeases leases, PodEvents events, GpuPodLimits limits, Clock clock) {
        this.api = api;
        this.leases = leases;
        this.events = events;
        this.limits = limits;
        this.clock = clock;
    }

    public Report last() {
        return last;
    }

    public GpuPodLimits limits() {
        return limits;
    }

    /** One sweep. Never throws: a failed sweep is reported and logged, and the next one tries again. */
    public Report sweep() {
        Instant now = clock.instant();
        Report r;
        try {
            r = doSweep(now);
        } catch (RuntimeException e) {
            log.warn("GPU watchdog: could not list {} pods ({}); studio pods are unchecked until the next sweep "
                    + "(their in-pod self-stop still applies)", api.provider(), e.getMessage());
            r = new Report(now, limits.dryRun(), api.configured(), 0, 0, List.of(), List.of(),
                    e.getClass().getSimpleName() + ": " + e.getMessage());
        }
        last = r;
        return r;
    }

    private Report doSweep(Instant now) {
        if (!api.configured()) {
            return new Report(now, limits.dryRun(), false, 0, 0, List.of(), List.of(), null);
        }
        List<PodApi.Pod> studio = api.listPods().stream()
                .filter(p -> p.name() != null && p.name().startsWith(limits.prefix()))
                .filter(p -> !"TERMINATED".equalsIgnoreCase(p.status()))
                .toList();
        double hourly = studio.stream().mapToDouble(PodApi.Pod::costPerHrUsd).sum();
        List<String> warnings = new ArrayList<>();
        if (studio.size() > limits.maxPods()) {
            warnings.add("%d studio pods running, limit %d".formatted(studio.size(), limits.maxPods()));
        }
        if (hourly > limits.maxHourlyUsd()) {
            warnings.add("studio pods bill $%.2f/h, limit $%.2f/h".formatted(hourly, limits.maxHourlyUsd()));
        }
        warnings.forEach(w -> log.warn("GPU watchdog: {}", w));

        List<Action> actions = new ArrayList<>();
        for (PodApi.Pod pod : studio) {
            Optional<String> reason = reason(pod, now);
            if (reason.isEmpty()) {
                continue;
            }
            actions.add(act(pod, reason.get(), now));
        }
        return new Report(now, limits.dryRun(), true, studio.size(), hourly, List.copyOf(actions),
                List.copyOf(warnings), null);
    }

    /** Why this studio pod must go, or empty if it may keep running. */
    Optional<String> reason(PodApi.Pod pod, Instant now) {
        Instant born = pod.createdAt() != null ? pod.createdAt() : pod.startedAt();
        if (born == null) {
            return Optional.of("no creation time reported");
        }
        long ageMin = Duration.between(born, now).toMinutes();
        if (ageMin >= limits.maxRuntimeMinutes()) {
            return Optional.of("over max lifetime (%d min, limit %d)".formatted(ageMin, limits.maxRuntimeMinutes()));
        }
        Optional<PodLeases.Lease> lease = leases.find(pod.id());
        if (lease.isEmpty()) {
            return ageMin >= limits.leaseGraceMinutes()
                    ? Optional.of("no lease (%d min old, grace %d)".formatted(ageMin, limits.leaseGraceMinutes()))
                    : Optional.empty();
        }
        if (lease.get().releasedAt() != null) {
            return Optional.of("lease released at " + lease.get().releasedAt());
        }
        Instant beat = lease.get().heartbeatAt();
        long idleMin = beat == null ? ageMin : Duration.between(beat, now).toMinutes();
        if (idleMin >= limits.idleMinutes()) {
            return Optional.of("idle (no heartbeat for %d min, limit %d)".formatted(idleMin, limits.idleMinutes()));
        }
        return Optional.empty();
    }

    private Action act(PodApi.Pod pod, String reason, Instant now) {
        if (limits.dryRun()) {
            log.warn("GPU watchdog DRY RUN: would terminate {} pod {} ({}): {}", api.provider(), pod.id(), pod.name(), reason);
            bookOrphan(pod, reason, now);
            return new Action(pod.id(), pod.name(), reason, false, null);
        }
        try {
            api.terminate(pod.id());
        } catch (RuntimeException e) {
            log.warn("GPU watchdog: FAILED to terminate {} pod {} ({}): {}: {}", api.provider(), pod.id(), pod.name(),
                    reason, e.getMessage());
            return new Action(pod.id(), pod.name(), reason, false, e.getMessage());
        }
        log.warn("GPU watchdog: terminated {} pod {} ({}): {}", api.provider(), pod.id(), pod.name(), reason);
        try {
            Instant billedFrom = pod.startedAt() != null ? pod.startedAt() : pod.createdAt();
            Instant booked = events.lastOrphanBooking(pod.id()).orElse(null); // dry-run sweeps already booked up to here
            if (booked != null && (billedFrom == null || booked.isAfter(billedFrom))) {
                billedFrom = booked;
            }
            double hours = billedFrom == null ? 0 : Math.max(0, Duration.between(billedFrom, now).toSeconds() / 3600.0);
            events.terminated(api.provider(), pod, reason, hours, hours * pod.costPerHrUsd());
            leases.release(pod.id());
        } catch (RuntimeException e) { // the pod is gone; a ledger hiccup must not hide that
            log.warn("GPU watchdog: pod {} terminated but its ledger/evidence row failed: {}", pod.id(), e.getMessage());
        }
        return new Action(pod.id(), pod.name(), reason, true, null);
    }

    /** Dry-run leaves the pod running, so its cost would never reach the ledger: book it in steps (rule 6). */
    private void bookOrphan(PodApi.Pod pod, String reason, Instant now) {
        try {
            Instant from = events.lastOrphanBooking(pod.id()).orElse(pod.startedAt() != null ? pod.startedAt() : pod.createdAt());
            if (from == null) {
                return;
            }
            long seconds = Duration.between(from, now).toSeconds();
            if (seconds < ORPHAN_BOOK_MIN_SECONDS) {
                return;
            }
            double hours = seconds / 3600.0;
            events.orphanBilled(api.provider(), pod, reason, hours, hours * pod.costPerHrUsd());
        } catch (RuntimeException e) {
            log.warn("GPU watchdog: could not book the running cost of orphan pod {}: {}", pod.id(), e.getMessage());
        }
    }

    /**
     * The start-side guard: any code that creates a studio pod calls this first and must not create one if it
     * throws. Refuses when the max pod count or the hourly spend limit would be passed, or the list call fails.
     */
    public void checkCanStart(double newPodHourlyUsd) {
        if (!api.configured()) {
            throw new IllegalStateException("no " + api.provider() + " API key configured");
        }
        List<PodApi.Pod> studio = api.listPods().stream()
                .filter(p -> p.name() != null && p.name().startsWith(limits.prefix()))
                .filter(p -> !"TERMINATED".equalsIgnoreCase(p.status()))
                .toList();
        if (studio.size() + 1 > limits.maxPods()) {
            throw new IllegalStateException("refusing to start a pod: %d studio pods already, limit %d (projectv.gpu.max-pods)"
                    .formatted(studio.size(), limits.maxPods()));
        }
        double hourly = studio.stream().mapToDouble(PodApi.Pod::costPerHrUsd).sum() + newPodHourlyUsd;
        if (hourly > limits.maxHourlyUsd()) {
            throw new IllegalStateException("refusing to start a pod: studio pods would bill $%.2f/h, limit $%.2f/h (projectv.gpu.max-hourly-usd)"
                    .formatted(hourly, limits.maxHourlyUsd()));
        }
    }
}
