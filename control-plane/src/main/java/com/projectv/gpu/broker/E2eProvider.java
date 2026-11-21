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

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * E2E Networks adapter (MyAccount REST {@code https://api.e2enetworks.com/myaccount/api/v1}, API key in the query string plus the
 * bearer token). E2E rents <b>VMs</b>, not containers, so the worker runs as a Docker container started by a cloud-init start script.
 * Same safety design as {@link VastProvider} (docs/e2e-provider-notes.md):
 * <ul>
 *   <li><b>No secret goes to the node.</b> The start scripts hold the worker env only (boot payload, port, root, the per-rental worker
 *       token), in a root-only file. Never the E2E key or token, nor any other key. The mandatory SSH key is a public key whose private
 *       half is generated in memory and thrown away, and password login is off: nobody can log in.</li>
 *   <li><b>Teardown is ours.</b> {@link #terminate} calls {@code DELETE nodes/{id}/}. Power-off does not stop E2E billing, only delete does.
 *       The in-VM guard only ends the worker ({@link com.projectv.gpu.PodSelfStop#wrapExit}). The watchdog lists this provider's nodes by
 *       the studio name prefix.</li>
 *   <li><b>Quotes</b> come from the live plan list ({@code GET images/}); it has no stock field, so availability is only known when create
 *       succeeds, and the broker tries the next candidate when it fails.</li>
 * </ul>
 * Shapes taken from docs.e2enetworks.com (OpenAPI) on 22 Nov 2026; the plan list, node list, credit and quota calls were checked live and
 * read-only. NOT checked live: create, show, delete (no node was created yet).
 */
@Component
public class E2eProvider implements GpuProvider {

    private static final Logger log = LoggerFactory.getLogger(E2eProvider.class);
    static final String LABEL_PREFIX = "projectv-";
    private static final Pattern SAFE_ENV = Pattern.compile("[A-Za-z0-9+/=._:@-]*");
    private static final Pattern PRICE = Pattern.compile("([0-9]+(?:\\.[0-9]+)?)\\s*/\\s*hour", Pattern.CASE_INSENSITIVE);
    /** Start scripts are limited to 16 KB each: stay well below. */
    static final int SCRIPT_BYTES = 14_000;

    private final E2eConfig cfg;
    private final String baseUrl;
    private final String apiKey;
    private final String authToken;
    private final String projectId;
    private final RestClient http;
    private final long pollMillis;
    /** An Ed25519 public key whose private half does not exist anywhere (create demands one SSH key). */
    private final String throwawayPublicKey;
    private final Map<String, Instant> firstSeen = new ConcurrentHashMap<>();
    private final Map<String, Double> hourly = new ConcurrentHashMap<>();
    private volatile String lastWhy = "no plan passes the filter";
    private volatile long cachedAt;
    private volatile List<Quote> cached = List.of();

    @Autowired
    public E2eProvider(E2eConfig cfg, @Value("${projectv.e2e.base-url:https://api.e2enetworks.com/myaccount/api/v1}") String baseUrl,
                       @Value("${projectv.e2e.api-key:}") String apiKey, @Value("${projectv.e2e.auth-token:}") String authToken,
                       @Value("${projectv.e2e.project-id:}") String projectId) {
        this(cfg, baseUrl, apiKey, authToken, projectId, client(), cfg.pollSeconds() * 1000L);
    }

    E2eProvider(E2eConfig cfg, String baseUrl, String apiKey, String authToken, String projectId, RestClient http, long pollMillis) {
        this.cfg = cfg;
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.apiKey = apiKey == null ? "" : apiKey.strip();
        this.authToken = authToken == null ? "" : authToken.strip();
        this.projectId = projectId == null ? "" : projectId.strip();
        this.http = http;
        this.pollMillis = pollMillis;
        this.throwawayPublicKey = throwawayKey();
    }

    private static RestClient client() {
        SimpleClientHttpRequestFactory f = new SimpleClientHttpRequestFactory();
        f.setConnectTimeout(10_000);
        f.setReadTimeout(60_000);
        return RestClient.builder().requestFactory(f).defaultHeader("User-Agent", "projectv-studio/1.0").build();
    }

    @Override
    public String provider() {
        return "e2e";
    }

    @Override
    public boolean configured() {
        return !apiKey.isEmpty() && !authToken.isEmpty();
    }

    /** Off until the owner has seen a real node run (no node has been created yet). */
    @Override
    public boolean enabledByDefault() {
        return false;
    }

    /** No account key on the node: the guard only ends the worker; the control plane deletes the node. */
    @Override
    public List<String> guard(List<String> jobArgv, int maxRuntimeMinutes, int idleMinutes, Instant createdAt) {
        return com.projectv.gpu.PodSelfStop.wrapExit(jobArgv, maxRuntimeMinutes, idleMinutes, createdAt);
    }

    /** Local checks on the real create body (start-script sizes, env characters) plus the read-only plan list (plan/image pairing). */
    @Override
    public Preflight preflight(GpuSpec spec, CreateRequest probe) {
        if (!configured()) {
            return new Preflight(false, List.of("not configured (E2E_API_KEY / E2E_AUTH_TOKEN missing)"));
        }
        List<String> checks = new ArrayList<>();
        boolean ok = true;
        try {
            List<String> scripts = startScripts(probe);
            int longest = scripts.stream().mapToInt(String::length).max().orElse(0);
            checks.add("start scripts: " + scripts.size() + " piece(s), longest " + longest + " chars (limit used here " + SCRIPT_BYTES + ")");
            if (longest > SCRIPT_BYTES) {
                ok = false;
                checks.add("FAIL: a start script is longer than " + SCRIPT_BYTES);
            }
        } catch (RuntimeException e) {
            ok = false;
            checks.add("FAIL: start script cannot be built: " + e.getMessage());
        }
        List<Quote> plans = quotes(spec);
        if (plans.isEmpty()) {
            ok = false;
            checks.add("FAIL: no allowed plan with image '" + cfg.image() + "' in the live plan list: " + lastWhy);
        } else {
            checks.add("plan/image pairing: " + plans.size() + " plan(s) offered with image '" + cfg.image() + "', cheapest $"
                    + "%.2f/h".formatted(plans.getFirst().hourlyUsd()));
        }
        return new Preflight(ok, checks);
    }

    // ------------------------------------------------------------------------------------------------ quote

    @Override
    public Quote quote(GpuSpec spec) {
        List<Quote> all = quotes(spec);
        return all.isEmpty() ? Quote.unavailable("e2e", lastWhy) : all.getFirst();
    }

    @Override
    public List<Quote> quotes(GpuSpec spec) {
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
        Map<String, Object> r = call(() -> http.get().uri(url("images/", Map.of("os", "Ubuntu", "category", "GPU")))
                .header("Authorization", "Bearer " + authToken).retrieve().body(Map.class));
        Map<String, Quote> best = new LinkedHashMap<>();
        Map<String, String> why = new LinkedHashMap<>();
        if (r != null && r.get("data") instanceof List<?> rows) {
            for (Object o : rows) {
                if (!(o instanceof Map<?, ?> m)) {
                    continue;
                }
                Map<String, Object> row = (Map<String, Object>) m;
                E2eConfig.Plan plan = cfg.plans().stream().filter(p -> p.sku().equals(str(row.get("name")))).findFirst().orElse(null);
                if (plan == null || !cfg.image().equals(str(row.get("image")))) {
                    continue;
                }
                Map<String, Object> s = row.get("specs") instanceof Map<?, ?> sm ? (Map<String, Object>) sm : Map.of();
                String reject = problem(plan, s, spec);
                if (reject != null) {
                    why.put(plan.gpu() + ": " + reject, "");
                    continue;
                }
                double inr = num(s.get("price_per_hour"));
                double usd = inr * (1 + cfg.gstPercent() / 100.0) / cfg.inrPerUsd();
                String planName = str(row.get("plan"));
                String detail = "%s, %.0f vCPU, %.0f GB RAM, %.0f GB disk, Rs %.0f/h + %.0f%% GST = $%.2f/h (Rs %.0f per $); stock unknown until create"
                        .formatted(plan.sku(), num(s.get("cpu")), num(s.get("ram")), num(s.get("disk_space")), inr, cfg.gstPercent(), usd, cfg.inrPerUsd());
                Quote q = new Quote("e2e", true, "ON-DEMAND", plan.gpu() + "|" + plan.sku() + "|" + planName, usd, detail);
                // several OS templates per card: prefer the CUDA 12 one
                best.merge(plan.sku(), q, (a, b) -> a.gpu().contains("Cuda12") || !b.gpu().contains("Cuda12") ? a : b);
            }
        }
        List<Quote> out = new ArrayList<>(best.values());
        out.sort(Comparator.comparingDouble(Quote::hourlyUsd));
        lastWhy = !out.isEmpty() ? "" : why.isEmpty() ? "no allowed GPU plan in the E2E plan list" : "every plan was rejected: " + why.keySet();
        return out;
    }

    private String problem(E2eConfig.Plan plan, Map<String, Object> s, GpuSpec spec) {
        if (plan.vramGb() < spec.minVramGb()) {
            return "too little VRAM";
        }
        if (num(s.get("ram")) < spec.minRamGb()) {
            return "too little RAM";
        }
        if (num(s.get("disk_space")) < spec.diskGb()) {
            return "not enough disk";
        }
        if (num(s.get("price_per_hour")) <= 0) {
            return "no live price";
        }
        return null;
    }

    // ------------------------------------------------------------------------------------------------ create

    @Override
    @SuppressWarnings("unchecked")
    public Instance create(CreateRequest req) {
        if (!req.name().startsWith(LABEL_PREFIX)) {
            throw new IllegalArgumentException("node name must carry the studio prefix so the watchdog can see it");
        }
        String[] g = req.quote().gpu().split("\\|");
        if (g.length < 3) {
            throw new IllegalArgumentException("not an E2E quote: " + req.quote().gpu());
        }
        Map<String, Object> r = call(() -> http.post().uri(url("nodes/", Map.of())).contentType(MediaType.APPLICATION_JSON)
                .header("Authorization", "Bearer " + authToken).body(createBody(req, g[2])).retrieve().body(Map.class));
        Map<String, Object> data = r != null && r.get("data") instanceof Map<?, ?> m ? (Map<String, Object>) m : null;
        if (data == null && r != null && r.get("data") instanceof List<?> l && !l.isEmpty() && l.getFirst() instanceof Map<?, ?> m) {
            data = (Map<String, Object>) m; // we ask for exactly one node, but tolerate a one-element list
        }
        if (data == null || !(data.get("id") instanceof Number n)) {
            throw new IllegalStateException("E2E create returned no node id: " + (r == null ? "empty reply" : abbreviate(String.valueOf(r.get("message")))));
        }
        String id = String.valueOf(n.longValue());
        firstSeen.put(id, Instant.now());
        hourly.put(id, req.quote().hourlyUsd());
        String endpoint = waitForEndpoint(id);
        return new Instance("e2e", id, req.name(), req.quote().hourlyUsd(), g[0], endpoint);
    }

    /** The create body. No account key, no password, no usable SSH key. Also what the tests check. */
    Map<String, Object> createBody(CreateRequest req, String plan) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("name", req.name());
        body.put("label", "default");
        body.put("plan", plan);
        body.put("image", cfg.image());
        body.put("ssh_keys", List.of(throwawayPublicKey));
        body.put("number_of_instances", 1);
        body.put("default_public_ip", true);
        body.put("disable_password", true);
        body.put("start_scripts", startScripts(req));
        return body;
    }

    /**
     * Cloud-init scripts: the worker env file (split in pieces under the 16 KB limit), then {@code docker run}. The env file is
     * root-only. Values are checked against a safe character set, so nothing can break out of the here-documents.
     */
    List<String> startScripts(CreateRequest req) {
        List<String> lines = new ArrayList<>();
        if (req.env() != null) {
            for (Map.Entry<String, String> e : req.env().entrySet()) {
                if (e.getKey().isEmpty() || !SAFE_ENV.matcher(e.getKey()).matches() || !SAFE_ENV.matcher(e.getValue()).matches()) {
                    throw new IllegalArgumentException("env " + e.getKey() + " has characters that cannot go into the E2E start script");
                }
                lines.add(e.getKey() + "=" + e.getValue());
            }
        }
        List<String> scripts = new ArrayList<>();
        StringBuilder cur = new StringBuilder();
        for (String line : lines) {
            if (cur.length() + line.length() + 1 > SCRIPT_BYTES) {
                scripts.add(envScript(cur));
                cur = new StringBuilder();
            }
            cur.append(line).append('\n');
        }
        if (cur.length() > 0) {
            scripts.add(envScript(cur));
        }
        StringBuilder run = new StringBuilder("#!/bin/bash\n")
                .append("# wait for the Docker daemon (first boot)\n")
                .append("for i in $(seq 1 90); do docker info >/dev/null 2>&1 && break; sleep 2; done\n")
                .append("docker run -d --name projectv-worker --gpus all --shm-size=8g --env-file /opt/projectv/worker.env")
                .append(" -p ").append(cfg.publicPort()).append(':').append(req.port()).append(' ').append(shellQuote(req.image()));
        for (String a : req.bootArgv()) {
            run.append(' ').append(shellQuote(a));
        }
        run.append('\n');
        scripts.add(run.toString());
        return scripts;
    }

    private static String envScript(StringBuilder lines) {
        return "#!/bin/bash\numask 077\nmkdir -p /opt/projectv\ncat >> /opt/projectv/worker.env <<'PV_ENV_EOF'\n" + lines + "PV_ENV_EOF\n";
    }

    static String shellQuote(String s) {
        return "'" + s.replace("'", "'\\''") + "'";
    }

    /** Polls the new node until it runs with a public IP; deletes it when it dies or takes too long. */
    private String waitForEndpoint(String id) {
        long deadline = System.currentTimeMillis() + cfg.startTimeoutSeconds() * 1000L;
        String state = "no status yet";
        while (true) {
            Map<String, Object> node = null;
            try {
                node = fetch(id);
            } catch (RuntimeException e) {
                state = "status unreadable: " + e.getMessage();
            }
            if (node != null) {
                state = str(node.get("status"));
                String ep = endpointOf(node);
                if (isRunning(state) && ep != null) {
                    return ep;
                }
                if (isDead(state)) {
                    throw failedStart(id, "node state '" + state + "' (it will never run)");
                }
            }
            if (System.currentTimeMillis() >= deadline) {
                throw failedStart(id, "not running with a public IP after " + cfg.startTimeoutSeconds() + " s (last state: " + state + ")");
            }
            try {
                Thread.sleep(pollMillis);
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
                failedStart(id, "interrupted while starting");
                throw new IllegalStateException("interrupted while waiting for the E2E node");
            }
        }
    }

    private RuntimeException failedStart(String id, String why) {
        try {
            terminate(id);
            return new IllegalStateException("E2E node " + id + " deleted: " + why);
        } catch (RuntimeException e) {
            // E2E refuses delete while a node is still being created; the watchdog (name prefix) retries.
            return new IllegalStateException("E2E node " + id + " " + why + " and deleting it failed (" + e.getMessage() + "); the watchdog will retry");
        }
    }

    private static boolean isRunning(String status) {
        return "running".equalsIgnoreCase(status);
    }

    private static boolean isDead(String status) {
        String s = status.toLowerCase();
        return s.contains("error") || s.contains("fail") || s.contains("delet") || s.contains("terminat") || s.contains("power")
                || s.contains("stopp") || s.equals("off");
    }

    // ------------------------------------------------------------------------------------------------ status / list / delete

    @Override
    public InstanceStatus status(String instanceId) {
        Map<String, Object> node;
        try {
            node = fetch(instanceId);
        } catch (HttpClientErrorException.NotFound e) {
            return new InstanceStatus(State.GONE, null, "404");
        }
        if (node == null || node.isEmpty()) {
            return new InstanceStatus(State.GONE, null, "not found");
        }
        String st = str(node.get("status"));
        if (isRunning(st)) {
            String ep = endpointOf(node);
            return ep == null ? new InstanceStatus(State.STARTING, null, "running, no public IP yet") : new InstanceStatus(State.RUNNING, ep, st);
        }
        if (isDead(st)) {
            return new InstanceStatus(State.GONE, null, st); // a powered-off node still bills: the broker deletes it
        }
        Instant born = firstSeen.computeIfAbsent(instanceId, k -> Instant.now());
        if (Instant.now().isAfter(born.plusSeconds(cfg.startTimeoutSeconds() * 3L))) {
            return new InstanceStatus(State.GONE, null, "never became running (" + st + ")");
        }
        return new InstanceStatus(State.STARTING, null, st);
    }

    @Override
    public String endpointFor(String instanceId, int port) {
        try {
            Map<String, Object> node = fetch(instanceId);
            return node == null ? null : endpointOf(node);
        } catch (RuntimeException e) {
            log.warn("E2E: could not read the address of node {}: {}", instanceId, e.getMessage());
            return null;
        }
    }

    /** All nodes of the account (the watchdog filters by the studio name prefix). */
    @Override
    @SuppressWarnings("unchecked")
    public List<Pod> listPods() {
        Map<String, Object> r = call(() -> http.get().uri(url("nodes/", Map.of())).header("Authorization", "Bearer " + authToken)
                .retrieve().body(Map.class));
        if (r == null || !(r.get("data") instanceof List<?> l)) {
            throw new IllegalStateException("E2E list nodes returned no data array");
        }
        List<Pod> out = new ArrayList<>();
        for (Object o : l) {
            if (o instanceof Map<?, ?> m) {
                Map<String, Object> node = (Map<String, Object>) m;
                String id = String.valueOf((long) num(node.get("id")));
                Instant born = born(id, node);
                out.add(new Pod(id, str(node.get("name")), str(node.get("status")).isEmpty() ? "LOADING" : str(node.get("status")).toUpperCase(),
                        hourlyOf(id, node), born, born));
            }
        }
        return out;
    }

    @Override
    public void terminate(String podId) {
        try {
            Map<String, Object> r = call(() -> http.delete().uri(url("nodes/" + Long.parseLong(podId) + "/", Map.of()))
                    .header("Authorization", "Bearer " + authToken).retrieve().body(Map.class));
            if (r != null && r.get("code") instanceof Number c && c.intValue() >= 400) {
                throw new IllegalStateException("E2E delete " + podId + " refused: " + abbreviate(String.valueOf(r.get("message"))));
            }
        } catch (HttpClientErrorException.NotFound e) {
            // already gone
        }
        firstSeen.remove(podId);
        hourly.remove(podId);
    }

    // ------------------------------------------------------------------------------------------------ helpers

    @SuppressWarnings("unchecked")
    private Map<String, Object> fetch(String id) {
        Map<String, Object> r = call(() -> http.get().uri(url("nodes/" + Long.parseLong(id) + "/", Map.of()))
                .header("Authorization", "Bearer " + authToken).retrieve().body(Map.class));
        return r != null && r.get("data") instanceof Map<?, ?> m ? (Map<String, Object>) m : null;
    }

    private String endpointOf(Map<String, Object> node) {
        String ip = str(node.get("public_ip_address")).strip();
        return ip.isEmpty() || "[]".equals(ip) || "null".equals(ip) ? null : "http://" + ip + ":" + cfg.publicPort();
    }

    private Instant born(String id, Map<String, Object> node) {
        String c = str(node.get("created_at"));
        if (!c.isEmpty()) {
            try {
                return Instant.parse(c);
            } catch (RuntimeException ignored) {
                // fall through to first-seen
            }
        }
        return firstSeen.computeIfAbsent(id, k -> Instant.now());
    }

    /** Our own quote when we created it; else "Rs. 102.0/Hour" from the node (plus GST, in dollars); else 0 (the watchdog does not need it). */
    private double hourlyOf(String id, Map<String, Object> node) {
        Double known = hourly.get(id);
        if (known != null) {
            return known;
        }
        Matcher m = PRICE.matcher(str(node.get("price")));
        return m.find() ? Double.parseDouble(m.group(1)) * (1 + cfg.gstPercent() / 100.0) / cfg.inrPerUsd() : 0;
    }

    /** The URL with apikey, location and project; built only here. It carries the key, so it is never logged or put in a message. */
    private URI url(String path, Map<String, String> extra) {
        StringBuilder q = new StringBuilder("apikey=").append(enc(apiKey)).append("&location=").append(enc(cfg.location()));
        if (!projectId.isEmpty()) {
            q.append("&project_id=").append(enc(projectId));
        }
        extra.forEach((k, v) -> q.append('&').append(k).append('=').append(enc(v)));
        return URI.create(baseUrl + "/" + path + "?" + q);
    }

    private static String enc(String s) {
        return URLEncoder.encode(s, StandardCharsets.UTF_8);
    }

    /** Runs one E2E call; error text is scrubbed of the key and token (a connection error message contains the full URL). */
    private <T> T call(java.util.function.Supplier<T> c) {
        try {
            return c.get();
        } catch (HttpClientErrorException.NotFound e) {
            throw e;
        } catch (HttpClientErrorException e) {
            int code = e.getStatusCode().value();
            throw new IllegalStateException("E2E refused the call (HTTP " + code + "): " + scrub(abbreviate(e.getResponseBodyAsString()))
                    + (code == 401 || code == 403 ? " (check E2E_API_KEY / E2E_AUTH_TOKEN in the MyAccount API settings)" : ""));
        } catch (RuntimeException e) {
            throw new IllegalStateException("E2E call failed: " + e.getClass().getSimpleName() + ": " + scrub(String.valueOf(e.getMessage())));
        }
    }

    private String scrub(String s) {
        String out = s;
        for (String secret : new String[] {apiKey, authToken}) {
            if (!secret.isEmpty()) {
                out = out.replace(secret, "***").replace(enc(secret), "***");
            }
        }
        return out;
    }

    /** OpenSSH public key from a fresh Ed25519 pair; the private key is not kept. */
    static String throwawayKey() {
        try {
            KeyPair kp = KeyPairGenerator.getInstance("Ed25519").generateKeyPair();
            byte[] x509 = kp.getPublic().getEncoded();
            byte[] raw = java.util.Arrays.copyOfRange(x509, x509.length - 32, x509.length);
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            DataOutputStream out = new DataOutputStream(bos);
            byte[] type = "ssh-ed25519".getBytes(StandardCharsets.US_ASCII);
            out.writeInt(type.length);
            out.write(type);
            out.writeInt(raw.length);
            out.write(raw);
            return "ssh-ed25519 " + Base64.getEncoder().encodeToString(bos.toByteArray()) + " projectv-no-login";
        } catch (java.security.GeneralSecurityException | IOException e) {
            throw new IllegalStateException("cannot generate the placeholder SSH key", e);
        }
    }

    private static String abbreviate(String s) {
        return s == null ? "" : s.length() > 200 ? s.substring(0, 200) : s;
    }

    private static double num(Object o) {
        return o instanceof Number n ? n.doubleValue() : o instanceof String s && s.matches("[0-9.]+") ? Double.parseDouble(s) : 0;
    }

    private static String str(Object o) {
        return o == null ? "" : o.toString();
    }
}
