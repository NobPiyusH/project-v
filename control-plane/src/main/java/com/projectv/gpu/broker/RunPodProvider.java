package com.projectv.gpu.broker;

import com.projectv.gpu.RunPodApi;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * RunPod adapter on REST v2. Listing and terminating reuse {@link RunPodApi} (the watchdog's client). Stock and price come
 * from {@code GET /gpu-types/{id}?include=AVAILABILITY&product=POD&cloud=SECURE|COMMUNITY} (the REST form of the
 * get-gpu-type read tool, checked live 20 Nov 2026: {@code availability} HIGH/MEDIUM/LOW/NONE, {@code price.secure/community}
 * USD per hour). Create uses the v2 body of docs/wan-fp8-4090-runbook.md (branch claude/wan-fp8-bench-257933).
 * NOT yet exercised against the live API (no spend allowed): the first paid test must confirm create/status.
 * Secure cloud is tried when Community has no stock; the broker's wallet decides whether the higher price fits.
 */
@Component
public class RunPodProvider implements GpuProvider {

    private final RunPodApi base;
    private final String baseUrl;
    private final String apiKey;
    private final RestClient http;

    @Autowired
    public RunPodProvider(RunPodApi base, @Value("${projectv.runpod.base-url:https://api.runpod.io/v2}") String baseUrl,
                          @Value("${projectv.runpod.api-key:}") String apiKey) {
        this(base, baseUrl, apiKey, client());
    }

    RunPodProvider(RunPodApi base, String baseUrl, String apiKey, RestClient http) {
        this.base = base;
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.apiKey = apiKey == null ? "" : apiKey.strip();
        this.http = http;
    }

    private static RestClient client() {
        SimpleClientHttpRequestFactory f = new SimpleClientHttpRequestFactory();
        f.setConnectTimeout(10_000);
        f.setReadTimeout(30_000);
        return RestClient.builder().requestFactory(f).build();
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
    public boolean enabledByDefault() {
        return true;
    }

    @Override
    public List<Pod> listPods() {
        return base.listPods();
    }

    @Override
    public void terminate(String podId) {
        base.terminate(podId);
    }

    @Override
    public Quote quote(GpuSpec spec) {
        List<Quote> all = quotes(spec);
        return all.isEmpty() ? Quote.unavailable("runpod", lastWhy) : all.getFirst();
    }

    private volatile String lastWhy = "no GPU type in stock";

    /** Every GPU in stock, Community and Secure, cheapest first. */
    @Override
    @SuppressWarnings("unchecked")
    public List<Quote> quotes(GpuSpec spec) {
        List<Quote> out = new java.util.ArrayList<>();
        StringBuilder why = new StringBuilder();
        for (String cloud : List.of("COMMUNITY", "SECURE")) {
            for (String gpu : spec.gpuPreference()) {
                Map<String, Object> r;
                try {
                    r = http.get().uri(java.net.URI.create(baseUrl + "/gpu-types/" + enc(gpu)
                                    + "?include=AVAILABILITY&product=POD&cloud=" + cloud))
                            .header("Authorization", "Bearer " + apiKey).retrieve().body(Map.class);
                } catch (RuntimeException e) {
                    why.append(gpu).append('/').append(cloud).append(": ").append(e.getMessage()).append("; ");
                    continue;
                }
                if (r == null) {
                    continue;
                }
                String stock = String.valueOf(r.getOrDefault("availability", "NONE")).toUpperCase();
                if (r.get("memory") instanceof Number m && m.intValue() < spec.minVramGb()) {
                    continue;
                }
                Map<String, Object> price = r.get("price") instanceof Map<?, ?> m ? (Map<String, Object>) m : Map.of();
                Object p = price.get(cloud.toLowerCase());
                if (!(p instanceof Number n) || "NONE".equals(stock) || "OUT".equals(stock) || "UNAVAILABLE".equals(stock)) {
                    why.append(gpu).append('/').append(cloud).append(": ").append(stock).append("; ");
                    continue;
                }
                out.add(new Quote("runpod", true, stock, gpu + "|" + cloud, n.doubleValue(), ""));
            }
        }
        out.sort(java.util.Comparator.comparingDouble(Quote::hourlyUsd));
        lastWhy = why.isEmpty() ? "no GPU type in stock" : why.toString();
        return out;
    }

    /** The create body (also what the tests check). {@code quote.gpu} is {@code "<gpu id>|<COMMUNITY|SECURE>"}. */
    static Map<String, Object> createBody(CreateRequest req) {
        String[] g = req.quote().gpu().split("\\|");
        Map<String, Object> gpu = new LinkedHashMap<>();
        gpu.put("id", g[0]);
        gpu.put("count", 1);
        gpu.put("minCudaVersion", "12.8");
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("name", req.name());
        body.put("cloud", g.length > 1 ? g[1] : "SECURE");
        body.put("gpu", gpu);
        body.put("image", req.image());
        body.put("disk", req.diskGb());
        body.put("cmd", req.bootArgv());
        if (req.env() != null && !req.env().isEmpty()) {
            body.put("env", req.env());
        }
        body.put("ports", List.of(req.port() + "/http"));
        if (req.volumeId() != null && !req.volumeId().isBlank()) {
            body.put("volumeId", req.volumeId());
        }
        return body;
    }

    @Override
    @SuppressWarnings("unchecked")
    public Instance create(CreateRequest req) {
        if (!req.name().startsWith("projectv-")) {
            throw new IllegalArgumentException("pod name must carry the studio prefix so the watchdog can see it");
        }
        Map<String, Object> r = http.post().uri(baseUrl + "/pods").contentType(MediaType.APPLICATION_JSON)
                .header("Authorization", "Bearer " + apiKey).body(createBody(req)).retrieve().body(Map.class);
        if (r == null || r.get("id") == null) {
            throw new IllegalStateException("RunPod create pod returned no id");
        }
        String id = String.valueOf(r.get("id"));
        double hourly = r.get("cost") instanceof Number n ? n.doubleValue() : req.quote().hourlyUsd();
        return new Instance("runpod", id, req.name(), hourly, req.quote().gpu().split("\\|")[0], proxyUrl(id, req.port()));
    }

    @Override
    @SuppressWarnings("unchecked")
    public InstanceStatus status(String instanceId) {
        try {
            Map<String, Object> r = http.get().uri(baseUrl + "/pods/{id}", instanceId)
                    .header("Authorization", "Bearer " + apiKey).retrieve().body(Map.class);
            String st = r == null ? "" : String.valueOf(r.getOrDefault("status", ""));
            State s = switch (st.toUpperCase()) {
                case "RUNNING" -> State.RUNNING;
                case "TERMINATED", "EXITED", "" -> State.GONE;
                default -> State.STARTING;
            };
            return new InstanceStatus(s, null, st);
        } catch (org.springframework.web.client.HttpClientErrorException.NotFound e) {
            return new InstanceStatus(State.GONE, null, "404");
        }
    }

    @Override
    public String endpointFor(String instanceId, int port) {
        return proxyUrl(instanceId, port);
    }

    /** RunPod's HTTP proxy address for an exposed port. */
    static String proxyUrl(String podId, int port) {
        return "https://" + podId + "-" + port + ".proxy.runpod.net";
    }

    private static String enc(String s) {
        return java.net.URLEncoder.encode(s, java.nio.charset.StandardCharsets.UTF_8).replace("+", "%20");
    }
}
