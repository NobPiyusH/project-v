package com.projectv.gpu.broker;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestClient;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;

/**
 * Vast.ai adapter (REST v0, {@code https://console.vast.ai/api/v0}, bearer key). Vast hosts are other people's machines, so
 * the design is defensive (docs/m3-gpu-router-runbook.md, "Vast.ai"):
 * <ul>
 *   <li><b>No secret goes to the instance.</b> The create body holds the image, the studio label, the guard command and the
 *       worker env (boot payload, port, root, the per-rental worker token). Never this key, the RunPod key, S3 or Gemini keys.
 *       SSH is off (launch mode {@code args}), so no SSH key is registered either.</li>
 *   <li><b>Teardown is ours.</b> {@link #terminate} calls {@code DELETE /instances/{id}/}; the in-pod guard only ends the worker
 *       ({@link com.projectv.gpu.PodSelfStop#wrapExit}). The watchdog lists this provider's instances by the studio label.</li>
 *   <li><b>Offers:</b> on-demand, verified, reliable, 1 GPU, enough VRAM, RAM, disk and download speed, a direct public port,
 *       price from the live offer plus the host's own internet price per GB (see {@link VastConfig}).</li>
 * </ul>
 * Shapes checked 21 Nov 2026: the offer search ({@code POST /bundles/}) live and read-only with the owner's key (200, fields in
 * {@link #offer}); create / show / destroy against docs.vast.ai (openapi). NOT checked live: create, show instance, destroy and
 * the instance list (see {@link #listPods}). 22 Nov 2026: with a 2FA account, instance and account calls need a 2FA session key
 * ({@code vastai tfa login}, saved in {@code ~/.config/vastai/vast_tfa_key}); the normal key is used only for offer search.
 * Instance list/show use {@code GET /api/v1/instances/} (the v0 list is gone: 410); create and destroy stay {@code /api/v0}
 * (v1 has no by-id route: 404; the docs and live error shapes show v0 create/destroy are current).
 */
@Component
public class VastProvider implements GpuProvider {

    private static final Logger log = LoggerFactory.getLogger(VastProvider.class);
    static final String LABEL_PREFIX = "projectv-";
    private static final Pattern SAFE_ENV = Pattern.compile("[A-Za-z0-9+/=._:@-]*");

    private final VastConfig cfg;
    private final String baseUrl;
    /** The instance list lives under /api/v1 (the v0 list answers 410); create and destroy are still /api/v0. */
    private final String v1Url;
    private final String apiKey;
    /** Vast accounts with 2FA need a 2FA session key for instance and account calls (the normal key only works for offer search). */
    private final java.util.function.Supplier<String> sessionKey;
    private final RestClient http;
    private final long pollMillis;
    /** First time each instance was seen (or created here): the fallback when Vast reports no start date. */
    private final Map<String, Instant> firstSeen = new ConcurrentHashMap<>();
    private volatile String lastWhy = "no offer passes the filter";
    private volatile GpuSpec lastSpec;
    /** The worker port of the last create (status() has no port argument). */
    private volatile int workerPort = 8000;
    private volatile long cachedAt;
    private volatile List<Quote> cached = List.of();

    @Autowired
    public VastProvider(VastConfig cfg, @Value("${projectv.vast.base-url:https://console.vast.ai/api/v0}") String baseUrl,
                        @Value("${projectv.vast.api-key:}") String apiKey,
                        @Value("${projectv.vast.session-key:}") String sessionKey) {
        this(cfg, baseUrl, apiKey, sessionKeySource(sessionKey, Path.of(System.getProperty("user.home"), ".config", "vastai", "vast_tfa_key")),
                client(), cfg.pollSeconds() * 1000L);
    }

    VastProvider(VastConfig cfg, String baseUrl, String apiKey, java.util.function.Supplier<String> sessionKey, RestClient http, long pollMillis) {
        this.sessionKey = sessionKey;
        this.cfg = cfg;
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.v1Url = this.baseUrl.endsWith("/api/v0") ? this.baseUrl.substring(0, this.baseUrl.length() - 2) + "v1" : this.baseUrl;
        this.apiKey = apiKey == null ? "" : apiKey.strip();
        this.http = http;
        this.pollMillis = pollMillis;
    }

    /**
     * The 2FA session key: the setting if given, else the file {@code vastai tfa login} writes. The file is read on every call
     * so a fresh login is picked up without a restart. The key is never logged.
     */
    static java.util.function.Supplier<String> sessionKeySource(String setting, Path file) {
        String fixed = setting == null ? "" : setting.strip();
        return () -> {
            if (!fixed.isEmpty()) {
                return fixed;
            }
            try {
                return Files.isRegularFile(file) ? Files.readString(file).strip() : "";
            } catch (java.io.IOException | RuntimeException e) {
                return "";
            }
        };
    }

    static final String SESSION_HELP = "Vast 2FA session expired: run `vastai tfa login --method-type totp -c <code>`";

    private static RestClient client() {
        SimpleClientHttpRequestFactory f = new SimpleClientHttpRequestFactory();
        f.setConnectTimeout(10_000);
        f.setReadTimeout(30_000);
        return RestClient.builder().requestFactory(f).defaultHeader("User-Agent", "projectv-studio/1.0").build();
    }

    @Override
    public String provider() {
        return "vast";
    }

    @Override
    public boolean configured() {
        return !apiKey.isEmpty() || !sessionKey.get().isEmpty();
    }

    /** Key for offer search: the normal key (falls back to the session key when only that is set). */
    private String searchKey() {
        return apiKey.isEmpty() ? sessionKey.get() : apiKey;
    }

    /** Key for instance and account calls: the 2FA session key; the normal key only when no session key exists (accounts without 2FA). */
    private String accountKey() {
        String k = sessionKey.get();
        return k.isEmpty() ? apiKey : k;
    }

    @Override
    public boolean enabledByDefault() {
        return true;
    }

    /** No provider key on the machine: the guard only ends the worker; the control plane destroys the instance. */
    @Override
    public List<String> guard(List<String> jobArgv, int maxRuntimeMinutes, int idleMinutes, Instant createdAt) {
        return com.projectv.gpu.PodSelfStop.wrapExit(jobArgv, maxRuntimeMinutes, idleMinutes, createdAt);
    }

    // ------------------------------------------------------------------------------------------------ search

    @Override
    public Quote quote(GpuSpec spec) {
        List<Quote> all = quotes(spec);
        return all.isEmpty() ? Quote.unavailable("vast", lastWhy) : all.getFirst();
    }

    /** The cheapest passing offer for each allowed GPU model, cheapest first. Results are reused for a few seconds. */
    @Override
    public List<Quote> quotes(GpuSpec spec) {
        lastSpec = spec;
        long now = System.currentTimeMillis();
        if (now - cachedAt < cfg.cacheSeconds() * 1000L && !cached.isEmpty()) {
            return cached;
        }
        List<Quote> fresh = search(spec);
        cached = fresh;
        cachedAt = fresh.isEmpty() ? 0 : now;
        return fresh;
    }

    @SuppressWarnings("unchecked")
    private List<Quote> search(GpuSpec spec) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("verified", Map.of("eq", true));
        body.put("rentable", Map.of("eq", true));
        body.put("rented", Map.of("eq", false));
        body.put("num_gpus", Map.of("eq", 1));
        body.put("gpu_ram", Map.of("gte", spec.minVramGb() * 1000)); // MB in the REST API
        body.put("cpu_ram", Map.of("gte", spec.minRamGb() * 1000));
        body.put("reliability", Map.of("gte", cfg.minReliability()));
        body.put("direct_port_count", Map.of("gte", 1));
        body.put("disk_space", Map.of("gte", cfg.diskGb()));
        body.put("inet_down", Map.of("gte", cfg.minDownMbps()));
        body.put("gpu_name", Map.of("in", cfg.gpuNames()));
        body.put("type", "on-demand");
        body.put("allocated_storage", cfg.diskGb()); // dph_total then includes the disk we will rent
        body.put("order", List.of(List.of("dph_total", "asc")));
        body.put("limit", 40);
        Map<String, Object> r = call(false, () -> http.post().uri(baseUrl + "/bundles/").contentType(MediaType.APPLICATION_JSON)
                .header("Authorization", "Bearer " + searchKey()).body(body).retrieve().body(Map.class));
        List<Quote> out = new ArrayList<>();
        Map<String, String> why = new LinkedHashMap<>();
        if (r != null && r.get("offers") instanceof List<?> offers) {
            Map<String, Quote> cheapest = new LinkedHashMap<>();
            for (Object o : offers) {
                if (!(o instanceof Map<?, ?> m)) {
                    continue;
                }
                Map<String, Object> offer = (Map<String, Object>) m;
                String reject = offerProblem(offer, spec);
                if (reject != null) {
                    why.merge(reject, "1", (a, b) -> String.valueOf(Integer.parseInt(a) + 1));
                    continue;
                }
                Quote q = quoteOf(offer);
                cheapest.merge(String.valueOf(offer.get("gpu_name")), q, (a, b) -> a.hourlyUsd() <= b.hourlyUsd() ? a : b);
            }
            out.addAll(cheapest.values());
        }
        out.sort(Comparator.comparingDouble(Quote::hourlyUsd));
        lastWhy = !out.isEmpty() ? "" : why.isEmpty() ? "no on-demand offer in stock that fits the filter"
                : "every offer was rejected: " + why.entrySet().stream().map(e -> e.getKey() + " x" + e.getValue()).toList();
        return out;
    }

    /** Why this offer must not be rented, or null. Also checked here (not only in the query) so a server that ignores a filter cannot slip an offer through. */
    private String offerProblem(Map<String, Object> o, GpuSpec spec) {
        if (!"verified".equals(String.valueOf(o.get("verification")))) {
            return "not a verified host";
        }
        if (Boolean.TRUE.equals(o.get("is_bid")) || Boolean.TRUE.equals(o.get("rented")) || Boolean.FALSE.equals(o.get("rentable"))) {
            return "not an on-demand offer you can rent";
        }
        if (num(o.get("num_gpus")) != 1) {
            return "not exactly 1 GPU";
        }
        if (!cfg.gpuNames().contains(String.valueOf(o.get("gpu_name")))) {
            return "GPU model not allowed";
        }
        if (num(o.get("gpu_ram")) < spec.minVramGb() * 1000.0) {
            return "too little VRAM";
        }
        if (num(o.get("cpu_ram")) < spec.minRamGb() * 1000.0) {
            return "too little RAM";
        }
        if (num(o.get("reliability")) < cfg.minReliability()) {
            return "host reliability too low";
        }
        if (num(o.get("disk_space")) < cfg.diskGb()) {
            return "not enough free disk";
        }
        if (num(o.get("inet_down")) < cfg.minDownMbps()) {
            return "download speed too low";
        }
        if (num(o.get("direct_port_count")) < 1 || str(o.get("public_ipaddr")).isBlank()) {
            return "no direct public port";
        }
        if (!(o.get("dph_total") instanceof Number) || num(o.get("dph_total")) <= 0) {
            return "no live price";
        }
        if (bandwidthUsd(o) > cfg.maxBandwidthUsd()) {
            return "host internet price too high";
        }
        return null;
    }

    /** What the host charges for the data this rental moves (Vast bills internet per GB, set by each host; some charge a lot). */
    double bandwidthUsd(Map<String, Object> o) {
        return num(o.get("inet_down_cost")) * cfg.downloadGb() + num(o.get("inet_up_cost")) * cfg.uploadGb();
    }

    private Quote quoteOf(Map<String, Object> o) {
        double hourly = num(o.get("dph_total")) + bandwidthUsd(o) / cfg.amortizeHours();
        String gpu = String.valueOf(o.get("gpu_name"));
        String country = str(o.get("geolocation"));
        String machine = String.valueOf((long) num(o.get("machine_id")));
        // "<gpu>|<offer id>|<country>|m<machine>": the broker's key and RoutingDecision rows show where the GPU is (strangers' machines)
        String key = gpu + "|" + (long) num(o.get("id")) + "|" + country.replace('|', '/') + "|m" + machine;
        String detail = "offer %d, host %d machine %s, %s, reliability %.3f, %.0f Mb/s down, GPU+disk $%.3f/h + ~$%.2f internet (%.0f GB in, %.0f GB out)"
                .formatted((long) num(o.get("id")), (long) num(o.get("host_id")), machine, country, num(o.get("reliability")),
                        num(o.get("inet_down")), num(o.get("dph_total")), bandwidthUsd(o), cfg.downloadGb(), cfg.uploadGb());
        return new Quote("vast", true, "ON-DEMAND", key, hourly, detail);
    }

    // ------------------------------------------------------------------------------------------------ create

    @Override
    @SuppressWarnings("unchecked")
    public Instance create(CreateRequest req) {
        if (!req.name().startsWith(LABEL_PREFIX)) {
            throw new IllegalArgumentException("instance label must carry the studio prefix so the watchdog can see it");
        }
        String[] g = req.quote().gpu().split("\\|");
        if (g.length < 2) {
            throw new IllegalArgumentException("not a Vast quote: " + req.quote().gpu());
        }
        long ask = Long.parseLong(g[1]);
        workerPort = req.port();
        double ceiling = req.quote().hourlyUsd() * cfg.priceSlack();
        List<Long> tried = new ArrayList<>();
        RuntimeException last = null;
        for (int attempt = 0; attempt < cfg.offerAttempts(); attempt++) {
            tried.add(ask);
            String id = null;
            try {
                Map<String, Object> r = call(true, () -> http.put().uri(baseUrl + "/asks/{id}/", tried.getLast())
                        .contentType(MediaType.APPLICATION_JSON).header("Authorization", "Bearer " + accountKey())
                        .body(createBody(req)).retrieve().body(Map.class));
                if (r == null || !(r.get("new_contract") instanceof Number n)) {
                    throw new IllegalStateException("Vast create returned no new_contract: "
                            + (r == null ? "empty reply" : String.valueOf(r.get("msg"))));
                }
                id = String.valueOf(n.longValue());
                firstSeen.put(id, Instant.now());
                String endpoint = waitForEndpoint(id, req.port());
                return new Instance("vast", id, req.name(), req.quote().hourlyUsd(), g[0], endpoint);
            } catch (HttpClientErrorException e) {
                // 404 / 410: the offer was taken or withdrawn between search and create. Nothing was created.
                if (e.getStatusCode().value() != 404 && e.getStatusCode().value() != 410) {
                    throw e;
                }
                last = e;
            } catch (StartFailed e) {
                if (!e.destroyed) {
                    throw e; // never rent a second machine while this one could not be destroyed
                }
                last = e; // it never came up and was destroyed: try the next offer
            }
            Quote next = nextOffer(tried, ceiling);
            if (next == null) {
                break;
            }
            ask = Long.parseLong(next.gpu().split("\\|")[1]);
            log.warn("Vast: offer {} failed ({}); trying offer {}", tried.getLast(), last.getMessage(), ask);
        }
        throw last == null ? new IllegalStateException("Vast create failed") : last;
    }

    private Quote nextOffer(List<Long> tried, double ceiling) {
        GpuSpec spec = lastSpec;
        if (spec == null) {
            return null;
        }
        try {
            cachedAt = 0;
            return search(spec).stream().filter(q -> q.hourlyUsd() <= ceiling)
                    .filter(q -> !tried.contains(Long.parseLong(q.gpu().split("\\|")[1]))).findFirst().orElse(null);
        } catch (RuntimeException e) {
            return null;
        }
    }

    /** Thrown when an instance was created but did not come up; it has been destroyed (or destroying it failed and is in the message). */
    static class StartFailed extends IllegalStateException {
        final boolean destroyed;

        StartFailed(String m, boolean destroyed) {
            super(m);
            this.destroyed = destroyed;
        }
    }

    /** Polls the new instance until it runs with its port mapped; destroys it when it exits or takes too long. */
    private String waitForEndpoint(String id, int port) {
        long deadline = System.currentTimeMillis() + cfg.startTimeoutSeconds() * 1000L;
        String state = "no status yet";
        while (true) {
            Map<String, Object> inst = null;
            try {
                inst = fetch(id);
            } catch (RuntimeException e) {
                state = "status unreadable: " + e.getMessage();
            }
            if (inst != null) {
                state = String.valueOf(inst.get("actual_status"));
                String ep = endpointOf(inst, port);
                if ("running".equals(state) && ep != null) {
                    return ep;
                }
                if (isDead(state)) {
                    throw failedStart(id, "instance state '" + state + "' (it will never run)");
                }
            }
            if (System.currentTimeMillis() >= deadline) {
                throw failedStart(id, "not running with its port mapped after " + cfg.startTimeoutSeconds() + " s (last state: " + state + ")");
            }
            try {
                Thread.sleep(pollMillis);
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
                failedStart(id, "interrupted while starting");
                throw new IllegalStateException("interrupted while waiting for the Vast instance");
            }
        }
    }

    private StartFailed failedStart(String id, String why) {
        try {
            terminate(id);
            return new StartFailed("Vast instance " + id + " destroyed: " + why, true);
        } catch (RuntimeException e) {
            // The pod list adoption in GpuBroker and the watchdog (label prefix) cover an instance we could not destroy now.
            return new StartFailed("Vast instance " + id + " " + why + " and destroying it failed (" + e.getMessage() + "); the watchdog will retry", false);
        }
    }

    private static boolean isDead(String status) {
        return "exited".equals(status) || "offline".equals(status) || "unknown".equals(status);
    }

    /** The create body. No SSH, no key, no provider secret: env carries only what the worker needs. Also what the tests check. */
    Map<String, Object> createBody(CreateRequest req) {
        StringBuilder env = new StringBuilder();
        if (req.env() != null) {
            for (Map.Entry<String, String> e : req.env().entrySet()) {
                if (!SAFE_ENV.matcher(e.getKey()).matches() || e.getKey().isEmpty() || !SAFE_ENV.matcher(e.getValue()).matches()) {
                    throw new IllegalArgumentException("env " + e.getKey() + " has characters that cannot go into Vast's env string");
                }
                env.append("-e ").append(e.getKey()).append('=').append(e.getValue()).append(' ');
            }
        }
        env.append("-p ").append(req.port()).append(':').append(req.port());
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("image", req.image());
        body.put("label", req.name());
        body.put("disk", Math.max(req.diskGb(), cfg.diskGb()));
        body.put("runtype", "args");                 // entrypoint mode: no SSH, no Jupyter, nothing else listens
        body.put("args", req.bootArgv());
        body.put("env", env.toString());
        body.put("cancel_unavail", true);            // never queue behind a taken offer
        return body;
    }

    // ------------------------------------------------------------------------------------------------ status / list / destroy

    @Override
    public InstanceStatus status(String instanceId) {
        Map<String, Object> inst;
        try {
            inst = fetch(instanceId);
        } catch (HttpClientErrorException.NotFound e) {
            return new InstanceStatus(State.GONE, null, "404");
        }
        if (inst == null || inst.isEmpty()) {
            return new InstanceStatus(State.GONE, null, "not found");
        }
        String st = String.valueOf(inst.get("actual_status"));
        if ("running".equals(st)) {
            return new InstanceStatus(State.RUNNING, endpointOf(inst, workerPort), st);
        }
        if (isDead(st)) {
            return new InstanceStatus(State.GONE, null, st); // "exited / unknown / offline never reach running": destroy and replace
        }
        Instant born = born(instanceId, inst);
        if (born != null && Instant.now().isAfter(born.plusSeconds(cfg.startTimeoutSeconds() * 3L))) {
            return new InstanceStatus(State.GONE, null, "never became running (" + st + ")");
        }
        return new InstanceStatus(State.STARTING, null, st);
    }

    @Override
    public String endpointFor(String instanceId, int port) {
        try {
            Map<String, Object> inst = fetch(instanceId);
            return inst == null ? null : endpointOf(inst, port);
        } catch (RuntimeException e) { // a broker restart must not fail because Vast is slow
            log.warn("Vast: could not read the address of instance {}: {}", instanceId, e.getMessage());
            return null;
        }
    }

    /** All instances of the account (the watchdog filters by the studio label). {@code GET /api/v1/instances/}, keyset-paged, 2FA session key. */
    @Override
    @SuppressWarnings("unchecked")
    public List<Pod> listPods() {
        List<Pod> out = new ArrayList<>();
        String after = null;
        for (int page = 0; page < 40; page++) { // 25 per page: 1000 instances is far beyond any sane account
            String token = after;
            Map<String, Object> r = call(true, () -> http.get().uri(v1Url + "/instances/?limit=25" + (token == null ? "" : "&after_token={t}"),
                    token == null ? Map.<String, Object>of() : Map.<String, Object>of("t", token))
                    .header("Authorization", "Bearer " + accountKey()).retrieve().body(Map.class));
            if (r == null || !(r.get("instances") instanceof List<?> l)) {
                throw new IllegalStateException("Vast list instances returned no instances array");
            }
            for (Object o : l) {
                if (o instanceof Map<?, ?> m) {
                    Map<String, Object> inst = (Map<String, Object>) m;
                    String id = String.valueOf((long) num(inst.get("id")));
                    Instant born = born(id, inst);
                    out.add(new Pod(id, str(inst.get("label")), str(inst.get("actual_status")).isEmpty() ? "LOADING"
                            : str(inst.get("actual_status")).toUpperCase(), num(inst.get("dph_total")), born, born));
                }
            }
            after = r.get("next_token") == null ? null : r.get("next_token").toString();
            if (after == null || after.isBlank() || l.isEmpty()) {
                return out;
            }
        }
        throw new IllegalStateException("Vast list instances did not end after 40 pages");
    }

    @Override
    public void terminate(String podId) {
        try {
            Map<String, Object> r = call(true, () -> http.delete().uri(baseUrl + "/instances/{id}/", podId)
                    .header("Authorization", "Bearer " + accountKey()).retrieve().body(Map.class));
            if (r != null && Boolean.FALSE.equals(r.get("success"))) {
                throw new IllegalStateException("Vast destroy " + podId + " refused: " + r.get("msg"));
            }
        } catch (HttpClientErrorException.NotFound e) {
            // already gone: nothing more to destroy
        }
        firstSeen.remove(podId);
    }

    // ------------------------------------------------------------------------------------------------ helpers

    /** One instance by id through the v1 list ({@code select_filters} on id): v1 has no by-id route (404) and the v0 list is gone (410). Null when it does not exist. */
    @SuppressWarnings("unchecked")
    private Map<String, Object> fetch(String id) {
        String filter = "{\"id\":{\"eq\":" + Long.parseLong(id) + "}}";
        Map<String, Object> r = call(true, () -> http.get().uri(v1Url + "/instances/?limit=5&select_filters={f}", Map.of("f", filter))
                .header("Authorization", "Bearer " + accountKey()).retrieve().body(Map.class));
        if (r == null || !(r.get("instances") instanceof List<?> l)) {
            return null;
        }
        for (Object o : l) {
            if (o instanceof Map<?, ?> m && id.equals(String.valueOf((long) num(m.get("id"))))) {
                return (Map<String, Object>) m;
            }
        }
        return null;
    }

    /** {@code http://<public ip>:<mapped port>} from {@code ports["<port>/tcp"][0].HostPort}; null until the mapping exists. */
    @SuppressWarnings("unchecked")
    static String endpointOf(Map<String, Object> inst, int port) {
        String ip = str(inst.get("public_ipaddr")).strip();
        if (ip.isEmpty() || !(inst.get("ports") instanceof Map<?, ?> ports)) {
            return null;
        }
        Object mapped = ((Map<String, Object>) ports).get(port + "/tcp");
        if (mapped instanceof List<?> l && !l.isEmpty() && l.getFirst() instanceof Map<?, ?> m && m.get("HostPort") != null) {
            return "http://" + ip + ":" + m.get("HostPort");
        }
        return null;
    }

    private Instant born(String id, Map<String, Object> inst) {
        if (inst.get("start_date") instanceof Number n && n.doubleValue() > 0) {
            return Instant.ofEpochMilli((long) (n.doubleValue() * 1000));
        }
        return firstSeen.computeIfAbsent(id, k -> Instant.now());
    }

    /**
     * Runs one Vast call. {@code account}: an instance/account call (needs the 2FA session key); a 401/403 there means the session
     * is missing or expired, and the message says how to log in again (it reaches the dashboard card, and the broker rents nothing).
     */
    private <T> T call(boolean account, java.util.function.Supplier<T> c) {
        try {
            return c.get();
        } catch (HttpClientErrorException.Unauthorized | HttpClientErrorException.Forbidden e) {
            if (account) {
                throw new VastSessionExpired(SESSION_HELP + " (HTTP " + e.getStatusCode().value() + ": " + abbreviate(e.getResponseBodyAsString()) + ")");
            }
            throw new IllegalStateException("Vast.ai refused the key (HTTP " + e.getStatusCode().value() + "): "
                    + abbreviate(e.getResponseBodyAsString()) + " (check the key on cloud.vast.ai/manage-keys)");
        }
    }

    /** The account key was refused: the 2FA session is missing or has expired. Never carries a key. */
    public static class VastSessionExpired extends IllegalStateException {
        VastSessionExpired(String m) {
            super(m);
        }
    }

    private static String abbreviate(String s) {
        return s == null ? "" : s.length() > 200 ? s.substring(0, 200) : s;
    }

    private static double num(Object o) {
        return o instanceof Number n ? n.doubleValue() : 0;
    }

    private static String str(Object o) {
        return o == null ? "" : o.toString();
    }
}
