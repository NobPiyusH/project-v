package com.projectv.salad;

import com.projectv.gpu.PodEvents;
import com.projectv.gpu.PodLeases;
import com.projectv.gpu.PodWatchdog;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Clock;

/**
 * The Salad twin of the RunPod watchdog: the same {@link PodWatchdog} logic over {@link SaladApi}, with its own limits
 * ({@code projectv.salad.*}), only touching groups named with {@code projectv.salad.group-prefix}. Dry-run by default.
 * Swept on the same cadence ({@code projectv.gpu.watchdog.poll-seconds}); a sweep without Salad credentials does nothing.
 */
@Component
public class SaladWatchdogConfig {

    private final PodWatchdog watchdog;

    SaladWatchdogConfig(SaladApi api, PodLeases leases, PodEvents events) {
        this.watchdog = new PodWatchdog(api, leases, events, SaladSessions.limits(api.settings()), Clock.systemUTC());
    }

    public PodWatchdog watchdog() {
        return watchdog;
    }

    @Configuration
    @EnableScheduling
    @ConditionalOnProperty(name = "projectv.gpu.watchdog.scheduler", havingValue = "true", matchIfMissing = true)
    static class Tick {
        private final SaladWatchdogConfig config;

        Tick(SaladWatchdogConfig config) {
            this.config = config;
        }

        @Scheduled(initialDelay = 30_000, fixedDelayString = "#{${projectv.gpu.watchdog.poll-seconds:180} * 1000}")
        public void tick() {
            try {
                config.watchdog.sweep();
            } catch (RuntimeException e) {
                org.slf4j.LoggerFactory.getLogger(SaladWatchdogConfig.class).warn("Salad watchdog tick failed: {}", e.getMessage());
            }
        }
    }
}
