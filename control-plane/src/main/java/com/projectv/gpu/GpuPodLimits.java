package com.projectv.gpu;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * Safety limits for rented GPU pods (CLAUDE.md rules 7 and 8), all from config ({@code projectv.gpu.*}), none
 * hard-coded in the watchdog. Prices are never config: a pod's hourly rate is read live from the provider's API.
 *
 * @param prefix            only pods whose name starts with this are ever touched (owner's other pods are left alone)
 * @param maxRuntimeMinutes hard lifetime cap from the pod's creation, whatever it is doing
 * @param idleMinutes       a leased pod whose heartbeat is older than this is idle and gets terminated
 * @param leaseGraceMinutes a new pod may run this long before its lease must exist (covers the create → lease gap)
 * @param maxPods           at most this many studio pods at once; {@link PodWatchdog#checkCanStart} refuses more
 * @param maxHourlyUsd      the sum of the studio pods' live hourly rates may not go above this
 * @param dryRun            true: log what would be terminated, terminate nothing (default false: live since 21 Nov 2026)
 */
public record GpuPodLimits(String prefix, int maxRuntimeMinutes, int idleMinutes, int leaseGraceMinutes, int maxPods,
                           double maxHourlyUsd, boolean dryRun) {

    public GpuPodLimits {
        if (prefix == null || prefix.isBlank()) {
            throw new IllegalArgumentException("projectv.gpu.pod-prefix must not be empty (it would match every pod)");
        }
        if (maxRuntimeMinutes <= 0 || idleMinutes <= 0 || leaseGraceMinutes < 0 || maxPods < 0 || maxHourlyUsd < 0) {
            throw new IllegalArgumentException("projectv.gpu.* limits must be positive");
        }
    }

    /** Spring bean built from application.properties / .env. */
    @Component
    public static class FromConfig {
        public final GpuPodLimits limits;

        FromConfig(@Value("${projectv.gpu.pod-prefix:projectv-}") String prefix,
                   @Value("${projectv.gpu.max-runtime-minutes:120}") int maxRuntime,
                   @Value("${projectv.gpu.idle-minutes:15}") int idle,
                   @Value("${projectv.gpu.lease-grace-minutes:10}") int grace,
                   @Value("${projectv.gpu.max-pods:2}") int maxPods,
                   @Value("${projectv.gpu.max-hourly-usd:2.0}") double maxHourlyUsd,
                   @Value("${projectv.gpu.watchdog.dry-run:false}") boolean dryRun) {
            this.limits = new GpuPodLimits(prefix, maxRuntime, idle, grace, maxPods, maxHourlyUsd, dryRun);
        }
    }
}
