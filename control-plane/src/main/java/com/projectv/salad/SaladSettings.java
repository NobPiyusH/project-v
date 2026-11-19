package com.projectv.salad;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.Arrays;
import java.util.List;

/**
 * Everything about the Salad route that is configuration ({@code projectv.salad.*}, env in application.properties).
 * No price is ever configured here: hourly rates come live from Salad's API (CLAUDE.md rule 7).
 *
 * @param gpuClasses preference order; the first whose name contains an entry (case-insensitive) and that has a price
 *                   for {@code priority} is used
 */
public record SaladSettings(String baseUrl, String apiKey, String org, String project, String image, String priority,
                            List<String> gpuClasses, int cpu, int memoryMb, int diskGb, int port, int readyTimeoutMinutes,
                            String workerFile, boolean watchdogDryRun, String prefix, int maxLifetimeMinutes,
                            int idleMinutes, int leaseGraceMinutes, int maxGroups, double maxHourlyUsd) {

    public boolean configured() {
        return !apiKey.isBlank() && !org.isBlank() && !project.isBlank();
    }

    @Component
    static class FromConfig {
        final SaladSettings settings;

        FromConfig(@Value("${projectv.salad.base-url:https://api.salad.com/api/public}") String baseUrl,
                   @Value("${projectv.salad.api-key:}") String apiKey,
                   @Value("${projectv.salad.org:}") String org,
                   @Value("${projectv.salad.project:default}") String project,
                   @Value("${projectv.salad.image:pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime}") String image,
                   @Value("${projectv.salad.priority:high}") String priority,
                   @Value("${projectv.salad.gpu-classes:RTX 5090,RTX 4090}") String gpuClasses,
                   @Value("${projectv.salad.cpu:8}") int cpu,
                   @Value("${projectv.salad.memory-mb:49152}") int memoryMb,
                   @Value("${projectv.salad.disk-gb:120}") int diskGb,
                   @Value("${projectv.salad.port:8000}") int port,
                   @Value("${projectv.salad.ready-timeout-minutes:30}") int readyTimeoutMinutes,
                   @Value("${projectv.salad.worker-file:workers/salad/server.py}") String workerFile,
                   @Value("${projectv.salad.watchdog.dry-run:true}") boolean dryRun,
                   @Value("${projectv.salad.group-prefix:projectv-salad-}") String prefix,
                   @Value("${projectv.salad.max-lifetime-minutes:90}") int maxLifetime,
                   @Value("${projectv.salad.idle-minutes:15}") int idle,
                   @Value("${projectv.salad.lease-grace-minutes:10}") int grace,
                   @Value("${projectv.salad.max-groups:1}") int maxGroups,
                   @Value("${projectv.salad.max-hourly-usd:1.0}") double maxHourly) {
            this.settings = new SaladSettings(baseUrl.replaceAll("/+$", ""), apiKey.strip(), org.strip(), project.strip(),
                    image, priority.strip().toLowerCase(), Arrays.stream(gpuClasses.split(",")).map(String::strip)
                    .filter(s -> !s.isEmpty()).toList(), cpu, memoryMb, diskGb, port, readyTimeoutMinutes, workerFile,
                    dryRun, prefix, maxLifetime, idle, grace, maxGroups, maxHourly);
        }
    }
}
