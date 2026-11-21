package com.projectv.gpu;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;

import java.time.Clock;

/**
 * Runs the GPU watchdog every {@code projectv.gpu.watchdog.poll-seconds} (default 180) while the control plane is up.
 * Without {@code RUNPOD_API_KEY} a sweep does nothing. {@code projectv.gpu.watchdog.scheduler=false} switches the
 * schedule off (tests); dry-run vs live is {@code projectv.gpu.watchdog.dry-run}.
 */
@Configuration
@EnableScheduling
public class PodWatchdogScheduler {

    @Bean
    @org.springframework.context.annotation.Primary
    PodWatchdog podWatchdog(PodApi api, PodLeases leases, PodEvents events, GpuPodLimits.FromConfig limits) {
        return new PodWatchdog(api, leases, events, limits.limits, Clock.systemUTC());
    }

    /** Vast.ai instances are swept too (same studio-label rule): the in-pod guard on Vast only ends the worker, this destroys. */
    @Bean
    PodWatchdog vastWatchdog(com.projectv.gpu.broker.VastProvider vast, PodLeases leases, PodEvents events,
                             GpuPodLimits.FromConfig limits) {
        return new PodWatchdog(vast, leases, events, limits.limits, Clock.systemUTC());
    }

    @Configuration
    @ConditionalOnProperty(name = "projectv.gpu.watchdog.scheduler", havingValue = "true", matchIfMissing = true)
    static class Tick {
        private final java.util.List<PodWatchdog> watchdogs;

        Tick(PodWatchdog only) {
            this(java.util.List.of(only));
        }

        @org.springframework.beans.factory.annotation.Autowired
        Tick(java.util.List<PodWatchdog> watchdogs) {
            this.watchdogs = watchdogs;
        }

        @Scheduled(initialDelay = 20_000, fixedDelayString = "#{${projectv.gpu.watchdog.poll-seconds:180} * 1000}")
        public void tick() {
            for (PodWatchdog watchdog : watchdogs) { // one per provider (RunPod, Vast.ai)
                try {
                    watchdog.sweep(); // already never throws; belt and braces so the schedule can't die
                } catch (RuntimeException e) {
                    org.slf4j.LoggerFactory.getLogger(PodWatchdogScheduler.class).warn("GPU watchdog tick failed: {}", e.getMessage());
                }
            }
        }
    }
}
