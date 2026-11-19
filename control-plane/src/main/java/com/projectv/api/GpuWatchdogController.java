package com.projectv.api;

import com.projectv.gpu.PodEvents;
import com.projectv.gpu.PodLeases;
import com.projectv.gpu.PodWatchdog;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.Map;

/** GPU watchdog status for the dashboard: mode, limits, the last sweep, recent leases and terminations. */
@RestController
@RequestMapping("/api/gpu")
public class GpuWatchdogController {

    private final PodWatchdog watchdog;
    private final PodLeases.Jdbc leases;
    private final PodEvents.Jdbc events;

    public GpuWatchdogController(PodWatchdog watchdog, PodLeases.Jdbc leases, PodEvents.Jdbc events) {
        this.watchdog = watchdog;
        this.leases = leases;
        this.events = events;
    }

    @GetMapping("/watchdog")
    public Map<String, Object> status() {
        Map<String, Object> out = new HashMap<>();
        out.put("limits", watchdog.limits());
        out.put("lastSweep", watchdog.last()); // null until the first sweep
        out.put("leases", leases.recent(20));
        out.put("terminations", events.recent(20));
        return out;
    }
}
