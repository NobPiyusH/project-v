package com.projectv.salad;

import com.projectv.dream.DreamService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/** Salad route endpoints: the worker's lease heartbeat (same machine) and the watchdog status. Never exposes the key. */
@RestController
@RequestMapping("/api")
public class SaladController {

    private final DreamService dreams;
    private final SaladWatchdogConfig watchdog;
    private final SaladSettings.FromConfig settings;

    public SaladController(DreamService dreams, SaladWatchdogConfig watchdog, SaladSettings.FromConfig settings) {
        this.dreams = dreams;
        this.watchdog = watchdog;
        this.settings = settings;
    }

    /** The media worker calls this while it waits for the group or runs jobs, so an active group is never "idle". */
    @PostMapping("/internal/salad/{dreamId}/heartbeat")
    public Map<String, Object> heartbeat(@PathVariable UUID dreamId) {
        dreams.saladHeartbeat(dreamId);
        return Map.of("ok", true);
    }

    @GetMapping("/gpu/salad")
    public Map<String, Object> status() {
        Map<String, Object> out = new HashMap<>();
        out.put("enabled", dreams.usesSalad());
        out.put("configured", settings.settings.configured());
        out.put("priority", settings.settings.priority());
        out.put("limits", watchdog.watchdog().limits());
        out.put("lastSweep", watchdog.watchdog().last());
        return out;
    }
}
