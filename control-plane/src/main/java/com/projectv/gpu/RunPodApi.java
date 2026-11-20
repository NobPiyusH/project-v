package com.projectv.gpu;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * RunPod REST v2 (checked 19 Nov 2026 at docs.runpod.io/api-reference-v2): {@code GET /v2/pods} (cursor-paginated,
 * {@code pods[]} with {@code id, name, status, cost} (USD/hour), {@code createdAt, startedAt}) and
 * {@code DELETE /v2/pods/{id}} (terminate, 204). The key is sent as a bearer token and never logged.
 */
@Component
@org.springframework.context.annotation.Primary // the watchdog's PodApi; broker adapters (GpuProvider) are PodApi beans too
public class RunPodApi implements PodApi {

    private final String baseUrl;
    private final String apiKey;
    private final RestClient http;

    @Autowired
    public RunPodApi(@Value("${projectv.runpod.base-url:https://api.runpod.io/v2}") String baseUrl,
                     @Value("${projectv.runpod.api-key:}") String apiKey) {
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.apiKey = apiKey == null ? "" : apiKey.strip();
        SimpleClientHttpRequestFactory f = new SimpleClientHttpRequestFactory();
        f.setConnectTimeout(10_000);
        f.setReadTimeout(30_000);
        this.http = RestClient.builder().requestFactory(f).build();
    }

    @Override
    public String provider() {
        return "runpod";
    }

    @Override
    public boolean configured() {
        return !apiKey.isEmpty();
    }

    @Override
    @SuppressWarnings("unchecked")
    public List<Pod> listPods() {
        List<Pod> out = new ArrayList<>();
        String cursor = null;
        for (int page = 0; page < 50; page++) { // 50 pages x 1000 pods: a hard stop against a looping cursor
            String uri = baseUrl + "/pods?limit=1000" + (cursor == null ? "" : "&cursor=" + encode(cursor));
            Map<String, Object> r = http.get().uri(java.net.URI.create(uri))
                    .header("Authorization", "Bearer " + apiKey)
                    .retrieve().body(Map.class);
            if (r == null || !(r.get("pods") instanceof List<?> pods)) {
                throw new IllegalStateException("RunPod list pods returned no pods array");
            }
            for (Object o : pods) {
                if (o instanceof Map<?, ?> p) {
                    out.add(pod((Map<String, Object>) p));
                }
            }
            Map<String, Object> pg = r.get("pagination") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
            if (!Boolean.TRUE.equals(pg.get("hasNextPage")) || !(pg.get("nextCursor") instanceof String next)) {
                return out;
            }
            cursor = next;
        }
        throw new IllegalStateException("RunPod list pods: too many pages");
    }

    @Override
    public void terminate(String podId) {
        http.delete().uri(baseUrl + "/pods/{id}", podId)
                .header("Authorization", "Bearer " + apiKey)
                .retrieve().toBodilessEntity();
    }

    static Pod pod(Map<String, Object> p) {
        return new Pod(str(p.get("id")), str(p.get("name")), str(p.get("status")),
                p.get("cost") instanceof Number n ? n.doubleValue() : 0,
                instant(p.get("createdAt")), instant(p.get("startedAt")));
    }

    private static String str(Object o) {
        return o == null ? "" : o.toString();
    }

    private static Instant instant(Object o) {
        if (!(o instanceof String s) || s.isBlank()) {
            return null;
        }
        try {
            return Instant.parse(s);
        } catch (RuntimeException e) {
            return java.time.OffsetDateTime.parse(s).toInstant();
        }
    }

    private static String encode(String s) {
        return java.net.URLEncoder.encode(s, java.nio.charset.StandardCharsets.UTF_8);
    }
}
