package com.projectv.dream;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;
import java.util.UUID;

@RestController
@RequestMapping("/api")
public class DreamGpuController {

    private final DreamGpu gpu;

    public DreamGpuController(DreamGpu gpu) {
        this.gpu = gpu;
    }

    /** The media worker pings this while it waits for the pod or runs jobs, so an active pod is never "idle" to the watchdog. */
    @PostMapping("/internal/gpu/{dreamId}/heartbeat")
    public Map<String, Object> heartbeat(@PathVariable UUID dreamId) {
        gpu.heartbeat(dreamId);
        return Map.of("ok", true);
    }

    @GetMapping("/gpu/broker")
    public Map<String, Object> status() {
        return Map.of("enabled", gpu.enabled());
    }
}
