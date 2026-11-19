package com.projectv.salad;

import com.projectv.gpu.PodApi;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;

import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.LongConsumer;

/**
 * SaladCloud public REST API (docs.salad.com, checked 20 Nov 2026): base {@code https://api.salad.com/api/public},
 * header {@code Salad-Api-Key}. Container groups live under /organizations/{org}/projects/{project}/containers;
 * GPU classes with live prices under /organizations/{org}/gpu-classes. The key is never logged.
 * Also the {@link PodApi} the watchdog sweeps: a "pod" is a container group, its id is the group name.
 */
@Component
public class SaladApi implements PodApi {

    private static final Logger log = LoggerFactory.getLogger(SaladApi.class);

    /** A GPU class chosen for a group: its id, name and the live hourly rate for the configured priority. */
    public record Gpu(String id, String name, double hourlyUsd) {
    }

    private final SaladSettings cfg;
    private final RestClient http;

    @Autowired
    public SaladApi(SaladSettings.FromConfig from) {
        this(from.settings);
    }

    public SaladApi(SaladSettings cfg) {
        this.cfg = cfg;
        SimpleClientHttpRequestFactory f = new SimpleClientHttpRequestFactory();
        f.setConnectTimeout(10_000);
        f.setReadTimeout(60_000);
        this.http = RestClient.builder().requestFactory(f).build();
    }

    public SaladSettings settings() {
        return cfg;
    }

    @Override
    public String provider() {
        return "salad";
    }

    @Override
    public boolean configured() {
        return cfg.configured();
    }

    private String projectBase() {
        return cfg.baseUrl() + "/organizations/" + cfg.org() + "/projects/" + cfg.project() + "/containers";
    }

    // ------------------------------------------------------------------------------------------- GPU classes

    /** Live GPU classes with prices (read-only). */
    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> gpuClasses() {
        Map<String, Object> r = http.get().uri(cfg.baseUrl() + "/organizations/" + cfg.org() + "/gpu-classes")
                .header("Salad-Api-Key", cfg.apiKey()).retrieve().body(Map.class);
        Object items = r == null ? null : r.get("items");
        return items instanceof List<?> l ? (List<Map<String, Object>>) l : List.of();
    }

    /** The first configured GPU class (preference order) that has a live price for the configured priority. */
    public Gpu pickGpu() {
        List<Map<String, Object>> classes = gpuClasses();
        for (String want : cfg.gpuClasses()) {
            for (Map<String, Object> c : classes) {
                String name = String.valueOf(c.get("name"));
                if (!name.toLowerCase().contains(want.toLowerCase())) {
                    continue;
                }
                if (c.get("prices") instanceof List<?> prices) {
                    for (Object o : prices) {
                        if (o instanceof Map<?, ?> p && cfg.priority().equalsIgnoreCase(String.valueOf(p.get("priority")))) {
                            try {
                                return new Gpu(String.valueOf(c.get("id")), name, Double.parseDouble(String.valueOf(p.get("price"))));
                            } catch (NumberFormatException e) {
                                log.warn("Salad GPU class {} has an unreadable price", name);
                            }
                        }
                    }
                }
            }
        }
        throw new IllegalStateException("No Salad GPU class matching " + cfg.gpuClasses() + " has a live price for priority '"
                + cfg.priority() + "' (projectv.salad.gpu-classes / priority)");
    }

    // ------------------------------------------------------------------------------------------- container groups

    /** Creates (and autostarts) a one-replica group. Returns the created group (name, networking.dns, ...). */
    @SuppressWarnings("unchecked")
    public Map<String, Object> create(String name, Gpu gpu, Map<String, String> env, List<String> command) {
        Map<String, Object> resources = new LinkedHashMap<>();
        resources.put("cpu", cfg.cpu());
        resources.put("memory", cfg.memoryMb());
        resources.put("gpu_classes", List.of(gpu.id()));
        resources.put("storage_amount", cfg.diskGb() * 1024L * 1024L * 1024L);
        resources.put("shm_size", 1024);
        Map<String, Object> container = new LinkedHashMap<>();
        container.put("image", cfg.image());
        container.put("command", command);
        container.put("environment_variables", env);
        container.put("priority", cfg.priority());
        container.put("resources", resources);
        Map<String, Object> net = new LinkedHashMap<>();
        net.put("protocol", "http");
        net.put("port", cfg.port());
        net.put("auth", true);
        net.put("load_balancer", "round_robin");
        net.put("single_connection_limit", false);
        net.put("client_request_timeout", 100000);
        net.put("server_response_timeout", 100000);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("name", name);
        body.put("display_name", name);
        body.put("autostart_policy", true);
        body.put("replicas", 1);
        body.put("restart_policy", "on_failure");
        body.put("container", container);
        body.put("networking", net);
        body.put("readiness_probe", Map.of("http", Map.of("path", "/health", "port", cfg.port(), "scheme", "http"),
                "initial_delay_seconds", 0, "period_seconds", 5, "failure_threshold", 20, "success_threshold", 1,
                "timeout_seconds", 5));
        Map<String, Object> r = http.post().uri(projectBase()).header("Salad-Api-Key", cfg.apiKey())
                .contentType(MediaType.APPLICATION_JSON).body(body).retrieve().body(Map.class);
        return r == null ? Map.of("name", name) : r;
    }

    @SuppressWarnings("unchecked")
    public Optional<Map<String, Object>> get(String name) {
        try {
            return Optional.ofNullable(http.get().uri(projectBase() + "/{n}", name).header("Salad-Api-Key", cfg.apiKey())
                    .retrieve().body(Map.class));
        } catch (RestClientResponseException e) {
            if (e.getStatusCode().value() == 404) {
                return Optional.empty();
            }
            throw e;
        }
    }

    public void start(String name) {
        http.post().uri(projectBase() + "/{n}/start", name).header("Salad-Api-Key", cfg.apiKey()).retrieve().toBodilessEntity();
    }

    public void stop(String name) {
        http.post().uri(projectBase() + "/{n}/stop", name).header("Salad-Api-Key", cfg.apiKey()).retrieve().toBodilessEntity();
    }

    /** Deletes the group; a group that is already gone (404) counts as deleted. */
    public void delete(String name) {
        try {
            http.delete().uri(projectBase() + "/{n}", name).header("Salad-Api-Key", cfg.apiKey()).retrieve().toBodilessEntity();
        } catch (RestClientResponseException e) {
            if (e.getStatusCode().value() != 404) {
                throw e;
            }
        }
    }

    public static String status(Map<String, Object> group) {
        return group.get("current_state") instanceof Map<?, ?> s ? String.valueOf(s.get("status")) : "unknown";
    }

    /** The Container Gateway URL (https, from networking.dns), empty until Salad has assigned one. */
    public static Optional<String> gatewayUrl(Map<String, Object> group) {
        if (group.get("networking") instanceof Map<?, ?> n && n.get("dns") instanceof String dns && !dns.isBlank()) {
            return Optional.of("https://" + dns);
        }
        return Optional.empty();
    }

    /**
     * Waits until the group reports {@code running} (the worker's own /health says when the weights are in).
     * Throws when it fails/stops or after the timeout; the caller deletes the group.
     */
    public Map<String, Object> waitRunning(String name, Duration timeout, LongConsumer sleepMillis) {
        Instant end = Instant.now().plus(timeout);
        while (true) {
            Map<String, Object> g = get(name).orElseThrow(() -> new IllegalStateException("Salad group " + name + " disappeared"));
            String st = status(g);
            if ("running".equalsIgnoreCase(st)) {
                return g;
            }
            if ("failed".equalsIgnoreCase(st) || "stopped".equalsIgnoreCase(st)) {
                throw new IllegalStateException("Salad group " + name + " is " + st);
            }
            if (Instant.now().isAfter(end)) {
                throw new IllegalStateException("Salad group " + name + " not running after " + timeout.toMinutes() + " min (" + st + ")");
            }
            sleepMillis.accept(5000);
        }
    }

    // ------------------------------------------------------------------------------------------- PodApi (watchdog)

    @Override
    @SuppressWarnings("unchecked")
    public List<Pod> listPods() {
        Map<String, Object> r = http.get().uri(projectBase()).header("Salad-Api-Key", cfg.apiKey()).retrieve().body(Map.class);
        Object items = r == null ? null : (r.get("items") != null ? r.get("items") : r.get("container_groups"));
        double rate = 0;
        try {
            rate = pickGpu().hourlyUsd(); // live rate of the class we would use; the watchdog's hourly check is a bound
        } catch (RuntimeException e) {
            log.debug("Salad watchdog: no live rate ({})", e.getMessage());
        }
        List<Pod> out = new ArrayList<>();
        if (items instanceof List<?> l) {
            for (Object o : l) {
                if (o instanceof Map<?, ?> g) {
                    Map<String, Object> m = (Map<String, Object>) g;
                    out.add(new Pod(String.valueOf(m.get("name")), String.valueOf(m.get("name")), status(m), rate,
                            instant(m.get("create_time")), instant(m.get("create_time"))));
                }
            }
        }
        return out;
    }

    @Override
    public void terminate(String groupName) {
        delete(groupName);
    }

    private static Instant instant(Object o) {
        if (!(o instanceof String s) || s.isBlank()) {
            return null;
        }
        try {
            return Instant.parse(s);
        } catch (RuntimeException e) {
            return OffsetDateTime.parse(s).toInstant();
        }
    }
}
