package com.projectv.api;

import com.projectv.gpu.PodApi;
import com.projectv.gpu.PodLogs;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The dashboard's GPU side panel: the pods on the RunPod account and one bounded read of a pod's log. The panel only
 * calls these while it is open, every few seconds, so an idle dashboard costs nothing.
 */
@RestController
@RequestMapping("/api/gpu")
public class GpuLogsController {

    private final PodApi pods;
    private final PodLogs logs;

    public GpuLogsController(PodApi pods, PodLogs logs) {
        this.pods = pods;
        this.logs = logs;
    }

    @GetMapping("/pods")
    public Map<String, Object> pods() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("configured", pods.configured());
        if (!pods.configured()) {
            out.put("pods", List.of());
            return out;
        }
        try {
            Instant now = Instant.now();
            out.put("pods", pods.listPods().stream().map(p -> {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("id", p.id());
                m.put("name", p.name());
                m.put("status", p.status());
                m.put("costPerHrUsd", p.costPerHrUsd());
                m.put("createdAt", p.createdAt());
                double hours = p.createdAt() == null ? 0 : Duration.between(p.createdAt(), now).toSeconds() / 3600.0;
                m.put("runningMinutes", Math.round(hours * 60));
                m.put("spentUsdEstimate", Math.round(hours * p.costPerHrUsd() * 100) / 100.0);
                return m;
            }).toList());
        } catch (RuntimeException e) {
            out.put("pods", List.of());
            out.put("error", "Could not list pods: " + e.getClass().getSimpleName());
        }
        return out;
    }

    @GetMapping("/pods/{id}/logs")
    public PodLogs.Page read(@PathVariable String id, @RequestParam(required = false) String since,
                             @RequestParam(defaultValue = "150") int tail) {
        return logs.read(id, since, tail);
    }
}
