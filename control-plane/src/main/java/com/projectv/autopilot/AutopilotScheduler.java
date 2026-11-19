package com.projectv.autopilot;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;

/**
 * Wakes the autopilot once a minute while the control plane runs. Whether anything happens is decided by the owner's
 * settings (off by default). If the laptop was off at the start time, today's run starts when the backend is next up.
 * Tests switch it off with {@code projectv.autopilot.scheduler=false}.
 */
@Configuration
@EnableScheduling
@ConditionalOnProperty(name = "projectv.autopilot.scheduler", havingValue = "true", matchIfMissing = true)
public class AutopilotScheduler {

    private static final Logger log = LoggerFactory.getLogger(AutopilotScheduler.class);

    private final AutopilotService autopilot;

    public AutopilotScheduler(AutopilotService autopilot) {
        this.autopilot = autopilot;
    }

    @Scheduled(initialDelay = 30_000, fixedDelay = 60_000)
    public void tick() {
        try {
            autopilot.tick();
        } catch (RuntimeException e) { // never let one bad minute stop the schedule
            log.warn("Autopilot tick failed: {}", e.getMessage());
        }
    }
}
